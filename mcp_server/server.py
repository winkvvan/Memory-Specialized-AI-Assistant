"""FastMCP server exposing Zhipu's GLM-4V vision model as a tool."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_server import vision

mcp = FastMCP("zhipu-vision")


@mcp.tool()
def describe_image(image: str, prompt: str = "请详细描述这张图片的内容。") -> str:
    """Describe an image using Zhipu's GLM-4V vision model.

    Args:
        image: Image as an HTTP(S) URL, a local file path, or a base64 data URI.
        prompt: What the model should do with the image.
    """
    return vision.describe_image(image=image, prompt=prompt)


def main() -> None:
    vision.load_env_file()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
