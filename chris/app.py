"""Composition root: wires all subsystems together.

Nothing in this module holds business logic; it only constructs the runtime
graph and provides a minimal prompt renderer derived from structured state.
"""

from __future__ import annotations

from dataclasses import dataclass

from chris.behavior.engine import BehaviorEngine, RuleBasedBehaviorEngine
from chris.config import Config
from chris.embedding.cache import EmbeddingCache
from chris.embedding.factory import create_embedding_provider_from_config
from chris.events import EventBus, EventType
from chris.llm.base import LLMProvider
from chris.llm.factory import create_provider
from chris.memory.base import MemoryQuery, MemorySystem
from chris.memory.ecology import (
    MemoryConfig,
    MemoryEcology,
    MemoryOperation,
    MemoryOperationProposal,
)
from chris.memory.maintenance import MemoryMaintenanceScheduler
from chris.personality.models import Personality
from chris.personality.store import VersionedPersonalityStore
from chris.retrieval.gateway import RetrievalGateway
from chris.retrieval.semantic import SemanticRetriever
from chris.temporal.clock import create_clock
from chris.temporal.system import TemporalSystem
from chris.tools.builtin import EchoTool, GetCurrentTimeTool
from chris.tools.confirmation.manager import ConfirmationManager
from chris.tools.executor import ToolExecutor
from chris.tools.permission import DefaultPermissionPolicy
from chris.tools.registry import ToolRegistry
from chris.tools.search.factory import create_search_provider
from chris.tools.web_search import WebSearchTool
from chris.voice.manager import VoiceManager
from chris.world.awareness import WorldAwareness
from chris.world.sensors import create_sensor
from chris.world.state import WorldState, load_world_state, save_world_state


@dataclass
class Runtime:
    config: Config
    bus: EventBus
    provider: LLMProvider
    personality: Personality
    personality_store: VersionedPersonalityStore
    memory: MemorySystem
    temporal: TemporalSystem
    memory_maintenance: MemoryMaintenanceScheduler
    world: WorldState
    world_awareness: WorldAwareness
    behavior: BehaviorEngine
    retrieval: RetrievalGateway | None = None
    tool_registry: ToolRegistry | None = None
    tool_executor: ToolExecutor | None = None
    confirmation: ConfirmationManager | None = None
    voice: VoiceManager | None = None


REMEMBER_MARKERS = ("帮我记住", "请记住", "记住了", "别忘了", "不要忘记", "记一下", "记住")


def _is_question(text: str) -> bool:
    if text.endswith(("？", "?", "吗")):
        return True
    for phrase in ("你还记得", "还记得", "记得吗", "记得什么", "记不记得", "记性"):
        if phrase in text:
            return True
    return False


def has_remember_intent(text: str) -> bool:
    """Cheap keyword pre-check: is the user possibly asking Chris to remember?"""
    return any(marker in text for marker in REMEMBER_MARKERS)


_REMEMBER_EXTRACT_SYSTEM = (
    "You decide whether the user is telling Chris to remember something new. "
    "If the user is explicitly asking to remember / memorize / not forget some fact "
    "(e.g. \"记住我喜欢喝咖啡\", \"don't forget my birthday is May 18\"), reply with ONLY "
    "the fact(s) to remember, one per line, in the same language as the user, with no labels. "
    "If the message is a question about past memories, a greeting, or not a remember "
    "instruction, reply exactly: NO_MEMORY"
)


def extract_remember_with_llm(provider, text: str) -> list[str]:
    """Use the LLM to understand WHAT the user wants remembered.

    This replaces the fragile keyword-stripping heuristic: the LLM judges intent,
    so questions about past memories yield no new memory, while directives like
    "记住我对象叫小明" yield a clean fact. Storage still goes through MemoryEcology.
    """
    if not has_remember_intent(text):
        return []
    try:
        reply = provider.generate(
            [
                {"role": "system", "content": _REMEMBER_EXTRACT_SYSTEM},
                {"role": "user", "content": text},
            ],
            max_tokens=256,
        )
    except Exception:  # noqa: BLE001
        return []
    facts: list[str] = []
    for line in reply.splitlines():
        line = line.strip().strip("-\"'*•")
        if not line:
            continue
        if line.upper() == "NO_MEMORY":
            return []
        facts.append(line)
    return facts


