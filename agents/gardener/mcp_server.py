"""Model Context Protocol server exposing hydro tools."""
from __future__ import annotations

import json
from typing import Any, Dict, List

from mcp import types
from mcp.server import InitializationOptions, NotificationOptions, Server
from mcp.server.stdio import stdio_server

from .tools import ToolRegistry


def create_server(registry: ToolRegistry) -> Server:
    """Create an MCP server wired up to the tool registry."""

    server = Server(
        name="hydro-gardener",
        instructions=(
            "Hydro Gardener exposes hydroponics control tools. "
            "Tools correspond to backend API capabilities and are refreshed dynamically."
        ),
        version="0.1.0",
    )

    @server.list_tools()
    async def list_tools() -> List[types.Tool]:
        await registry.refresh()
        return [
            types.Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.input_schema,
            )
            for tool in registry.all()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]):
        tool = registry.get(name)
        if not tool:
            return [types.TextContent(type="text", text=f"Tool '{name}' is not registered")]

        result = await tool.handler(arguments)

        # Handle MCP-formatted content responses (e.g., images)
        if isinstance(result, dict) and "content" in result:
            content_list = []
            for item in result["content"]:
                if item.get("type") == "image":
                    content_list.append(
                        types.ImageContent(
                            type="image",
                            data=item["data"],
                            mimeType=item.get("mimeType", "image/webp")
                        )
                    )
                elif item.get("type") == "text":
                    content_list.append(
                        types.TextContent(type="text", text=item.get("text", ""))
                    )
            return content_list

        # Default: return result as JSON text
        json_result = json.dumps(result, indent=2)
        return [types.TextContent(type="text", text=json_result)]

    return server


async def serve_stdio(registry: ToolRegistry) -> None:
    """Serve the MCP API over stdio (for Claude Desktop integrations)."""

    server = create_server(registry)
    init_options: InitializationOptions = server.create_initialization_options(
        notification_options=NotificationOptions(),
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)
