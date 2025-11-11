#!/usr/bin/env python3
"""Quick test script to verify the Spark MCP server is working."""

from spark_mcp.database import SparkDatabase

def main():
    """Test database access."""
    print("Testing Spark MCP Server...")
    print("=" * 60)

    # Initialize database
    try:
        db = SparkDatabase()
        print("✓ Successfully connected to Spark databases")
    except Exception as e:
        print(f"✗ Failed to connect: {e}")
        return

    # Get statistics
    print("\nFetching statistics...")
    stats = db.get_statistics()

    print(f"\nTranscript Summary:")
    print(f"  Total transcripts: {stats['totalTranscripts']}")
    print(f"  Calendar meetings: {stats['calendarMeetings']}")
    print(f"  Ad-hoc meetings: {stats['adHocMeetings']}")
    print(f"  Kept transcripts: {stats['keptTranscripts']}")
    print(f"  With full text: {stats['withFullText']}")

    print(f"\nDate Range:")
    print(f"  Earliest: {stats['dateRange']['earliest']}")
    print(f"  Latest: {stats['dateRange']['latest']}")

    if stats['topSenders']:
        print(f"\nTop Senders:")
        for sender in stats['topSenders'][:5]:
            print(f"  {sender['email']}: {sender['count']} transcripts")

    # List recent transcripts
    print("\n" + "=" * 60)
    print("Recent Transcripts:")
    print("=" * 60)

    result = db.list_transcripts(limit=5)
    for t in result['transcripts']:
        print(f"\n[{t['messagePk']}] {t['subject']}")
        print(f"  From: {t['sender']}")
        print(f"  Date: {t['receivedDate']}")
        print(f"  Type: {'Calendar' if t['isCalendarEvent'] else 'Ad-hoc'}")
        print(f"  Text: {t['textLength']} chars")

    print("\n" + "=" * 60)
    print("✓ All tests passed! Server is ready to use.")
    print("\nNext steps:")
    print("1. Add to Claude Desktop config (see README.md)")
    print("2. Restart Claude Desktop")
    print("3. Ask Claude to use the Spark tools!")

if __name__ == "__main__":
    main()