def extract_remember(text: str) -> str | None:
    """Heuristic fallback (no LLM). Only used when the LLM extraction is unavailable."""
    stripped = text.strip()
    if not stripped or _is_question(stripped):
        return None
    content = stripped
    found = False
    for marker in REMEMBER_MARKERS:
        if marker in content:
            found = True
            content = content.replace(marker, "")
    if not found:
        return None
    content = content.strip(" ，。,.!！？?～~ \t\n了")
    if not content or content == stripped:
        return None
    return content


@dataclass
class RecallIntentDecision:
    """What Chris understood about the user's message w.r.t. past memories.

    confirm_memory_ids: memories the user EXPLICITLY re-confirmed (to REINFORCE).
    want_old_memory: user is asking about old/past (possibly archived) memories,
        so retrieval should include the archive.
    """

    confirm_memory_ids: list[str]
    want_old_memory: bool
    source: str = "dialogue"


_RECALL_JUDGE_SYSTEM = (
    "You are the memory-understanding part of Chris. Given the user's latest message and a list of "
    "Chris's past memories, decide two things:\n"
    "1) confirm_memory_ids: the memory_id(s) the user is EXPLICITLY re-confirming / re-affirming right now "
    "(e.g. '对，我就是喜欢冰美式', '是的，就是那个', '确实，我还是想学画画'). Merely mentioning or asking about a "
    "memory is NOT confirmation.\n"
    "2) want_old_memory: true if the user is asking about something from their past / long ago that Chris "
    "may have archived (e.g. '你知不知道我之前那个AI项目', '你还记得我说过什么吗', '之前那个事怎么样了', "
    "'好久以前你告诉过我的那个').\n"
    "Reply with ONLY one JSON object and nothing else: "
    '{"confirm_memory_ids": ["id1", ...], "want_old_memory": true|false}'
)


