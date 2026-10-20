"""MCP server exposing Zhipu's GLM-4V vision model as MCP tools.

Standalone tool, kept separate from the `chris/` core package on purpose:
the core keeps its LLM Provider isolation boundary, while this server is an
external-facing MCP tool that calls the Zhipu OpenAI-compatible endpoint
directly. API key comes from the ZHIPU_API_KEY environment variable.
"""
