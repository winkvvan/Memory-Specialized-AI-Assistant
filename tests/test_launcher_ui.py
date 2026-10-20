"""UI layer tests: markdown parser, ViewModel, streaming chat, theme.
All use MockProvider / temp data - never the real API."""

import tempfile
import threading
import time
import unittest
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from launcher import chat as launcher_chat
from launcher.markdown import parse
from launcher.theme import Theme
from launcher.viewmodel import ViewModel

from chris.config import Config
from chris.llm.mock import MockProvider


def make_config(tmp):
    cfg = Config()
    cfg.clock_mode = "manual"
    cfg.personality_file = str(Path(tmp) / "p.json")
    cfg.memory_file = str(Path(tmp) / "m.json")
    cfg.temporal_file = str(Path(tmp) / "t.json")
    cfg.world_state_file = str(Path(tmp) / "w.json")
    return cfg


class TestMarkdown(unittest.TestCase):
    def test_parse_text_only(self):
        segments = parse("hello **world**")
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["type"], "text")

    def test_parse_code_fence(self):
        segments = parse("before\n```python\nprint(1)\n```\nafter")
        types = [s["type"] for s in segments]
        self.assertEqual(types, ["text", "code", "text"])
        code = segments[1]
        self.assertEqual(code["lang"], "python")
        self.assertEqual(code["content"], "print(1)")

    def test_parse_unclosed_fence(self):
        segments = parse("```\nline1\nline2")
        self.assertEqual(segments[-1]["type"], "code")
        self.assertEqual(segments[-1]["content"], "line1\nline2")


class TestTheme(unittest.TestCase):
    def test_dark_light(self):
        self.assertIn("bg", Theme("dark").colors)
        self.assertIn("bg", Theme("light").colors)
        self.assertNotEqual(Theme("dark").c("bg"), Theme("light").c("bg"))

    def test_system_resolves(self):
        Theme("system")  # must not raise


