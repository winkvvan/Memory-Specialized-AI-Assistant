"""Interactive command-line chat loop."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import timedelta

from chris.app import (
    Runtime,
    build_behavior_context,
    build_runtime,
    build_system_prompt,
    extract_remember,
    extract_remember_with_llm,
    process_recall_intent,
)
from chris.behavior.engine import BehaviorAction
from chris.config import Config
from chris.events import Event, EventType, utcnow
from chris.llm.base import Message
from chris.logging_setup import setup_logging
from chris.memory.base import MemoryEntry
from chris.memory.ecology import MemoryOperation, MemoryOperationProposal
from chris.personality.models import PersonalityChangeRecord
from chris.world.state import save_world_state

EXIT_COMMANDS = {"quit", "exit", "q", "/quit", "/exit"}
HEALTH_COMMANDS = {"/health", "/models"}
PERSONALITY_COMMANDS = {"/personality", "/history", "/adjust", "/evolve", "/rollback"}


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _handle_world_command(runtime: Runtime, line: str) -> bool:
    if not line.casefold().startswith("/world"):
        return False
    parts = line.split(None, 2)
    sub = parts[1].casefold() if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    w = runtime.world
    aw = runtime.world_awareness

    if sub == "":
        u = w.user
        c = w.computer
        print(
            f"User:\n"
            f"  presence: {u.presence.value}\n"
            f"  activity: {u.activity.value}\n"
            f"  attention: {u.attention.value}\n"
            f"  state: {u.estimated_state.value}\n"
            f"Computer:\n"
            f"  active: {c.active}\n"
            f"  foreground: {c.foreground_app or '-'}\n"
            f"  title: {c.window_title or '-'}\n"
            f"  idle: {c.idle_seconds:.0f}s\n"
            f"Environment:\n"
            f"  location: {w.location}\n"
            f"  camera: {w.environment.get('camera_available', False)}\n"
            f"  mic: {w.environment.get('microphone_available', False)}"
        )
    elif sub == "timeline":
        sessions = aw.timeline()
        if not sessions:
            print("(无活动会话)")
        for s in sessions:
            dur = s.duration(aw._now())
            end = s.ended_at or aw._now()
            print(f"  {s.started_at:%H:%M} - {end:%H:%M}  {s.activity}  ({dur.total_seconds()/60:.0f}min)")
    elif sub == "sensor":
        name = aw.sensor.name if aw.sensor else "none"
        print(f"sensor: {name}")
        print(f"last observation: {aw._last_observation or '(none)'}")
    elif sub == "simulate":
        if not arg:
            print("[usage] /world simulate activity=CODING attention=FOCUSED presence=PRESENT idle_seconds=0 duration=7200")
            return True
        fields = {}
        for token in arg.split():
            if "=" in token:
                key, value = token.split("=", 1)
                if key in ("idle_seconds", "duration", "confidence"):
                    fields[key] = float(value)
                elif key == "active":
                    fields[key] = value.lower() in ("1", "true")
                else:
                    fields[key] = value
        events = aw.simulate(**fields)
        print(f"[ok] simulated -> events: {[e.value for e in events]}")
    else:
        print("[usage] /world | /world timeline | /world sensor | /world simulate ...")
    return True


def _handle_memory_command(runtime: Runtime, line: str) -> bool:
    if not line.casefold().startswith("/memory"):
        return False
    parts = line.split(None, 2)
    sub = parts[1].casefold() if len(parts) > 1 else ""
    arg = parts[2].strip() if len(parts) > 2 else ""
    memory = runtime.memory

    if sub == "list":
        for entry in memory.list_active(limit=50):
            print(f"  [{entry.status}] {entry.memory_type} vitality={entry.vitality:.2f} {entry.content[:60]}")
    elif sub == "recall":
        results = memory.query(text=arg, limit=10)
        if not results:
            print("(无相关记忆)")
            return True
        for i, result in enumerate(results, 1):
            entry = result.memory
            print(
                f"{i}. {entry.content[:60]}  score={result.score:.3f} "
                f"vitality={entry.vitality:.2f} importance={entry.importance:.2f}"
            )
            print(f"   reason: {result.reason}")
    elif sub == "inspect":
        entry = memory.get(arg)
        if entry is None:
            print("[error] 未找到该记忆")
            return True
        for key, value in entry.to_dict().items():
            print(f"  {key}: {value}")
    elif sub == "timeline":
        events = memory.timeline(arg)
        if not events:
            print("(该记忆暂无生命周期事件)")
            return True
        for event in events:
            print(f"  {event['at']} {event['event']} {event['reason']}")
    elif sub == "access":
        entry = memory.access(arg)
        print(f"[ok] {arg} access_count={entry.access_count} vitality={entry.vitality:.2f}" if entry else "[error] 未找到")
    elif sub == "reinforce":
        entry = memory.reinforce(arg)
        print(f"[ok] {arg} life={entry.life_score:.1f} status={entry.status}" if entry else "[error] 未找到")
    elif sub == "decay":
        changed = memory.decay(arg or None)
        print(f"[ok] decayed {len(changed)} memory/memories")
    elif sub == "maintenance":
        report = runtime.memory_maintenance.run()
        print(f"[ok] maintenance {report}")
    elif sub == "invalidate":
        entry = memory.invalidate(arg)
        print(f"[ok] {arg} -> {entry.status}" if entry else "[error] 未找到")
    elif sub == "archive":
        entry = memory.archive_one(arg)
        print(f"[ok] {arg} -> {entry.storage_tier}" if entry else "[error] 未找到")
    elif sub == "forget":
        entry = memory.forget(arg)
        print(f"[ok] {arg} -> {entry.status}" if entry else "[error] 未找到")
    elif sub == "restore":
        entry = memory.restore(arg)
        print(f"[ok] {arg} -> {entry.status}" if entry else "[error] 未找到")
    elif sub == "compress":
        created = memory.compress(arg or None)
        print(f"[ok] compressed {len(created)} group(s): {[e.memory_id[:8] for e in created]}")
    elif sub == "gc":
        removed = memory.gc()
        print(f"[ok] gc removed {len(removed)} memories")
    elif sub == "search":
        retrieval = getattr(runtime, "retrieval", None)
        if retrieval is None:
            print("[error] 语义检索不可用（retrieval 未装配），请检查 embedding 配置")
            return True
        debug = retrieval.debug_query(arg or "", top_k=None)
        print(f"query: {debug.get('query', '')}")
        print(
            f"mode={debug.get('mode', '?')} semantic_enabled={debug.get('semantic_enabled')} "
            f"semantic_available={debug.get('semantic_available')} "
            f"fallback={debug.get('fallback') or 'none'}"
        )
        sem_cands = [d for d in debug.get("final", []) if d.get("semantic_score", 0) > 0]
        kw_cands = [d for d in debug.get("final", []) if d.get("keyword_score", 0) > 0]
        print(f"semantic candidates: {len(sem_cands)}  keyword candidates: {len(kw_cands)}  "
              f"final: {len(debug.get('final', []))}")
        if not debug.get("final"):
            print("(无相关记忆)")
            return True
        for i, d in enumerate(debug["final"], 1):
            reason = d.get("reason", {})
            print(
                f"{i}. {d['content'][:50]}  final={d['final_score']:.3f} "
                f"sem={d['semantic_score']:.3f} kw={d['keyword_score']:.3f} "
                f"status={d['status']} strength={d['strength']:.2f}"
            )
            print(f"   reason: {reason}")
    elif sub == "embeddings":
        retrieval = getattr(runtime, "retrieval", None)
        if retrieval is None:
            print("[error] 语义检索不可用（retrieval 未装配）")
            return True
        for key, value in retrieval.embedding_stats().items():
            print(f"  {key}: {value}")
    elif sub == "embedding" and arg.casefold() == "rebuild":
        retrieval = getattr(runtime, "retrieval", None)
        if retrieval is None:
            print("[error] 语义检索不可用（retrieval 未装配）")
            return True
        count = retrieval.rebuild_embeddings()
        print(f"[ok] embedded {count} memories (explicit rebuild)")
    elif sub == "stats":
        for key, value in memory.stats().items():
            print(f"  {key}: {value}")
    else:
        print(
            "[usage] /memory list|recall <query>|search <query>|embeddings|embedding rebuild"
            "|inspect <id>|timeline <id>|access <id>"
            "|reinforce <id>|decay <id>|maintenance|invalidate <id>|archive <id>|forget <id>|restore <id>"
            "|compress|gc|stats"
        )
    return True


def _show_personality(runtime: Runtime) -> None:
    p = runtime.personality
    core = p.core
    print(
        f"version v{p.version} | {core.name}: {core.identity or core.base_character}\n"
        f"traits={len(core.core_traits)} values={len(core.values)} boundaries={len(core.boundaries)}"
    )
    if p.adaptive.preferences:
        print("adaptive:")
        for key, value in p.adaptive.preferences.items():
            print(f"  {key} = {value}")
    pending = runtime.personality_store.pending_evidence()
    if pending:
        print("pending evidence:")
        for field, count in pending.items():
            print(f"  {field}: {count}")
    rollbacks = runtime.personality_store.rollback_events()
    if rollbacks:
        last = rollbacks[-1]
        print(f"last rollback: v{last.from_version} -> v{last.to_version}")


def _show_history(runtime: Runtime) -> None:
    current = runtime.personality_store.current().version
    for version in runtime.personality_store.history():
        change = version.change
        marker = " (current)" if version.version == current else ""
        if change:
            status = change.status.value
            print(
                f"v{version.version} {version.created_at.isoformat()} "
                f"<- {change.trait or change.field_name} [{status}]{marker}"
            )
        else:
            print(f"v{version.version} {version.created_at.isoformat()} (initial){marker}")


def _show_proposals(runtime: Runtime) -> None:
    pending = runtime.personality_store.pending_evidence()
    if pending:
        print("pending:")
        for field, count in pending.items():
            print(f"  {field}: {count} evidence entries")
    log = runtime.personality_store.proposal_log()
    if not log:
        print("no evaluated proposals yet")
        return
    print("proposal log:")
    for rec in log[-10:]:
        print(
            f"  {rec.proposal_id[:8]} {rec.trait or rec.field_name} "
            f"{rec.old_value} -> {rec.proposed_value} "
            f"conf={rec.confidence:.2f} [{rec.status.value}] {rec.decision_reason}"
        )


def _handle_personality_command(runtime: Runtime, line: str) -> bool:
    command, _, rest = line.partition(" ")
    command = command.casefold()
    rest = rest.strip()
    if command == "/personality":
        if not rest:
            _show_personality(runtime)
            return True
        sub, _, sub_rest = rest.partition(" ")
        sub = sub.casefold()
        if sub == "history":
            _show_history(runtime)
        elif sub == "proposals":
            _show_proposals(runtime)
        elif sub == "rollback":
            try:
                target = int(sub_rest.strip())
            except ValueError:
                print("[usage] /personality rollback <version>")
                return True
            _rollback(runtime, target)
        else:
            print("[usage] /personality [history|proposals|rollback <version>]")
        return True
    if command == "/history":
        _show_history(runtime)
        return True
    if command == "/adjust":
        parts = rest.split(None, 1)
        if len(parts) != 2:
            print("[usage] /adjust <field> <value>")
            return True
        field, value = parts
        runtime.personality_store.propose(
            PersonalityChangeRecord(
                timestamp=utcnow(),
                reason="user requested adjustment",
                evidence=f"user: /adjust {field} {value}",
                before={},
                after={"value": value},
                confidence=0.8,
                field_name=field,
                trait=field,
                proposed_value=value,
                source="user",
            )
        )
        pending = runtime.personality_store.pending_evidence()
        print(
            f"[ok] evidence recorded for {field!r} "
            f"(pending: {pending.get(field, 0)}). Use /evolve to validate."
        )
        return True
    if command == "/evolve":
        results = runtime.personality_store.run_evolution()
        runtime.personality = runtime.personality_store.current()
        for result in results:
            status = "approved" if result.approved else "rejected"
            print(f"[{status}] {result.field_name}: {result.reason}")
        print(f"current version: v{runtime.personality.version}")
        return True
    if command == "/rollback":
        try:
            target = int(rest)
        except ValueError:
            print("[usage] /rollback <version>")
            return True
        _rollback(runtime, target)
        return True
    return False


def _parse_delta(text: str) -> timedelta | None:
    match = re.fullmatch(r"(\d+)\s*([smhdw])", text.strip())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    seconds = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }[unit]
    return timedelta(seconds=amount * seconds)


def _show_time(runtime: Runtime) -> None:
    t = runtime.temporal
    print(
        f"clock: {t.clock_mode()} (timezone: {t._timezone})\n"
        f"current time: {t.now().isoformat()}\n"
        f"known since: {t.days_known()} days ago\n"
        f"last interaction: {t.idle_time().total_seconds() / 3600:.1f} hours ago\n"
        f"session: {t.session_uptime().total_seconds() / 60:.0f} minutes\n"
        f"total runtime: {t.total_runtime().total_seconds() / 3600:.1f} hours"
    )


def _show_time_events(runtime: Runtime) -> None:
    events = runtime.temporal.recent_events(limit=20)
    if not events:
        print("no time events recorded")
        return
    for event in reversed(events):
        print(f"{event['timestamp']} {event['type']}")


def _handle_time_command(runtime: Runtime, line: str) -> bool:
    command, _, rest = line.partition(" ")
    command = command.casefold()
    rest = rest.strip()
    if command != "/time":
        return False
    if not rest:
        _show_time(runtime)
        return True
    sub, _, sub_rest = rest.partition(" ")
    sub = sub.casefold()
    if sub == "events":
        _show_time_events(runtime)
    elif sub == "advance":
        delta = _parse_delta(sub_rest)
        if delta is None:
            print("[usage] /time advance <n>[smhdw]  e.g. /time advance 7d")
            return True
        if runtime.temporal.clock_mode() != "manual":
            runtime.temporal.switch_clock("manual")
            print("(switched to manual clock; simulation mode on)")
        if runtime.temporal.advance(delta):
            print(f"[ok] advanced {delta} (now {runtime.temporal.now().isoformat()})")
        else:
            print("[error] clock is not manual")
    elif sub == "mode":
        mode = sub_rest.casefold()
        if mode not in ("manual", "real", "accelerated"):
            print("[usage] /time mode manual|real|accelerated")
            return True
        runtime.temporal.switch_clock(mode)
        print(f"[ok] clock mode -> {mode}")
    else:
        print("[usage] /time | /time events | /time advance <n>[smhdw] | /time mode <mode>")
    return True


def _rollback(runtime: Runtime, target: int) -> None:
    if runtime.personality_store.rollback(target):
        runtime.personality = runtime.personality_store.current()
        print(f"[ok] rolled back to v{runtime.personality.version}")
    else:
        print(f"[error] version {target} not found")


def _generate_reply(runtime: Runtime, messages: list[Message]) -> str:
    """Run the turn through the tool pipeline, falling back to plain generate."""
    from chris.tools.pipeline import run_tool_pipeline

    reply = run_tool_pipeline(
        runtime,
        messages,
        max_tokens=runtime.config.max_response_tokens,
        confirm=_confirm_prompt,
    )
    if reply is not None:
        return reply
    return runtime.provider.generate(
        messages, max_tokens=runtime.config.max_response_tokens
    )


_APPROVE_WORDS = {"y", "yes", "允许", "确认", "批准", "同意"}
_DENY_WORDS = {"n", "no", "拒绝", "取消", "不允许"}


def interpret_confirmation_answer(answer: str) -> bool:
    """Map a user's explicit answer to approve (True) / deny (False).

    Only the explicit allowlist approves; ambiguous input ("嗯", "应该可以",
    "你看着办") never approves.
    """
    text = (answer or "").strip().casefold()
    if text in _APPROVE_WORDS:
        return True
    return False


def _confirm_prompt(request) -> bool:
    """Explicit CLI confirmation prompt (whitelist words only).

    Never treats ambiguous input as approval - only the explicit allow words
    approve; everything else denies.
    """
    from chris.tools.confirmation.reason import render_reason

    print("\n⚠️  需要确认:")
    print(render_reason(request))
    print(f"[确认 ID: {request.confirmation_id}]")
    answer = input("允许吗？(y/yes 允许, n/no 拒绝): ")
    return interpret_confirmation_answer(answer)


def print_provider_health(runtime: Runtime) -> None:
    provider = runtime.provider
    report = getattr(provider, "health_report", None)
    if report is None:
        ok = provider.health_check()
        print(f"provider={provider.name} result={'ok' if ok else 'fail'}")
        return
    try:
        info = report()
    except ValueError as exc:
        print(f"provider={provider.name} check failed: {exc}")
        return
    status = f"status={info['status']}" if info.get("status") else "no response"
    line = (
        f"provider={provider.name} base_url={info['base_url']} "
        f"model={info['model']} result={'ok' if info['ok'] else 'fail'} {status}"
    )
    if info.get("error"):
        line += f"\nerror: {info['error']}"
    print(line)


def run_cli(runtime: Runtime) -> None:
    print("Chris ready. Type a message, or 'quit' to exit.")
    runtime.temporal.start_session()
    runtime.temporal.start_ticker(interval=1.0)
    try:
        while True:
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.casefold() in HEALTH_COMMANDS:
                print_provider_health(runtime)
                continue
            if line.casefold() in EXIT_COMMANDS:
                break
            if line.casefold() in PERSONALITY_COMMANDS or line.casefold().startswith(
                ("/personality ", "/adjust ", "/rollback ")
            ):
                if _handle_personality_command(runtime, line):
                    continue
            if line.casefold().startswith("/world"):
                if _handle_world_command(runtime, line):
                    continue
            if line.casefold().startswith("/memory"):
                if _handle_memory_command(runtime, line):
                    continue
            if line.casefold().startswith("/time"):
                if _handle_time_command(runtime, line):
                    continue

            runtime.bus.publish(Event(EventType.USER_MESSAGE, "cli", {"content": line}))
            runtime.temporal.record_interaction()
            just_added = runtime.memory.add(
                MemoryEntry(kind="conversation", content=f"user: {line}", metadata={"role": "user"})
            )
            facts = extract_remember_with_llm(runtime.provider, line)
            if not facts:
                fallback = extract_remember(line)
                if fallback:
                    facts = [fallback]
            for fact in facts:
                runtime.memory.propose(
                    MemoryOperationProposal(
                        operation=MemoryOperation.CREATE.value,
                        content=fact,
                        memory_type="",
                        remember=True,
                        source="EXPLICIT_USER",
                        reason="user asked to remember",
                    )
                )

            context = build_behavior_context(runtime, has_user_message=True)
            decision = runtime.behavior.decide(context)
            if decision.action is BehaviorAction.NO_ACTION:
                print("(no action)")
                continue

            try:
                process_recall_intent(
                    runtime, line, exclude_memory_ids={just_added.memory_id}
                )
            except Exception:  # noqa: BLE001
                pass

            messages: list[Message] = [
                {"role": "system", "content": build_system_prompt(runtime, query_text=line)},
                {"role": "user", "content": line},
            ]
            try:
                reply = _generate_reply(runtime, messages)
            except (ValueError, RuntimeError) as exc:
                print(f"[error] {exc}")
                continue
            runtime.memory.add(
                MemoryEntry(kind="conversation", content=f"chris: {reply}", metadata={"role": "assistant"})
            )
            runtime.bus.publish(Event(EventType.CHRIS_MESSAGE, "cli", {"content": reply}))
            print(f"{runtime.personality.core.name}> {reply}")
    finally:
        runtime.temporal.stop_ticker()
        runtime.temporal.end_session()
        save_world_state(runtime.world, runtime.config.resolve(runtime.config.world_state_file))


def main() -> None:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(prog="chris", description="Chris personal AI agent")
    parser.add_argument(
        "--health",
        "--check",
        action="store_true",
        help="probe the configured LLM provider (via /models) and exit",
    )
    args = parser.parse_args()

    cfg = Config.load()
    setup_logging(cfg.resolve(cfg.log_file), cfg.log_level)
    runtime = build_runtime(cfg)
    _wire_cli_voice(runtime)
    if args.health:
        print_provider_health(runtime)
        return
    run_cli(runtime)


def _wire_cli_voice(runtime) -> None:
    """Attach the voice chat runner so CLI voice sessions route into chat."""
    try:
        voice = getattr(runtime, "voice", None)
        if voice is None:
            return
        # CLI has no GUI stream; route ASR text through chat_turn (blocking).
        from launcher.chat import chat_turn

        class _CliVoiceRunner:
            def run(self, user_text, session):
                return chat_turn(runtime, user_text) or ""

        voice.set_chat_runner(_CliVoiceRunner())
    except Exception:  # noqa: BLE001
        pass
