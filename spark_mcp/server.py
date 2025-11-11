#!/usr/bin/env python3
"""Spark MCP Server - Provides access to Spark Desktop meeting transcripts."""

import asyncio
import json
from typing import Any, Sequence
from mcp.server import Server
from mcp.types import Tool, TextContent, GetPromptResult
from mcp.server.stdio import stdio_server
from .database import SparkDatabase


# Initialize database (errors will be logged by MCP framework)
db = SparkDatabase()


# Create server instance
server = Server("spark-mcp-server")


# Define tools - simplified descriptions and reduced limits
TOOLS: list[Tool] = [
    Tool(
        name="list_meeting_transcripts",
        description="List recent meeting transcripts",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "number", "description": "Max results (default: 10)", "default": 10}
            }
        }
    ),
    Tool(
        name="get_meeting_transcript",
        description="Get full transcript by messagePk",
        inputSchema={
            "type": "object",
            "properties": {
                "messagePk": {"type": "number", "description": "Message PK"}
            },
            "required": ["messagePk"]
        }
    ),
    Tool(
        name="search_meeting_transcripts",
        description="Search transcripts by keyword",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "number", "description": "Max results (default: 5)", "default": 5}
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="get_transcript_statistics",
        description="Get transcript stats and counts",
        inputSchema={"type": "object", "properties": {}}
    )
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: Any) -> Sequence[TextContent]:
    """Handle tool calls."""
    try:
        if name == "list_meeting_transcripts":
            result = db.list_transcripts(
                limit=int(arguments.get("limit", 10)),
                only_kept=True
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_meeting_transcript":
            message_pk = arguments.get("messagePk")
            if not message_pk:
                return [TextContent(type="text", text="Error: messagePk required")]
            result = db.get_transcript(message_pk=int(message_pk))
            if result is None:
                return [TextContent(type="text", text="Transcript not found")]
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "search_meeting_transcripts":
            query = arguments.get("query")
            if not query:
                return [TextContent(type="text", text="Error: query required")]
            result = db.search_transcripts(
                query=query,
                limit=int(arguments.get("limit", 5)),
                include_context=True
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_transcript_statistics":
            result = db.get_statistics()
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