class TestViewModel(unittest.TestCase):
    def _vm(self, tmp):
        runtime = launcher_chat.build_chat_pipeline(make_config(tmp), provider=MockProvider(responses=["ok"]))
        return runtime, ViewModel(runtime)

    def test_snapshot_has_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, vm = self._vm(tmp)
            snapshot = vm.snapshot()
            self.assertIn("world", snapshot)
            self.assertIn("temporal", snapshot)
            self.assertIn("personality", snapshot)
            self.assertEqual(snapshot["personality"]["name"], "Chris")

    def test_world_update_reflected(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, vm = self._vm(tmp)
            runtime.world_awareness.ingest({"activity": "CODING", "idle_seconds": 0})
            snapshot = vm.snapshot()
            self.assertEqual(snapshot["world"]["user"]["activity"], "CODING")

    def test_events_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, vm = self._vm(tmp)
            runtime.world_awareness.ingest({"activity": "CODING", "idle_seconds": 0})
            self.assertTrue(vm.snapshot()["recent_events"])


JUDGE_NOOP = '{"confirm_memory_ids": [], "want_old_memory": false}'


class TestStreamTurn(unittest.TestCase):
    def _runtime(self, tmp):
        return launcher_chat.build_chat_pipeline(
            make_config(tmp),
            provider=MockProvider(responses=[JUDGE_NOOP, "你好，我是 Chris。", "第二条"]),
        )

    def test_stream_returns_full_reply_and_writes_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            chunks = []
            reply = launcher_chat.stream_turn(
                runtime, "你好", threading.Event(), on_chunk=chunks.append
            )
            self.assertTrue(reply.startswith("你好"))
            self.assertIn("Chris", reply)
            self.assertEqual("".join(chunks), reply)
            self.assertTrue(runtime.memory.search("你好"))

    def test_stream_cancel_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            cancel = threading.Event()
            cancel.set()
            self.assertIsNone(launcher_chat.stream_turn(runtime, "你好", cancel))

    def test_blocking_chat_turn_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            self.assertEqual(launcher_chat.chat_turn(runtime, "你好"), "你好，我是 Chris。")


class TestMainWindow(unittest.TestCase):
    def _pump(self, root, seconds=0.6):
        deadline = time.time() + seconds
        while time.time() < deadline:
            root.update()
            time.sleep(0.02)

    def test_window_boots_with_mock_provider(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("tkinter unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            from launcher.ui.app_window import MainWindow

            root = tk.Tk()
            root.withdraw()
            win = MainWindow(
                root,
                Path(__file__).resolve().parent.parent,
                config=make_config(tmp),
                provider=MockProvider(responses=["ok"]),
            )
            self._pump(root)
            self.assertIsNotNone(win.runtime)
            self.assertIsNotNone(win.vm)
            win._quit()


class TestDesignTokens(unittest.TestCase):
    def test_semantic_tokens_present_in_both_themes(self):
        required = (
            "background", "surface", "surface-elevated", "text-primary",
            "text-secondary", "text-muted", "accent", "success", "warning", "error",
        )
        for name in ("light", "dark"):
            colors = Theme(name).colors
            for key in required:
                self.assertIn(key, colors)

    def test_light_is_default(self):
        self.assertEqual(Theme().name, "light")
        self.assertNotEqual(Theme("light").c("background"), Theme("dark").c("background"))

    def test_dark_is_not_mechanical_inversion(self):
        light, dark = Theme("light"), Theme("dark")
        self.assertNotEqual(dark.c("background"), "#000000")
        self.assertNotEqual(light.c("background"), "#FFFFFF")
        self.assertNotEqual(dark.c("text-primary"), "#FFFFFF")

    def test_type_scale_and_spacing(self):
        t = Theme("light")
        self.assertIn("display", t.type_scale())
        self.assertGreater(t.spacing("2xl"), t.spacing("xs"))
        self.assertEqual(t.radius("pill"), 999)


class TestViewModelProjections(unittest.TestCase):
    def _vm(self, tmp):
        runtime = launcher_chat.build_chat_pipeline(make_config(tmp), provider=MockProvider(responses=["ok"]))
        return runtime, ViewModel(runtime)

    def test_world_summary_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, vm = self._vm(tmp)
            runtime.world_awareness.ingest({"activity": "CODING", "idle_seconds": 0})
            snapshot = vm.snapshot()
            self.assertIn("world_summary", snapshot)
            self.assertIn("写代码", snapshot["world_summary"])


class TestMarkdownTables(unittest.TestCase):
    def test_parse_table(self):
        text = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
        segs = parse(text)
        table = segs[0]
        self.assertEqual(table["type"], "table")
        self.assertEqual(table["headers"], ["A", "B"])
        self.assertEqual(table["rows"], [["1", "2"], ["3", "4"]])

    def test_parse_mixed_table_and_text(self):
        text = "intro\n| H |\n|---|\n| v |\nend"
        types = [s["type"] for s in parse(text)]
        self.assertEqual(types, ["text", "table", "text"])

    def test_links_recorded_for_click(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("tkinter unavailable")
        root = tk.Tk()
        root.withdraw()
        try:
            from launcher.markdown import render_text
            from launcher.ui.chat_widget import markdown_tags
            text = tk.Text(root)
            markdown_tags(text, Theme("light"))
            link_map = {}
            render_text(text, "see [OpenAI](https://openai.com) here", {}, link_map)
            self.assertEqual(len(link_map), 1)
            self.assertEqual(list(link_map.values())[0], "https://openai.com")
        finally:
            root.destroy()


class TestStreamingOrder(unittest.TestCase):
    def test_chunks_append_in_order_and_marker_removed(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("tkinter unavailable")
        root = tk.Tk()
        root.withdraw()
        try:
            from launcher.ui.chat_widget import ChatView
            view = ChatView(root, Theme("light"))
            msg = view.start_streaming("10:00")
            for chunk in ["A", "B", "C", "D"]:
                msg.append_chunk(chunk)
            msg.end_stream()
            buf = msg.body_text.get("1.0", "end").replace("\n", "")
            self.assertEqual(buf, "ABCD")
            self.assertNotIn("▌", buf)
        finally:
            root.destroy()


class TestPages(unittest.TestCase):
    def _instantiate(self, cls, snapshot):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("tkinter unavailable")
        root = tk.Tk()
        root.withdraw()
        try:
            page = cls(root, Theme("light"))
            page.refresh(snapshot)
            page.apply_theme(Theme("dark"))
            page.refresh(snapshot)
        finally:
            root.destroy()

    def test_pages_render_without_data(self):
        from launcher.ui.panels import (
            DeveloperPage, EventsPage, MemoryPage, PersonalityPage,
            ProjectsPage, TemporalPage, ToolsPage, WorldPage,
        )
        for cls in (MemoryPage, WorldPage, ProjectsPage, ToolsPage,
                    PersonalityPage, TemporalPage, EventsPage, DeveloperPage):
            self._instantiate(cls, {})

    def test_page_does_not_rebuild_on_unchanged_snapshot(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("tkinter unavailable")
        root = tk.Tk()
        root.withdraw()
        try:
            from launcher.ui.panels import MemoryPage
            page = MemoryPage(root, Theme("light"))
            snap = {"recent_memories": [
                {"kind": "k", "age": "1天", "status": "ACTIVE", "content": "hi"},
            ]}
            page.refresh(snap)
            first = page.content.winfo_children()[0]
            page.refresh(snap)
            self.assertTrue(first.winfo_exists())
            snap2 = {"recent_memories": [
                {"kind": "k", "age": "2天", "status": "ACTIVE", "content": "hi"},
            ]}
            page.refresh(snap2)
            self.assertFalse(first.winfo_exists())
        finally:
            root.destroy()


class TestThemePersistence(unittest.TestCase):
    def test_load_default_light(self):
        import launcher.launcher as L
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(L._load_theme(Path(tmp)), "light")

    def test_persist_via_window(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("tkinter unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            from launcher.ui.app_window import MainWindow
            root = tk.Tk()
            root.withdraw()
            win = MainWindow(
                root,
                Path(tmp),
                config=make_config(tmp),
                provider=MockProvider(responses=["ok"]),
            )
            win._apply_theme("dark")
            root.update()
            data = json.loads((Path(tmp) / "data" / "ui.json").read_text(encoding="utf-8"))
            self.assertEqual(data["theme"], "dark")
            win._quit()


VOICE_CFG_ENABLED = {
    "enabled": True,
    "asr": {"enabled": True, "provider": "fake"},
    "tts": {"enabled": True, "provider": "fake"},
    "input": {"enabled": True, "backend": "fake"},
    "output": {"enabled": True, "backend": "fake"},
}


class TestVoiceGui(unittest.TestCase):
    def _pump(self, root, seconds=0.4):
        deadline = time.time() + seconds
        while time.time() < deadline:
            root.update()
            time.sleep(0.02)

    def _wait_until(self, root, cond, seconds=4.0):
        deadline = time.time() + seconds
        while time.time() < deadline:
            root.update()
            if cond():
                return True
            time.sleep(0.02)
        return cond()

    def _messages(self, win):
        out = []
        for m in win.chat.messages:
            body = getattr(m, "body_text", None)
            if body is not None:
                out.append(body.get("1.0", "end").strip())
        return out

    def _make_window(self, tmp, voice=None):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("tkinter unavailable")
        from launcher.ui.app_window import MainWindow

        cfg = make_config(tmp)
        cfg.voice = voice or {}
        root = tk.Tk()
        root.withdraw()
        win = MainWindow(
            root,
            Path(__file__).resolve().parent.parent,
            config=cfg,
            provider=MockProvider(responses=[JUDGE_NOOP, "好的，我听到了。", "备用"]),
        )
        return root, win

    def test_voice_toggle_auto_configures_tts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, win = self._make_window(tmp)  # empty voice section -> auto config
            self._pump(root, 0.6)
            self.assertFalse(win.voice_mode)
            win._toggle_voice()
            self.assertTrue(win.voice_mode)
            self.assertTrue(
                self._wait_until(
                    root,
                    lambda: any("语音回复已开启" in t for t in self._messages(win)),
                )
            )
            self.assertFalse(any("未配置 TTS" in t for t in self._messages(win)))
            self.assertTrue(win.runtime.voice.can_speak)
            win._toggle_voice()
            self.assertFalse(win.voice_mode)
            win._quit()

    def test_voice_toggle_enabled_with_fakes_no_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, win = self._make_window(tmp, VOICE_CFG_ENABLED)
            self._pump(root, 0.6)
            win._toggle_voice()
            self.assertTrue(win.voice_mode)
            self.assertTrue(win.runtime.voice.can_speak)
            self.assertTrue(win.runtime.voice.can_capture)
            self.assertFalse(any("未配置 TTS" in t for t in self._messages(win)))
            win._quit()

    def test_mic_without_capture_shows_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, win = self._make_window(tmp)  # no input/asr providers
            self._pump(root, 0.6)
            win._toggle_voice()
            win._toggle_mic()
            self.assertFalse(win._recording)
            self.assertTrue(any("语音输入未配置" in t for t in self._messages(win)))
            win._quit()

    def test_mic_capture_flows_into_chat_and_speaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, win = self._make_window(tmp, VOICE_CFG_ENABLED)
            self._pump(root, 0.6)
            win._toggle_voice()
            win._toggle_mic()
            self.assertTrue(win._recording)
            self.assertTrue(
                self._wait_until(
                    root,
                    lambda: any("语音识别测试" in t for t in self._messages(win)),
                )
            )
            self.assertTrue(
                self._wait_until(
                    root,
                    lambda: len(win.runtime.voice.pipeline.audio_output.played) >= 1,
                )
            )
            self.assertFalse(win._recording)
            win._quit()

    def test_inputbar_voice_states(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("tkinter unavailable")
        root = tk.Tk()
        root.withdraw()
        try:
            from launcher.ui.chat_widget import InputBar

            calls = {"voice": 0, "mic": 0}
            bar = InputBar(
                root,
                on_send=lambda t: None,
                on_stop=lambda: None,
                theme=Theme("light"),
                on_voice=lambda: calls.__setitem__("voice", calls["voice"] + 1),
                on_mic=lambda: calls.__setitem__("mic", calls["mic"] + 1),
            )
            self.assertTrue(bar.voice_btn._enabled)
            self.assertFalse(bar.mic_btn._enabled)
            bar.set_voice_active(True)
            self.assertEqual(bar.voice_btn._text, "● 语音")
            bar.set_voice_active(False)
            bar.set_mic_enabled(True)
            self.assertTrue(bar.mic_btn._enabled)
            bar.set_recording(True)
            self.assertEqual(bar.mic_btn._text, "■ 停止")
            bar.set_recording(False)
            bar._on_voice()
            bar._on_mic()
            self.assertEqual(calls["voice"], 1)
            self.assertEqual(calls["mic"], 1)
        finally:
            root.destroy()

    def test_mic_stop_uses_stop_recording_not_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, win = self._make_window(tmp, VOICE_CFG_ENABLED)
            self._pump(root, 0.6)
            win._toggle_voice()
            self.assertTrue(
                self._wait_until(root, lambda: win.runtime.voice.enabled)
            )
            calls = {"stop": 0, "cancel": 0}
            win.runtime.voice.stop_recording = (
                lambda: calls.__setitem__("stop", calls["stop"] + 1)
            )
            win.runtime.voice.cancel_voice_session = (
                lambda: calls.__setitem__("cancel", calls["cancel"] + 1)
            )
            win._set_recording(True)
            win._toggle_mic()  # second click = stop recording
            self.assertEqual(calls["stop"], 1)
            self.assertEqual(calls["cancel"], 0)
            win._quit()


if __name__ == "__main__":
    unittest.main()
