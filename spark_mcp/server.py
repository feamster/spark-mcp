#!/usr/bin/env python3
"""Spark MCP Server - Provides access to Spark Desktop meeting transcripts."""

import asyncio
import json
from typing import Any, Sequence
from mcp.server import Server
from mcp.types import Tool, TextContent, GetPromptResult
from mcp.server.stdio import stdio_server
from .database import SparkDatabase


# Initialize database
try:
    db = SparkDatabase()
except Exception as e:
    print(f"Error: Failed to connect to Spark databases: {e}", flush=True)
    raise


# Create server instance
server = Server("spark-mcp-server")


# Define tools
TOOLS: list[Tool] = [
    Tool(
        name="list_meeting_transcripts",
        description=(
            "List meeting transcripts with metadata. Returns transcripts sorted by date (newest first). "
            "Supports filtering by date range and type. Includes both calendar-based meetings and ad-hoc transcriptions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "startDate": {
                    "type": "string",
                    "description": "Filter transcripts after this ISO date (e.g., '2025-01-01')"
                },
                "endDate": {
                    "type": "string",
                    "description": "Filter transcripts before this ISO date"
                },
                "includeAdHoc": {
                    "type": "boolean",
                    "description": "Include ad-hoc meetings (transcripts without calendar events). Default: true",
                    "default": True
                },
                "onlyKept": {
                    "type": "boolean",
                    "description": "Only show kept transcripts (exclude deleted). Default: true",
                    "default": True
                },
                "limit": {
                    "type": "number",
                    "description": "Maximum number of results to return. Default: 50",
                    "default": 50
                },
                "offset": {
                    "type": "number",
                    "description": "Pagination offset. Default: 0",
                    "default": 0
                }
            }
        }
    ),
    Tool(
        name="get_meeting_transcript",
        description=(
            "Get full transcript content and metadata for a specific meeting. "
            "Provide either messagePk or transcriptId."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "messagePk": {
                    "type": "number",
                    "description": "Message primary key from list_meeting_transcripts"
                },
                "transcriptId": {
                    "type": "string",
                    "description": "Transcript ID (mtid) if known"
                }
            }
        }
    ),
    Tool(
        name="search_meeting_transcripts",
        description=(
            "Search across all transcript content using full-text search. "
            "Returns matching transcripts with highlighted excerpts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (supports FTS5 syntax like AND, OR, NOT, phrases with quotes)"
                },
                "startDate": {
                    "type": "string",
                    "description": "Filter results after this ISO date"
                },
                "endDate": {
                    "type": "string",
                    "description": "Filter results before this ISO date"
                },
                "limit": {
                    "type": "number",
                    "description": "Maximum number of results. Default: 20",
                    "default": 20
                },
                "includeContext": {
                    "type": "boolean",
                    "description": "Include surrounding text context in excerpts. Default: true",
                    "default": True
                }
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="get_transcript_statistics",
        description=(
            "Get overview statistics about the transcript collection including "
            "total counts, date ranges, and top senders."
        ),
        inputSchema={
            "type": "object",
            "properties": {}
        }
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
                start_date=arguments.get("startDate"),
                end_date=arguments.get("endDate"),
                include_ad_hoc=arguments.get("includeAdHoc", True),
                only_kept=arguments.get("onlyKept", True),
                limit=int(arguments.get("limit", 50)),
                offset=int(arguments.get("offset", 0))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_meeting_transcript":
            result = db.get_transcript(
                message_pk=arguments.get("messagePk"),
                transcript_id=arguments.get("transcriptId")
            )
            if result is None:
                return [TextContent(type="text", text="Transcript not found")]
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "search_meeting_transcripts":
            query = arguments.get("query")
            if not query:
                return [TextContent(type="text", text="Error: Missing required parameter 'query'")]

            result = db.search_transcripts(
                query=query,
                start_date=arguments.get("startDate"),
                end_date=arguments.get("endDate"),
                limit=int(arguments.get("limit", 20)),
                include_context=arguments.get("includeContext", True)
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
