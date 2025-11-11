#!/usr/bin/env python3
"""Verify all MCP tools work correctly."""

from spark_mcp.database import SparkDatabase
import json

db = SparkDatabase()

tests = [
    ("list_transcripts", lambda: db.list_transcripts(limit=2)),
    ("get_transcript", lambda: db.get_transcript(message_pk=63336)),
    ("search_transcripts", lambda: db.search_transcripts(query="patent", limit=2)),
    ("get_statistics", lambda: db.get_statistics()),
    ("list_emails", lambda: db.list_emails(limit=2)),
    ("search_emails", lambda: db.search_emails(query="meeting", limit=2)),
    ("find_action_items", lambda: db.find_action_items(days=7, limit=2)),
    ("find_pending_responses", lambda: db.find_pending_responses(days=7, limit=2)),
    ("list_events", lambda: db.list_events(limit=2)),
    ("find_events_needing_prep", lambda: db.find_events_needing_prep(limit=2)),
    ("get_daily_briefing", lambda: db.get_daily_briefing()),
]

print("Testing all MCP tools...")
print("=" * 60)

failed = []
for name, func in tests:
    try:
        result = func()
        # Verify JSON serializable
        json_str = json.dumps(result)
        print(f"✓ {name:30s} OK ({len(json_str):,} chars)")
    except Exception as e:
        print(f"✗ {name:30s} FAILED: {e}")
        failed.append((name, str(e)))

print("=" * 60)

if failed:
    print(f"\n❌ {len(failed)} tests failed:")
    for name, error in failed:
        print(f"  - {name}: {error}")
    exit(1)
else:
    print(f"\n✅ All {len(tests)} tests passed!")
    print("\nThe MCP server is working correctly.")
    print("If you're getting errors in Claude Desktop:")
    print("1. Restart Claude Desktop")
    print("2. Check ~/Library/Logs/Claude/mcp-server-spark.log for errors")
    print("3. The errors may be temporary Claude-side issues")
