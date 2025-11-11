#!/usr/bin/env python3
"""Spark MCP Server - Provides access to Spark Desktop meeting transcripts, emails, and calendar."""

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
    # ====================
    # TRANSCRIPT TOOLS
    # ====================
    Tool(
        name="list_meeting_transcripts",
        description="List meeting transcripts with metadata. Returns transcripts sorted by date (newest first). Supports filtering by date range and type. Includes both calendar-based meetings and ad-hoc transcriptions.",
        inputSchema={
            "type": "object",
            "properties": {
                "startDate": {"type": "string", "description": "Filter transcripts after this ISO date (e.g., '2025-01-01')"},
                "endDate": {"type": "string", "description": "Filter transcripts before this ISO date"},
                "includeAdHoc": {"type": "boolean", "description": "Include ad-hoc meetings. Default: true", "default": True},
                "onlyKept": {"type": "boolean", "description": "Only show kept transcripts. Default: true", "default": True},
                "limit": {"type": "number", "description": "Maximum results. Default: 50", "default": 50},
                "offset": {"type": "number", "description": "Pagination offset. Default: 0", "default": 0}
            }
        }
    ),
    Tool(
        name="get_meeting_transcript",
        description="Get full transcript content and metadata for a specific meeting. Provide either messagePk or transcriptId.",
        inputSchema={
            "type": "object",
            "properties": {
                "messagePk": {"type": "number", "description": "Message primary key from list_meeting_transcripts"},
                "transcriptId": {"type": "string", "description": "Transcript ID (mtid) if known"}
            }
        }
    ),
    Tool(
        name="search_meeting_transcripts",
        description="Search across all transcript content using full-text search. Returns matching transcripts with highlighted excerpts.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (supports FTS5 syntax like AND, OR, NOT, phrases with quotes)"},
                "startDate": {"type": "string", "description": "Filter results after this ISO date"},
                "endDate": {"type": "string", "description": "Filter results before this ISO date"},
                "limit": {"type": "number", "description": "Maximum results. Default: 20", "default": 20},
                "includeContext": {"type": "boolean", "description": "Include text context in excerpts. Default: true", "default": True}
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="get_transcript_statistics",
        description="Get overview statistics about the transcript collection including total counts, date ranges, and top senders.",
        inputSchema={"type": "object", "properties": {}}
    ),

    # ====================
    # EMAIL TOOLS
    # ====================
    Tool(
        name="list_emails",
        description="List emails with filtering options. Can filter by folder (inbox, sent, drafts, all), unread status, date range, and sender.",
        inputSchema={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Filter by folder: inbox, sent, drafts, all. Default: inbox", "default": "inbox"},
                "unreadOnly": {"type": "boolean", "description": "Only show unread emails. Default: false", "default": False},
                "startDate": {"type": "string", "description": "Filter emails after this ISO date"},
                "endDate": {"type": "string", "description": "Filter emails before this ISO date"},
                "sender": {"type": "string", "description": "Filter by sender email address"},
                "limit": {"type": "number", "description": "Maximum results. Default: 50", "default": 50},
                "offset": {"type": "number", "description": "Pagination offset. Default: 0", "default": 0}
            }
        }
    ),
    Tool(
        name="search_emails",
        description="Search emails using full-text search. Searches subject and body content with FTS5 syntax support.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (supports FTS5 syntax)"},
                "startDate": {"type": "string", "description": "Filter results after this ISO date"},
                "endDate": {"type": "string", "description": "Filter results before this ISO date"},
                "limit": {"type": "number", "description": "Maximum results. Default: 20", "default": 20}
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="get_email",
        description="Get full email content including body text, recipients, and thread information.",
        inputSchema={
            "type": "object",
            "properties": {
                "messagePk": {"type": "number", "description": "Message primary key from list_emails"}
            },
            "required": ["messagePk"]
        }
    ),
    Tool(
        name="find_action_items",
        description="Find emails with potential action items or todos. Searches for action-oriented language like 'todo', 'please review', 'need to', 'deadline', etc.",
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "number", "description": "Look back this many days. Default: 7", "default": 7},
                "limit": {"type": "number", "description": "Maximum results. Default: 20", "default": 20}
            }
        }
    ),
    Tool(
        name="find_pending_responses",
        description="Find emails you may need to respond to. Identifies inbox emails without sent replies in the same conversation thread.",
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "number", "description": "Look back this many days. Default: 7", "default": 7},
                "limit": {"type": "number", "description": "Maximum results. Default: 20", "default": 20}
            }
        }
    ),

    # ====================
    # CALENDAR TOOLS
    # ====================
    Tool(
        name="list_events",
        description="List calendar events. By default shows today's events, can specify custom date ranges.",
        inputSchema={
            "type": "object",
            "properties": {
                "startDate": {"type": "string", "description": "Start date (ISO format). Default: today"},
                "endDate": {"type": "string", "description": "End date (ISO format)"},
                "daysAhead": {"type": "number", "description": "If no endDate, look this many days ahead. Default: 1", "default": 1},
                "limit": {"type": "number", "description": "Maximum results. Default: 50", "default": 50}
            }
        }
    ),
    Tool(
        name="get_event_details",
        description="Get full event details including description, attendees, organizer, and conference links.",
        inputSchema={
            "type": "object",
            "properties": {
                "eventPk": {"type": "number", "description": "Event primary key from list_events"}
            },
            "required": ["eventPk"]
        }
    ),
    Tool(
        name="find_events_needing_prep",
        description="Find upcoming events that may need preparation. Identifies events with external attendees, conference links, or longer duration (>30 min).",
        inputSchema={
            "type": "object",
            "properties": {
                "hoursAhead": {"type": "number", "description": "Look this many hours ahead. Default: 24", "default": 24},
                "limit": {"type": "number", "description": "Maximum results. Default: 20", "default": 20}
            }
        }
    ),

    # ====================
    # COMBINED INTELLIGENCE
    # ====================
    Tool(
        name="get_daily_briefing",
        description="Get comprehensive daily briefing including: today's events, unread emails, recent action items, pending responses, and events needing preparation. Perfect for morning overview!",
        inputSchema={"type": "object", "properties": {}}
    ),
    Tool(
        name="find_context_for_meeting",
        description="Find recent email context related to a meeting. Searches for emails from/to meeting attendees to help you prepare.",
        inputSchema={
            "type": "object",
            "properties": {
                "eventPk": {"type": "number", "description": "Event primary key from list_events"},
                "daysBack": {"type": "number", "description": "Look back this many days for emails. Default: 30", "default": 30}
            },
            "required": ["eventPk"]
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
        # TRANSCRIPT TOOLS
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

        # EMAIL TOOLS
        elif name == "list_emails":
            result = db.list_emails(
                folder=arguments.get("folder", "inbox"),
                unread_only=arguments.get("unreadOnly", False),
                start_date=arguments.get("startDate"),
                end_date=arguments.get("endDate"),
                sender=arguments.get("sender"),
                limit=int(arguments.get("limit", 50)),
                offset=int(arguments.get("offset", 0))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "search_emails":
            query = arguments.get("query")
            if not query:
                return [TextContent(type="text", text="Error: Missing required parameter 'query'")]
            result = db.search_emails(
                query=query,
                start_date=arguments.get("startDate"),
                end_date=arguments.get("endDate"),
                limit=int(arguments.get("limit", 20))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_email":
            message_pk = arguments.get("messagePk")
            if not message_pk:
                return [TextContent(type="text", text="Error: Missing required parameter 'messagePk'")]
            result = db.get_email(int(message_pk))
            if result is None:
                return [TextContent(type="text", text="Email not found")]
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "find_action_items":
            result = db.find_action_items(
                days=int(arguments.get("days", 7)),
                limit=int(arguments.get("limit", 20))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "find_pending_responses":
            result = db.find_pending_responses(
                days=int(arguments.get("days", 7)),
                limit=int(arguments.get("limit", 20))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        # CALENDAR TOOLS
        elif name == "list_events":
            result = db.list_events(
                start_date=arguments.get("startDate"),
                end_date=arguments.get("endDate"),
                days_ahead=int(arguments.get("daysAhead", 1)),
                limit=int(arguments.get("limit", 50))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_event_details":
            event_pk = arguments.get("eventPk")
            if not event_pk:
                return [TextContent(type="text", text="Error: Missing required parameter 'eventPk'")]
            result = db.get_event_details(int(event_pk))
            if result is None:
                return [TextContent(type="text", text="Event not found")]
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "find_events_needing_prep":
            result = db.find_events_needing_prep(
                hours_ahead=int(arguments.get("hoursAhead", 24)),
                limit=int(arguments.get("limit", 20))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        # COMBINED INTELLIGENCE
        elif name == "get_daily_briefing":
            result = db.get_daily_briefing()
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "find_context_for_meeting":
            event_pk = arguments.get("eventPk")
            if not event_pk:
                return [TextContent(type="text", text="Error: Missing required parameter 'eventPk'")]
            result = db.find_context_for_meeting(
                event_pk=int(event_pk),
                days_back=int(arguments.get("daysBack", 30))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as error:
        return [TextContent(type="text", text=f"Error: {str(error)}")]

# Start server
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