def _parse_recall_decision(reply: str) -> dict:
    start = reply.find("{")
    end = reply.rfind("}")
    if start < 0 or end <= start:
        return {}
    import json

    try:
        data = json.loads(reply[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def judge_recall_intent(provider, user_text: str, candidates) -> RecallIntentDecision:
    """Ask the LLM to understand the user's intent about past memories.

    LLM-first intent understanding (no keyword matching), so paraphrases like
    '你知不知道...' trigger the same behaviour as '你还记得...'. The LLM only
    PROPOSES memory ids; the MemoryPolicy layer still gates the actual mutation.
    Any provider failure degrades to a safe no-op decision.
    """
    lines = [f"user message: {user_text}", "past memories:"]
    for entry in candidates:
        tier = getattr(entry, "storage_tier", "")
        lines.append(f"- id:{entry.memory_id} [{tier}] {entry.content[:120]}")
    if not candidates:
        lines.append("(none matched)")
    try:
        reply = provider.generate(
            [
                {"role": "system", "content": _RECALL_JUDGE_SYSTEM},
                {"role": "user", "content": "\n".join(lines)},
            ],
            max_tokens=220,
        )
    except Exception:  # noqa: BLE001
        return RecallIntentDecision(confirm_memory_ids=[], want_old_memory=False)
    data = _parse_recall_decision(reply)
    raw_ids = data.get("confirm_memory_ids") or []
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    ids = [str(x) for x in raw_ids if isinstance(x, (str, int))]
    return RecallIntentDecision(
        confirm_memory_ids=ids, want_old_memory=bool(data.get("want_old_memory", False))
    )


def process_recall_intent(
    runtime,
    user_text: str,
    exclude_memory_ids=frozenset(),
) -> RecallIntentDecision:
    """Run recall-intent understanding inside the dialogue flow (7.4).

    1. recall candidate past memories (active + archived) for the current message;
    2. ask the LLM to judge intent;
    3. for explicitly re-confirmed memories, PROPOSE REINFORCE (through MemoryPolicy);
    4. remember whether the user is asking about old/archived memories so the prompt
       retrieval can include the archive.
    Access is still not reinforcement: plain recall only counts access; only an
    explicit confirmation (judged by the LLM) raises strength.
    """
    decision = RecallIntentDecision(confirm_memory_ids=[], want_old_memory=False)
    candidates: list = []
    try:
        results = runtime.memory.query(MemoryQuery(text=user_text, limit=8, include_archived=True))
        candidates = [
            r.memory for r in results if r.memory.memory_id not in exclude_memory_ids
        ]
    except Exception:  # noqa: BLE001
        candidates = []
    decision = judge_recall_intent(runtime.provider, user_text, candidates)
    for memory_id in decision.confirm_memory_ids:
        if runtime.memory.get(memory_id) is None:
            continue
        try:
            runtime.memory.propose(
                MemoryOperationProposal(
                    operation=MemoryOperation.REINFORCE.value,
                    memory_id=memory_id,
                    source="dialogue",
                    reason="user explicitly confirmed a past memory",
                    confidence=0.9,
                )
            )
        except Exception:  # noqa: BLE001
            continue
    try:
        runtime._recall_include_archived = decision.want_old_memory
    except Exception:  # noqa: BLE001
        pass
    return decision


def build_runtime(config: Config | None = None, **overrides: object) -> Runtime:
    cfg = config or Config.load()
    bus = EventBus()
    provider = overrides.get("provider") or create_provider(cfg.llm)
    personality_store = VersionedPersonalityStore(cfg.resolve(cfg.personality_file))
    personality = personality_store.current()
    temporal = TemporalSystem(
        cfg.resolve(cfg.temporal_file),
        event_bus=bus,
        threshold_hours=cfg.time_threshold_hours,
        clock=create_clock(cfg.clock_mode, scale=cfg.time_scale),
        timezone=cfg.timezone,
    )
    memory_path = cfg.resolve("data/test/memory.json") if cfg.test_mode else cfg.resolve(cfg.memory_file)
    memory_config = MemoryConfig.from_dict(cfg.memory or {})
    memory = MemoryEcology(
        memory_path,
        event_bus=bus,
        temporal=temporal,
        config=memory_config,
    )
    bus.subscribe(
        lambda _event: memory.process_due(),
        [
            EventType.TIME_PASSED,
            EventType.DAY_CHANGED,
            EventType.WEEK_CHANGED,
            EventType.MONTH_CHANGED,
        ],
    )
    memory_maintenance = MemoryMaintenanceScheduler(memory, bus, temporal)
    bus.subscribe(lambda _event: memory_maintenance.tick(), EventType.TIME_TICK)
    world = load_world_state(cfg.resolve(cfg.world_state_file))
    world._bus = bus
    bus.subscribe(
        lambda _event: save_world_state(world, cfg.resolve(cfg.world_state_file)),
        EventType.WORLD_STATE_CHANGED,
    )
    sensor = (
        create_sensor(cfg.world_sensor)
        if cfg.world_sensor in ("mock", "windows")
        else None
    )
    world_awareness = WorldAwareness(
        state=world,
        sensor=sensor,
        path=cfg.resolve(cfg.world_state_file),
        event_bus=bus,
        temporal=temporal,
    )
    if cfg.world_poll_interval and cfg.world_poll_interval > 0 and sensor is not None:
        world_awareness.start_polling(cfg.world_poll_interval)
    behavior = RuleBasedBehaviorEngine()
    retrieval = _build_retrieval(cfg, memory, memory_config, temporal, memory_path)
    tool_registry, tool_executor, confirmation = _build_tool_system(cfg, bus, temporal)
    voice = _build_voice(cfg, bus, temporal, chat_runner=overrides.get("voice_chat_runner"))
    return Runtime(
        cfg,
        bus,
        provider,
        personality,
        personality_store,
        memory,
        temporal,
        memory_maintenance,
        world,
        world_awareness,
        behavior,
        retrieval,
        tool_registry,
        tool_executor,
        confirmation,
        voice,
    )


def _build_voice(
    cfg: Config,
    bus: EventBus,
    temporal: TemporalSystem,
    chat_runner=None,
):
    """Build the Voice Manager from the `voice` config section (Phase 9.3).

    Providers are built through the voice factory; the chat runner (which routes
    ASR text into the existing conversation pipeline) is injectable so the core
    never imports the launcher layer. Voice is disabled by default (text mode
    unchanged).
    """
    from chris.voice.config import VoiceConfig
    from chris.voice.factory import (
        build_asr,
        build_audio_input,
        build_audio_output,
        build_tts,
    )
    from chris.voice.manager import VoiceManager

    vcfg = VoiceConfig.from_dict(cfg.voice)

    def _persist_voice(updated: VoiceConfig) -> None:
        """Persist the auto-configured voice section back to config.json."""
        cfg.voice = updated.to_dict()
        if getattr(cfg, "config_path", None):
            try:
                cfg.save(cfg.config_path)
            except OSError:
                pass

    manager = VoiceManager(
        config=vcfg,
        audio_input=build_audio_input(vcfg),
        asr=build_asr(vcfg, cwd=str(cfg.resolve("."))),
        tts=build_tts(vcfg),
        audio_output=build_audio_output(vcfg),
        bus=bus,
        temporal=temporal,
        base_dir=str(cfg.resolve(".")),
        persist=_persist_voice,
    )
    if chat_runner is not None:
        manager.set_chat_runner(chat_runner)
    return manager


def _build_tool_system(cfg: Config, bus: EventBus, temporal: TemporalSystem):
    """Construct the Tool Core: registry + executor.

    Tools are registered here once; per-tool enable/disable is read from the
    `tools` config section (additive). The default permission policy allows
    READ_ONLY / LOW risk tools (the built-in test tools).
    """
    tools_cfg = cfg.tools or {}
    registry = ToolRegistry()

    per_tool = tools_cfg.get("tools", {}) if isinstance(tools_cfg.get("tools"), dict) else {}
    for tool in (GetCurrentTimeTool(), EchoTool()):
        overrides = per_tool.get(tool.name, {}) if isinstance(per_tool, dict) else {}
        if isinstance(overrides, dict):
            if "enabled" in overrides:
                tool.enabled = bool(overrides["enabled"])
        registry.register(tool)

    web_cfg = tools_cfg.get("web_search", {}) if isinstance(tools_cfg.get("web_search"), dict) else {}
    web_search = _build_web_search_tool(web_cfg)
    if web_search is not None:
        registry.register(web_search)

    fs_cfg = tools_cfg.get("filesystem", {}) if isinstance(tools_cfg.get("filesystem"), dict) else {}
    file_tools = _build_file_tools(cfg, fs_cfg)
    for tool in file_tools:
        registry.register(tool)

    coding_cfg = tools_cfg.get("coding", {}) if isinstance(tools_cfg.get("coding"), dict) else {}
    coding_tools = _build_coding_tools(cfg, bus, temporal, coding_cfg)
    for tool in coding_tools:
        registry.register(tool)

    computer_cfg = tools_cfg.get("computer", {}) if isinstance(tools_cfg.get("computer"), dict) else {}
    computer_tools = _build_computer_tools(bus, temporal, computer_cfg)
    for tool in computer_tools:
        registry.register(tool)

    policy = DefaultPermissionPolicy(
        allowlist_names=tools_cfg.get("allowlist", []) or []
    )
    confirm_cfg = (
        tools_cfg.get("permission_confirmation", {})
        if isinstance(tools_cfg.get("permission_confirmation"), dict)
        else {}
    )
    confirmation = ConfirmationManager(
        bus=bus,
        temporal=temporal,
        default_ttl_seconds=float(confirm_cfg.get("default_ttl_seconds", 60.0)),
        max_ttl_seconds=float(confirm_cfg.get("max_ttl_seconds", 300.0)),
    )
    executor = ToolExecutor(
        registry=registry,
        bus=bus,
        policy=policy,
        temporal=temporal,
        default_timeout=float(tools_cfg.get("timeout", 30.0)),
        confirmation=confirmation,
    )
    return registry, executor, confirmation


def _build_file_tools(cfg: Config, fs_cfg: dict):
    """Build the file/local tools bound to the configured workspace root.

    The workspace root is resolved through the existing config system
    (project-relative default: `workspace/`); it is never hardcoded. Resource
    limits come from `tools.filesystem` (see FilesystemLimits).
    """
    from chris.tools.file_tools import build_file_tools
    from chris.tools.filesystem.limits import FilesystemLimits
    from chris.tools.filesystem.path import WorkspacePathPolicy

    workspace = fs_cfg.get("workspace_root", "workspace")
    root = cfg.resolve(workspace)
    policy = WorkspacePathPolicy(root)
    limits = FilesystemLimits.from_dict(fs_cfg)
    return build_file_tools(
        policy,
        limits=limits,
        reject_sensitive=bool(fs_cfg.get("reject_sensitive", False)),
    )


def _build_coding_tools(cfg: Config, bus: EventBus, temporal, coding_cfg: dict):
    """Build the coding (OpenCode) tools.

    OpenCode is an external execution backend; if it is not installed the tools
    still register, and any submission returns opencode_not_installed. The
    project boundary reuses the filesystem workspace policy (one path policy,
    not a second ad-hoc one).
    """
    from chris.tools.coding.adapter import OpenCodeAdapter
    from chris.tools.coding.manager import CodingTaskManager
    from chris.tools.coding.policy import CodingExecutionPolicy
    from chris.tools.coding.project import ProjectResolver
    from chris.tools.coding.tool import build_coding_tools
    from chris.tools.filesystem.path import WorkspacePathPolicy

    workspace = coding_cfg.get("workspace_root", "workspace")
    root = cfg.resolve(workspace)
    policy = WorkspacePathPolicy(root)
    resolver = ProjectResolver(policy)

    command = coding_cfg.get("command", "opencode")
    exec_policy = CodingExecutionPolicy(
        allowed_scopes=coding_cfg.get("allowed_scopes") or None
    )
    adapter = OpenCodeAdapter(command=command, policy=exec_policy)
    manager = CodingTaskManager(
        adapter=adapter,
        policy=exec_policy,
        bus=bus,
        temporal=temporal,
        default_timeout=float(coding_cfg.get("default_timeout_seconds", 120.0)),
        max_timeout=float(coding_cfg.get("max_timeout_seconds", 600.0)),
    )
    return build_coding_tools(manager, resolver)


def _build_computer_tools(bus, temporal, computer_cfg: dict):
    """Build the Computer Action tools bound to a backend.

    Default backend is 'fake' (deterministic, safe for development); the real
    Windows ctypes backend is selected via `tools.computer.backend` = windows/auto.
    No third-party desktop-automation dependency is installed.
    """
    from chris.tools.computer.backend import create_backend
    from chris.tools.computer.manager import ComputerActionManager
    from chris.tools.computer.policy import ComputerActionPolicy
    from chris.tools.computer.tools import build_computer_tools

    backend_name = computer_cfg.get("backend", "fake")
    allowlist = computer_cfg.get("allowlist_actions", []) or []
    policy = ComputerActionPolicy(allowlist_actions=allowlist)
    backend = create_backend(backend_name)
    manager = ComputerActionManager(
        backend=backend,
        policy=policy,
        bus=bus,
        temporal=temporal,
        default_timeout=float(computer_cfg.get("default_timeout_seconds", 10.0)),
        max_timeout=float(computer_cfg.get("max_timeout_seconds", 60.0)),
        max_actions_per_sequence=int(
            computer_cfg.get("max_actions_per_sequence", 20)
        ),
        max_type_text_length=int(
            computer_cfg.get("max_type_text_length", 2000)
        ),
    )
    return build_computer_tools(manager)


def _build_web_search_tool(web_cfg: dict) -> WebSearchTool | None:
    """Build the web_search tool from the `tools.web_search` config section.

    Disabled by default: no provider config means no network calls. The search
    provider is created through the factory (swappable via config); an unknown
    provider name is a loud misconfiguration, never silently ignored.
    """
    if not web_cfg.get("enabled", False):
        return None
    provider = create_search_provider(web_cfg)
    return WebSearchTool(
        provider=provider,
        max_results=int(web_cfg.get("max_results", 5)),
        snippet_chars=int(web_cfg.get("snippet_max_chars", 300)),
    )


def _build_retrieval(cfg: Config, memory, memory_config: MemoryConfig, temporal, memory_path) -> RetrievalGateway | None:
    """Construct the retrieval gateway.

    Embedding is optional: a missing / broken embedding provider never stops
    Chris - the gateway falls back to keyword retrieval. No API key is required
    for the default mock provider.
    """
    try:
        embedding_cfg = (cfg.memory or {}).get("embedding", {}) if isinstance(cfg.memory, dict) else {}
        provider = create_embedding_provider_from_config(embedding_cfg)
        cache_path = memory_path.with_name(memory_path.stem + ".embeddings.json")
        if embedding_cfg.get("cache_file"):
            cache_path = cfg.resolve(embedding_cfg["cache_file"])
        cache = EmbeddingCache(
            cache_path,
            model=getattr(provider, "model", memory_config.embedding_model),
            dimension=int(embedding_cfg.get("dimension") or getattr(provider, "dimension", 0)
                          or memory_config.embedding_dimension),
            temporal=temporal,
        )
        semantic = SemanticRetriever(
            provider,
            cache,
            min_similarity=memory_config.semantic_min_similarity,
            top_k=memory_config.semantic_top_k,
            generate_cap=memory_config.embedding_generate_cap,
            batch_size=memory_config.embedding_batch_size,
        )
        return RetrievalGateway(memory, memory_config, temporal, semantic=semantic)
    except Exception:  # noqa: BLE001
        return None


def build_behavior_context(runtime: Runtime, has_user_message: bool = False) -> dict:
    """Context the Behavior Engine can read (world + temporal), for future proactive behavior."""
    awareness = runtime.world_awareness
    return {
        "has_user_message": has_user_message,
        "idle_hours": runtime.temporal.idle_time().total_seconds() / 3600,
        "user_presence": runtime.world.user.presence.value,
        "user_activity": runtime.world.user.activity.value,
        "user_attention": runtime.world.user.attention.value,
        "user_state": runtime.world.user.estimated_state.value,
        "activity_duration_seconds": awareness.activity_duration().total_seconds(),
        "idle_seconds": runtime.world.computer.idle_seconds,
        "foreground_app": runtime.world.computer.foreground_app,
        "time_of_day": runtime.temporal.time_of_day(),
        "recent_interaction_hours": runtime.temporal.idle_time().total_seconds() / 3600,
    }


def build_system_prompt(
    runtime: Runtime,
    exclude_since: object | None = None,
    query_text: str = "",
) -> str:
    """Render a short system prompt from structured state (kept minimal).

    The prompt is a runtime projection of persisted structured state, never
    the storage itself. `exclude_since`: skip memory entries written after this
    moment (used by chat UIs that pass real conversation turns separately, to
    avoid duplicating the current session in the projection).
    `query_text`: current user message, used to rank the most relevant memories
    into the prompt (MemoryQuery -> Top-K, within the memory prompt budget).
    """
    core = runtime.personality.core
    style = core.language_style
    character = core.identity or core.base_character
    lines = [
        f"You are {core.name}. {character}",
        f"Reply language: {style.language}.",
        f"Language style: {style.formality}, {style.sentence_preference} sentences. "
        f"Greeting: {style.greeting_style}.",
        f"Humor tendency: {core.humor_tendency}. Emotion expression: {core.emotion_expression}.",
        "You have a built-in time awareness module: you always know the current local "
        "time, the time of day, how long you have known the user, the time since the "
        "last interaction, and your current session length. Do not claim you cannot "
        "perceive time.",
    ]
    lines.extend(runtime.temporal.time_context())
    cs = runtime.personality.communication_style
    lines.append(
        f"Communication: {cs.overall} (humor {cs.humor:.2f}, warmth {cs.warmth:.2f}, "
        f"formality {cs.formality:.2f}, directness {cs.directness:.2f})."
    )
    rel = runtime.personality.relationship
    lines.append(
        f"Relationship to user: {rel.relationship_type} "
        f"(trust {rel.trust:.2f}, familiarity {rel.familiarity:.2f})."
    )
    if runtime.world.location != "unknown":
        lines.append(f"Current location: {runtime.world.location}")
    if runtime.world.current_activity != "idle":
        lines.append(f"Current activity: {runtime.world.current_activity}")
    recent = runtime.memory.list_recent(limit=30)
    if exclude_since is not None:
        recent = [entry for entry in recent if entry.timestamp < exclude_since]
    recent = [entry for entry in recent if not entry.metadata.get("remember")]
    if recent:
        lines.append("Recent context (each line has an absolute local timestamp):")
        for entry in reversed(recent[:5]):
            ts = runtime.temporal.local_str(entry.timestamp)
            lines.append(f"- ({ts}) {entry.content}")
    remembered_fn = getattr(runtime.memory, "list_remembered", None)
    if remembered_fn is not None:
        remembered = remembered_fn(limit=5)
        if remembered:
            lines.append("Things the user explicitly asked you to remember:")
            for entry in remembered:
                ts = runtime.temporal.local_str(entry.timestamp)
                lines.append(f"- {entry.content} ({ts})")
    if query_text:
        try:
            budget = getattr(runtime.memory._config, "memory_prompt_budget", 5)
            retrieval = getattr(runtime, "retrieval", None)
            include_archived = bool(getattr(runtime, "_recall_include_archived", False))
            q = MemoryQuery(text=query_text, limit=budget, include_archived=include_archived)
            if retrieval is not None:
                results = retrieval.query(q)
            else:
                results = runtime.memory.query(q)
            if results:
                lines.append("Memory context (relevant to the current message, ranked):")
                for result in results:
                    entry = result.memory
                    content = (entry.summary or entry.content)[:120]
                    lines.append(
                        f"- [{entry.memory_type}] {content} "
                        f"(vitality {entry.vitality:.2f}, importance {entry.importance:.2f})"
                    )
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(lines)
