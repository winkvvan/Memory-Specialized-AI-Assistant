"""Zhipu GLM-4V vision client.

Talks to Zhipu's OpenAI-compatible chat completions endpoint. The vision model
accepts an image as an HTTP(S) URL or a base64 data URI in the message content,
alongside a text prompt. The API key is read from ZHIPU_API_KEY (falling back
to CHRIS_API_KEY), never committed to the repository.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

import requests

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4v-flash"
ENV_API_KEY = "ZHIPU_API_KEY"
ENV_MODEL = "ZHIPU_VISION_MODEL"


def load_env_file(path: str | Path | None = None) -> None:
    """Load KEY=VALUE pairs from a .env file (stdlib only, no python-dotenv).

    Existing environment variables win; the file is optional. If `path` is not
    given, looks for `.env` in the project root (parent of this package).
    """
    dotenv = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not dotenv.exists():
        return
    for raw in dotenv.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and not os.environ.get(key):
            os.environ[key] = value

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4v-flash"
ENV_API_KEY = "ZHIPU_API_KEY"
ENV_MODEL = "ZHIPU_VISION_MODEL"


def _api_key() -> str:
    load_env_file()
    key = os.environ.get(ENV_API_KEY) or os.environ.get("CHRIS_API_KEY")
    if not key:
        raise ValueError(
            f"{ENV_API_KEY} (or CHRIS_API_KEY) is required to call the Zhipu vision model. "
            "Set it as an environment variable."
        )
    return key


def _resolve_image(image: str) -> str:
    """Turn a URL / local path / data URI into an image_url-compatible value."""
    if image.startswith(("http://", "https://", "data:")):
        return image
    path = Path(image)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image}")
    mime = mimetypes.guess_type(image)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def describe_image(
    image: str,
    prompt: str = "请详细描述这张图片的内容。",
    model: str | None = None,
    max_tokens: int = 1024,
    timeout: float = 60.0,
) -> str:
    """Send the image + prompt to Zhipu GLM-4V and return the model's text reply.

    Args:
        image: HTTP(S) URL, local file path, or base64 data URI of the image.
        prompt: Instruction for the model about what to do with the image.
        model: Zhipu vision model id; defaults to glm-4v-flash.
        max_tokens: Maximum tokens in the reply.
        timeout: Request timeout in seconds.
    """
    url = _resolve_image(image)
    payload = {
        "model": model or os.environ.get(ENV_MODEL, DEFAULT_MODEL),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ],
        "max_tokens": max_tokens,
    }
    response = requests.post(
        f"{DEFAULT_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"Zhipu vision API error {response.status_code}: {response.text[:300]}"
        )
    data = response.json()
    return data["choices"][0]["message"].get("content", "")
