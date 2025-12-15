#!/usr/bin/env python3
"""Spark MCP Server - Access Spark Desktop transcripts, emails, and calendar."""

import asyncio
import json
from typing import Any, Sequence
from mcp.server import Server
from mcp.types import Tool, TextContent, GetPromptResult
from mcp.server.stdio import stdio_server
from .database import SparkDatabase
from .pdf_operations import pdf_ops


# Initialize database (errors will be logged by MCP framework)
db = SparkDatabase()


# Create server instance
server = Server("spark-mcp-server")


# Define tools - optimized with minimal descriptions and small limits
TOOLS: list[Tool] = [
    # TRANSCRIPT TOOLS
    Tool(
        name="list_meeting_transcripts",
        description="List recent meeting transcripts",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "number", "description": "Max results", "default": 20}
            }
        }
    ),
    Tool(
        name="get_meeting_transcript",
        description="Get full transcript by ID",
        inputSchema={
            "type": "object",
            "properties": {
                "messagePk": {"type": "number", "description": "Message ID"}
            },
            "required": ["messagePk"]
        }
    ),
    Tool(
        name="search_meeting_transcripts",
        description="Search transcript content",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "limit": {"type": "number", "description": "Max results", "default": 10}
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="get_transcript_statistics",
        description="Get transcript stats",
        inputSchema={"type": "object", "properties": {}}
    ),

    # EMAIL TOOLS
    Tool(
        name="list_emails",
        description="List recent emails",
        inputSchema={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "inbox/sent/all", "default": "inbox"},
                "limit": {"type": "number", "description": "Max results", "default": 20}
            }
        }
    ),
    Tool(
        name="search_emails",
        description="Search email content",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "limit": {"type": "number", "description": "Max results", "default": 10}
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="get_email",
        description="Get full email by ID",
        inputSchema={
            "type": "object",
            "properties": {
                "messagePk": {"type": "number", "description": "Message ID"}
            },
            "required": ["messagePk"]
        }
    ),
    Tool(
        name="find_action_items",
        description="Find emails with todos",
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "number", "description": "Days back", "default": 7},
                "limit": {"type": "number", "description": "Max results", "default": 20}
            }
        }
    ),
    Tool(
        name="find_pending_responses",
        description="Find emails needing replies",
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "number", "description": "Days back", "default": 7},
                "limit": {"type": "number", "description": "Max results", "default": 20}
            }
        }
    ),

    # CALENDAR TOOLS
    Tool(
        name="list_events",
        description="List calendar events",
        inputSchema={
            "type": "object",
            "properties": {
                "daysAhead": {"type": "number", "description": "Days ahead", "default": 1},
                "limit": {"type": "number", "description": "Max results", "default": 20}
            }
        }
    ),
    Tool(
        name="get_event_details",
        description="Get event details by ID",
        inputSchema={
            "type": "object",
            "properties": {
                "eventPk": {"type": "number", "description": "Event ID"}
            },
            "required": ["eventPk"]
        }
    ),
    Tool(
        name="find_events_needing_prep",
        description="Find events needing preparation",
        inputSchema={
            "type": "object",
            "properties": {
                "hoursAhead": {"type": "number", "description": "Hours ahead", "default": 24},
                "limit": {"type": "number", "description": "Max results", "default": 20}
            }
        }
    ),

    # COMBINED INTELLIGENCE
    Tool(
        name="get_daily_briefing",
        description="Get today's briefing",
        inputSchema={"type": "object", "properties": {}}
    ),
    Tool(
        name="find_context_for_meeting",
        description="Find emails for meeting",
        inputSchema={
            "type": "object",
            "properties": {
                "eventPk": {"type": "number", "description": "Event ID"},
                "daysBack": {"type": "number", "description": "Days back", "default": 30}
            },
            "required": ["eventPk"]
        }
    ),

    # ATTACHMENT TOOLS
    Tool(
        name="list_attachments",
        description="List attachments for an email",
        inputSchema={
            "type": "object",
            "properties": {
                "messagePk": {"type": "number", "description": "Message ID"}
            },
            "required": ["messagePk"]
        }
    ),
    Tool(
        name="get_attachment",
        description="Get attachment content with text extraction for PDFs/docs",
        inputSchema={
            "type": "object",
            "properties": {
                "messagePk": {"type": "number", "description": "Message ID"},
                "attachmentIndex": {"type": "number", "description": "Attachment index (0-based)", "default": 0},
                "extractText": {"type": "boolean", "description": "Extract text from PDFs/docs", "default": True}
            },
            "required": ["messagePk"]
        }
    ),
    Tool(
        name="search_attachments",
        description="Search for emails with attachments",
        inputSchema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename pattern (use * as wildcard)"},
                "mimeType": {"type": "string", "description": "MIME type filter (e.g., application/pdf)"},
                "limit": {"type": "number", "description": "Max results", "default": 20}
            }
        }
    ),

    # PDF TOOLS
    Tool(
        name="get_pdf_form_fields",
        description="List fillable form fields in a PDF",
        inputSchema={
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Path to PDF file"}
            },
            "required": ["filePath"]
        }
    ),
    Tool(
        name="fill_pdf_form",
        description="Fill out form fields in a PDF and save",
        inputSchema={
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Path to source PDF"},
                "fields": {"type": "object", "description": "Field names mapped to values"},
                "outputPath": {"type": "string", "description": "Output path (default: ~/Downloads)"},
                "flatten": {"type": "boolean", "description": "Make fields non-editable", "default": False}
            },
            "required": ["filePath", "fields"]
        }
    ),
    Tool(
        name="sign_pdf",
        description="Add signature image to a PDF (uses configured default signature if not specified)",
        inputSchema={
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Path to source PDF"},
                "signatureImagePath": {"type": "string", "description": "Path to signature image (optional, uses default)"},
                "page": {"type": "number", "description": "Page number (1-indexed, -1 for last)", "default": -1},
                "x": {"type": "number", "description": "X position in points"},
                "y": {"type": "number", "description": "Y position in points"},
                "width": {"type": "number", "description": "Signature width in points", "default": 150},
                "outputPath": {"type": "string", "description": "Output path (default: ~/Downloads)"}
            },
            "required": ["filePath"]
        }
    ),
    Tool(
        name="fill_and_sign_pdf",
        description="Fill form fields and add signature in one step (uses configured default signature if not specified)",
        inputSchema={
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Path to source PDF"},
                "signatureImagePath": {"type": "string", "description": "Path to signature image (optional, uses default)"},
                "fields": {"type": "object", "description": "Field names mapped to values"},
                "page": {"type": "number", "description": "Signature page (1-indexed, -1 for last)", "default": -1},
                "x": {"type": "number", "description": "Signature X position"},
                "y": {"type": "number", "description": "Signature Y position"},
                "width": {"type": "number", "description": "Signature width", "default": 150},
                "outputPath": {"type": "string", "description": "Output path (default: ~/Downloads)"},
                "flatten": {"type": "boolean", "description": "Make fields non-editable", "default": False}
            },
            "required": ["filePath"]
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
                limit=int(arguments.get("limit", 20)),
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
                limit=int(arguments.get("limit", 10))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_transcript_statistics":
            result = db.get_statistics()
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        # EMAIL TOOLS
        elif name == "list_emails":
            result = db.list_emails(
                folder=arguments.get("folder", "inbox"),
                limit=int(arguments.get("limit", 20))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "search_emails":
            query = arguments.get("query")
            if not query:
                return [TextContent(type="text", text="Error: query required")]
            result = db.search_emails(
                query=query,
                limit=int(arguments.get("limit", 10))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_email":
            message_pk = arguments.get("messagePk")
            if not message_pk:
                return [TextContent(type="text", text="Error: messagePk required")]
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
                days_ahead=int(arguments.get("daysAhead", 1)),
                limit=int(arguments.get("limit", 20))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_event_details":
            event_pk = arguments.get("eventPk")
            if not event_pk:
                return [TextContent(type="text", text="Error: eventPk required")]
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
                return [TextContent(type="text", text="Error: eventPk required")]
            result = db.find_context_for_meeting(
                event_pk=int(event_pk),
                days_back=int(arguments.get("daysBack", 30))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        # ATTACHMENT TOOLS
        elif name == "list_attachments":
            message_pk = arguments.get("messagePk")
            if not message_pk:
                return [TextContent(type="text", text="Error: messagePk required")]
            result = db.list_attachments(int(message_pk))
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_attachment":
            message_pk = arguments.get("messagePk")
            if not message_pk:
                return [TextContent(type="text", text="Error: messagePk required")]
            result = db.get_attachment(
                message_pk=int(message_pk),
                attachment_index=int(arguments.get("attachmentIndex", 0)),
                extract_text=arguments.get("extractText", True)
            )
            if result is None:
                return [TextContent(type="text", text="Attachment not found")]
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "search_attachments":
            result = db.search_attachments(
                filename=arguments.get("filename"),
                mime_type=arguments.get("mimeType"),
                limit=int(arguments.get("limit", 20))
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        # PDF TOOLS
        elif name == "get_pdf_form_fields":
            file_path = arguments.get("filePath")
            if not file_path:
                return [TextContent(type="text", text="Error: filePath required")]
            result = pdf_ops.get_form_fields(file_path)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "fill_pdf_form":
            file_path = arguments.get("filePath")
            fields = arguments.get("fields")
            if not file_path:
                return [TextContent(type="text", text="Error: filePath required")]
            if not fields:
                return [TextContent(type="text", text="Error: fields required")]
            result = pdf_ops.fill_form(
                pdf_path=file_path,
                fields=fields,
                output_path=arguments.get("outputPath"),
                flatten=arguments.get("flatten", False)
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "sign_pdf":
            file_path = arguments.get("filePath")
            if not file_path:
                return [TextContent(type="text", text="Error: filePath required")]
            result = pdf_ops.add_signature(
                pdf_path=file_path,
                signature_image_path=arguments.get("signatureImagePath"),
                page=int(arguments.get("page", -1)),
                x=arguments.get("x"),
                y=arguments.get("y"),
                width=float(arguments.get("width", 150)),
                output_path=arguments.get("outputPath")
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "fill_and_sign_pdf":
            file_path = arguments.get("filePath")
            if not file_path:
                return [TextContent(type="text", text="Error: filePath required")]
            result = pdf_ops.fill_and_sign(
                pdf_path=file_path,
                signature_image_path=arguments.get("signatureImagePath"),
                fields=arguments.get("fields"),
                page=int(arguments.get("page", -1)),
                x=arguments.get("x"),
                y=arguments.get("y"),
                width=float(arguments.get("width", 150)),
                output_path=arguments.get("outputPath"),
                flatten=arguments.get("flatten", False)
            )
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
