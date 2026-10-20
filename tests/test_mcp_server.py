"""Tests for the Zhipu vision MCP server (no network; HTTP is mocked)."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from mcp_server import vision
from mcp_server.server import describe_image, mcp

BIGMODEL = vision.DEFAULT_BASE_URL + "/chat/completions"


def _fake_ok_response():
    response = mock.Mock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = {
        "choices": [{"message": {"content": "画面里有一只橘猫。"}}]
    }
    return response


class TestResolveImage(unittest.TestCase):
    def test_http_url_passthrough(self):
        self.assertEqual(
            vision._resolve_image("https://example.com/a.png"),
            "https://example.com/a.png",
        )

    def test_data_uri_passthrough(self):
        uri = "data:image/png;base64,AAAA"
        self.assertEqual(vision._resolve_image(uri), uri)

    def test_local_file_becomes_data_uri(self):
        with TemporaryDirectory() as tmp:
            img = Path(tmp) / "pic.png"
            img.write_bytes(b"\x89PNG\x0d\x0a\x1a\x0ahello")
            result = vision._resolve_image(str(img))
            self.assertTrue(result.startswith("data:image/png;base64,"))

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            vision._resolve_image("no-such-file.png")


class TestDescribeImage(unittest.TestCase):
    def test_sends_correct_payload_and_returns_content(self):
        with mock.patch("requests.post", return_value=_fake_ok_response()) as post:
            with mock.patch.dict("os.environ", {"ZHIPU_API_KEY": "test-key"}):
                result = vision.describe_image("https://example.com/a.png", "这是什么？")

        self.assertEqual(result, "画面里有一只橘猫。")
        post.assert_called_once()
        args, kwargs = post.call_args.args, post.call_args.kwargs
        self.assertEqual(args[0], BIGMODEL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        payload = kwargs["json"]
        self.assertEqual(payload["model"], "glm-4v-flash")
        content = payload["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(
            content[1]["image_url"]["url"], "https://example.com/a.png"
        )

    def test_env_model_override(self):
        with mock.patch("requests.post", return_value=_fake_ok_response()) as post:
            with mock.patch.dict(
                "os.environ", {"ZHIPU_API_KEY": "k", "ZHIPU_VISION_MODEL": "glm-4v-plus"}
            ):
                vision.describe_image("https://example.com/a.png")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "glm-4v-plus")

    def test_missing_key_raises(self):
        with mock.patch.object(vision, "load_env_file"):
            with mock.patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(ValueError):
                    vision.describe_image("https://example.com/a.png")

    def test_http_error_raises(self):
        response = mock.Mock()
        response.ok = False
        response.status_code = 429
        response.text = "rate limited"
        with mock.patch("requests.post", return_value=response):
            with mock.patch.dict("os.environ", {"ZHIPU_API_KEY": "k"}):
                with self.assertRaises(RuntimeError):
                    vision.describe_image("https://example.com/a.png")


class TestEnvFile(unittest.TestCase):
    def test_load_env_file_sets_missing_vars(self):
        with TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("ZHIPU_API_KEY=from-file\n# comment\nEMPTY=\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {}, clear=True):
                vision.load_env_file(env)
                self.assertEqual(os_env("ZHIPU_API_KEY"), "from-file")

    def test_existing_env_wins(self):
        with TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("ZHIPU_API_KEY=from-file\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"ZHIPU_API_KEY": "from-env"}):
                vision.load_env_file(env)
                self.assertEqual(os_env("ZHIPU_API_KEY"), "from-env")


def os_env(key: str) -> str | None:
    import os

    return os.environ.get(key)


class TestMCPTool(unittest.TestCase):
    def test_tool_registered(self):
        names = [t.name for t in mcp._tool_manager.list_tools()]
        self.assertIn("describe_image", names)

    def test_tool_calls_vision_client(self):
        with mock.patch(
            "mcp_server.vision.describe_image", return_value="一只猫"
        ) as mocked:
            result = describe_image("https://example.com/a.png", "描述")
        self.assertEqual(result, "一只猫")
        mocked.assert_called_once_with(
            image="https://example.com/a.png", prompt="描述"
        )

    def test_tool_invokable_via_mcp_api(self):
        with mock.patch(
            "mcp_server.vision.describe_image", return_value="一只猫"
        ):
            result = asyncio.run(
                mcp.call_tool("describe_image", {"image": "x", "prompt": "y"})
            )
        self.assertIn("一只猫", str(result))


if __name__ == "__main__":
    unittest.main()
