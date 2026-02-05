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
        description="""List recent emails from a folder. PREFERRED for finding emails by sender or recent activity.

WHEN TO USE THIS vs search_emails:
- Use list_emails when looking for emails FROM a specific person (use sender filter)
- Use list_emails when looking for recent correspondence
- Use list_emails first to browse recent activity, then search_emails for specific content

This tool is more reliable than search_emails for finding threads by correspondent.""",
        inputSchema={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "inbox/sent/all", "default": "inbox"},
                "sender": {"type": "string", "description": "Filter by sender email or name (partial match)"},
                "limit": {"type": "number", "description": "Max results", "default": 20}
            }
        }
    ),
    Tool(
        name="search_emails",
        description="""Search email body content using SQLite FTS5 full-text search.

IMPORTANT - FTS5 BEHAVIOR:
- Multiple words are AND-ed: "Bittner NetApp" only matches if BOTH words appear in the email body
- This often FAILS for finding threads because names may be in headers/signatures, not body text
- If a multi-word search returns nothing, TRY EACH WORD SEPARATELY

SEARCH STRATEGY (do this in order):
1. First try list_emails with sender filter to find emails from a person
2. If searching for a topic/project, use a SINGLE distinctive keyword, not multiple words
3. If first search fails, try alternative terms (company name, project name, invoice number separately)
4. For phrases, use quotes: "exact phrase here"

EXAMPLES:
- Looking for "Bittner about NetApp"? First try list_emails with sender="bittner", OR search for just "NetApp"
- Looking for invoice #INV-123? Search for "INV-123" alone
- Multi-word searches like "John Smith project update" will likely fail - try "project update" or check sender="john.smith" """,
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms. Use single keywords for best results. Multiple words are AND-ed together."},
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
                "fields": {"type": "object", "description": "Text field names mapped to string values"},
                "checkboxes": {"type": "object", "description": "Checkbox field names mapped to boolean values"},
                "outputPath": {"type": "string", "description": "Output path (default: ~/Downloads)"},
                "flatten": {"type": "boolean", "description": "Make fields non-editable", "default": False}
            },
            "required": ["filePath"]
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
                "fields": {"type": "object", "description": "Text field names mapped to string values"},
                "checkboxes": {"type": "object", "description": "Checkbox field names mapped to boolean values"},
                "signatureField": {"type": "string", "description": "Form field name to place signature in (auto-positions)"},
                "page": {"type": "number", "description": "Signature page (1-indexed, -1 for last)", "default": -1},
                "x": {"type": "number", "description": "Signature X position in points from left"},
                "y": {"type": "number", "description": "Signature Y position (top of signature, fitz coords)"},
                "yFromTop": {"type": "number", "description": "Y position of signature LINE from top - signature bottom aligns here (preferred)"},
                "width": {"type": "number", "description": "Signature width", "default": 150},
                "outputPath": {"type": "string", "description": "Output path (default: ~/Downloads)"},
                "flatten": {"type": "boolean", "description": "Make fields non-editable", "default": False},
                "textAnnotations": {
                    "type": "array",
                    "description": "Text annotations for non-fillable blanks",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page": {"type": "number", "description": "Page number (1-indexed, -1 for last)"},
                            "text": {"type": "string", "description": "Text to add"},
                            "x": {"type": "number", "description": "X position in points from left"},
                            "y": {"type": "number", "description": "Y position in points from bottom (PDF coords)"},
                            "yFromTop": {"type": "number", "description": "Y position from top (PyMuPDF coords, preferred)"},
                            "fontSize": {"type": "number", "description": "Font size", "default": 12}
                        },
                        "required": ["page", "text", "x"]
                    }
                }
            },
            "required": ["filePath"]
        }
    ),

    # PDF ANNOTATION AND TEMPLATE TOOLS
    Tool(
        name="annotate_pdf",
        description="Add text annotations to any PDF at specified coordinates (works on PDFs without form fields)",
        inputSchema={
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Path to source PDF"},
                "annotations": {
                    "type": "array",
                    "description": "List of text annotations to add",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page": {"type": "number", "description": "Page number (1-indexed, -1 for last)"},
                            "text": {"type": "string", "description": "Text to add"},
                            "x": {"type": "number", "description": "X position in points from left"},
                            "y": {"type": "number", "description": "Y position in points from bottom (PDF coords)"},
                            "yFromTop": {"type": "number", "description": "Y position from top (PyMuPDF coords, preferred)"},
                            "fontSize": {"type": "number", "description": "Font size", "default": 12},
                            "fontFamily": {"type": "string", "description": "Font family", "default": "helv"},
                            "fontColor": {"type": "string", "description": "Hex color (e.g., '000000')", "default": "000000"}
                        },
                        "required": ["page", "text", "x"]
                    }
                },
                "outputPath": {"type": "string", "description": "Output path (default: ~/Downloads)"},
                "flatten": {"type": "boolean", "description": "Make annotations permanent", "default": False}
            },
            "required": ["filePath", "annotations"]
        }
    ),
    Tool(
        name="get_pdf_layout",
        description="Analyze PDF pages to find coordinates for annotations (helps position text on blank lines)",
        inputSchema={
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Path to PDF file"},
                "page": {"type": "number", "description": "Specific page (1-indexed, -1 for last, omit for all)"},
                "detectBlankLines": {"type": "boolean", "description": "Try to detect fill-in lines", "default": True}
            },
            "required": ["filePath"]
        }
    ),
    Tool(
        name="save_pdf_template",
        description="Save a reusable template for filling PDFs with annotations",
        inputSchema={
            "type": "object",
            "properties": {
                "templateName": {"type": "string", "description": "Name for the template (e.g., 'protective_order')"},
                "fields": {
                    "type": "array",
                    "description": "List of field definitions",
                    "items": {
                        "type": "object",
                        "properties": {
                            "fieldName": {"type": "string", "description": "Name for this field (e.g., 'name', 'address')"},
                            "page": {"type": "number", "description": "Page number (1-indexed, -1 for last)"},
                            "x": {"type": "number", "description": "X position in points"},
                            "y": {"type": "number", "description": "Y position in points from bottom"},
                            "fontSize": {"type": "number", "description": "Font size", "default": 12},
                            "type": {"type": "string", "enum": ["text", "signature", "date"], "default": "text"},
                            "width": {"type": "number", "description": "Width for signature fields", "default": 150}
                        },
                        "required": ["fieldName", "page", "x", "y"]
                    }
                },
                "description": {"type": "string", "description": "Description of the template"}
            },
            "required": ["templateName", "fields"]
        }
    ),
    Tool(
        name="list_pdf_templates",
        description="List all saved PDF templates",
        inputSchema={"type": "object", "properties": {}}
    ),
    Tool(
        name="delete_pdf_template",
        description="Delete a saved PDF template",
        inputSchema={
            "type": "object",
            "properties": {
                "templateName": {"type": "string", "description": "Name of template to delete"}
            },
            "required": ["templateName"]
        }
    ),
    Tool(
        name="fill_from_template",
        description="Fill a PDF using a saved template",
        inputSchema={
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Path to source PDF"},
                "templateName": {"type": "string", "description": "Name of the saved template"},
                "values": {
                    "type": "object",
                    "description": "Field values (use 'auto' for date fields to insert current date)"
                },
                "sign": {"type": "boolean", "description": "Add signature to signature fields", "default": False},
                "signatureImagePath": {"type": "string", "description": "Path to signature image (optional)"},
                "outputPath": {"type": "string", "description": "Output path (default: ~/Downloads)"}
            },
            "required": ["filePath", "templateName", "values"]
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
                sender=arguments.get("sender"),
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
            checkboxes = arguments.get("checkboxes")
            if not file_path:
                return [TextContent(type="text", text="Error: filePath required")]
            if not fields and not checkboxes:
                return [TextContent(type="text", text="Error: fields or checkboxes required")]
            result = pdf_ops.fill_form(
                pdf_path=file_path,
                fields=fields,
                checkboxes=checkboxes,
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
                checkboxes=arguments.get("checkboxes"),
                page=int(arguments.get("page", -1)),
                x=arguments.get("x"),
                y=arguments.get("y"),
                y_from_top=arguments.get("yFromTop"),
                width=float(arguments.get("width", 150)),
                output_path=arguments.get("outputPath"),
                flatten=arguments.get("flatten", False),
                signature_field=arguments.get("signatureField"),
                text_annotations=arguments.get("textAnnotations")
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        # PDF ANNOTATION AND TEMPLATE TOOLS
        elif name == "annotate_pdf":
            file_path = arguments.get("filePath")
            annotations = arguments.get("annotations")
            if not file_path:
                return [TextContent(type="text", text="Error: filePath required")]
            if not annotations:
                return [TextContent(type="text", text="Error: annotations required")]
            result = pdf_ops.annotate_pdf(
                pdf_path=file_path,
                annotations=annotations,
                output_path=arguments.get("outputPath"),
                flatten=arguments.get("flatten", False)
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_pdf_layout":
            file_path = arguments.get("filePath")
            if not file_path:
                return [TextContent(type="text", text="Error: filePath required")]
            result = pdf_ops.get_pdf_layout(
                pdf_path=file_path,
                page=arguments.get("page"),
                detect_blank_lines=arguments.get("detectBlankLines", True)
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "save_pdf_template":
            template_name = arguments.get("templateName")
            fields = arguments.get("fields")
            if not template_name:
                return [TextContent(type="text", text="Error: templateName required")]
            if not fields:
                return [TextContent(type="text", text="Error: fields required")]
            result = pdf_ops.save_pdf_template(
                template_name=template_name,
                fields=fields,
                description=arguments.get("description")
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "list_pdf_templates":
            result = pdf_ops.list_pdf_templates()
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "delete_pdf_template":
            template_name = arguments.get("templateName")
            if not template_name:
                return [TextContent(type="text", text="Error: templateName required")]
            result = pdf_ops.delete_pdf_template(template_name)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "fill_from_template":
            file_path = arguments.get("filePath")
            template_name = arguments.get("templateName")
            values = arguments.get("values")
            if not file_path:
                return [TextContent(type="text", text="Error: filePath required")]
            if not template_name:
                return [TextContent(type="text", text="Error: templateName required")]
            if not values:
                return [TextContent(type="text", text="Error: values required")]
            result = pdf_ops.fill_from_template(
                pdf_path=file_path,
                template_name=template_name,
                values=values,
                sign=arguments.get("sign", False),
                signature_image_path=arguments.get("signatureImagePath"),
                output_path=arguments.get("outputPath")
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
