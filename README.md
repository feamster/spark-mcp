# Spark MCP Server

MCP server for accessing Spark Desktop meeting transcripts and emails through the Model Context Protocol.

## Features

- 📝 Access all meeting transcripts (calendar-based and ad-hoc)
- 🔍 Full-text search across transcript content
- 📊 Statistics and analytics about your transcripts
- 🔒 Read-only access - safe and non-destructive
- ⚡ Fast local SQLite queries - no network required
- 🎯 Captures ad-hoc meetings (primary use case)

## Requirements

- macOS (Spark Desktop must be installed)
- Python 3.10+
- Spark Desktop for macOS (App Store version)

## Installation

```bash
# Install in development mode
pip install -e .
```

## Usage

### With Claude Desktop

Add to your Claude Desktop MCP settings (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "spark": {
      "command": "python",
      "args": ["-m", "spark_mcp.server"],
      "cwd": "/Users/feamster/src/spark-mcp"
    }
  }
}
```

Or if installed via pip:

```json
{
  "mcpServers": {
    "spark": {
      "command": "spark-mcp"
    }
  }
}
```

Restart Claude Desktop, and the tools will be available.

### Standalone Testing

```bash
# Run the server (communicates via stdio)
python -m spark_mcp.server
```

## Available Tools

### 1. `list_meeting_transcripts`

List meeting transcripts with metadata.

**Parameters:**
- `startDate` (optional): Filter transcripts after this ISO date
- `endDate` (optional): Filter transcripts before this ISO date
- `includeAdHoc` (optional, default: true): Include ad-hoc meetings
- `onlyKept` (optional, default: true): Only show kept transcripts
- `limit` (optional, default: 50): Max results
- `offset` (optional, default: 0): Pagination offset

**Example:**
```json
{
  "startDate": "2025-11-01",
  "limit": 10
}
```

**Returns:**
```json
{
  "transcripts": [
    {
      "messagePk": 63336,
      "subject": "Prior Art Review for Patent Claims 416 and 571",
      "sender": "example@example.com",
      "receivedDate": "2025-11-11 15:59:12",
      "meetingStartDate": "2025-11-11T15:00:00.000Z",
      "meetingEndDate": "2025-11-11T16:00:00.000Z",
      "transcriptId": "-8929133086933914113",
      "isCalendarEvent": false,
      "eventSummary": null,
      "textLength": 29893,
      "hasFullText": true
    }
  ],
  "total": 228
}
```

### 2. `get_meeting_transcript`

Get full transcript content.

**Parameters:**
- `messagePk` (optional): Message primary key from list
- `transcriptId` (optional): Transcript ID (mtid)

**Example:**
```json
{
  "messagePk": 63336
}
```

**Returns:**
```json
{
  "messagePk": 63336,
  "subject": "Prior Art Review for Patent Claims 416 and 571",
  "sender": "example@example.com",
  "recipients": "recipient@example.com",
  "receivedDate": "2025-11-11 15:59:12",
  "meetingStartDate": "2025-11-11T15:00:00.000Z",
  "meetingEndDate": "2025-11-11T16:00:00.000Z",
  "transcriptId": "-8929133086933914113",
  "fullText": "the meeting focused on reviewing prior art for patent claims...",
  "metadata": {
    "language": "auto",
    "status": true,
    "autoProcessed": true,
    "isKept": true,
    "eventSummary": null
  }
}
```

### 3. `search_meeting_transcripts`

Full-text search across transcripts.

**Parameters:**
- `query` (required): Search query (FTS5 syntax supported)
- `startDate` (optional): Filter after this date
- `endDate` (optional): Filter before this date
- `limit` (optional, default: 20): Max results
- `includeContext` (optional, default: true): Include highlighted excerpts

**Example:**
```json
{
  "query": "neural network AND security",
  "limit": 5
}
```

**FTS5 Query Syntax:**
- `word1 AND word2` - Both words must be present
- `word1 OR word2` - Either word present
- `NOT word` - Exclude word
- `"exact phrase"` - Exact phrase match
- `word*` - Prefix match

**Returns:**
```json
{
  "results": [
    {
      "messagePk": 62642,
      "subject": "Meeting Summary",
      "sender": "example@example.com",
      "receivedDate": "2025-11-10 23:04:38",
      "excerpt": "...discussing <mark>neural network</mark> architectures for <mark>security</mark> applications...",
      "relevanceScore": 1.5
    }
  ],
  "total": 5
}
```

### 4. `get_transcript_statistics`

Get overview statistics.

**Parameters:** None

**Returns:**
```json
{
  "totalTranscripts": 233,
  "calendarMeetings": 37,
  "adHocMeetings": 196,
  "keptTranscripts": 228,
  "deletedTranscripts": 5,
  "withFullText": 225,
  "dateRange": {
    "earliest": "2024-09-01 10:00:00",
    "latest": "2025-11-11 15:59:12"
  },
  "topSenders": [
    {
      "email": "colleague@example.com",
      "count": 45
    }
  ]
}
```

## Data Sources

### Databases Used

1. **`messages.sqlite`** - Transcript metadata
   - Location: `~/Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application Support/Spark Desktop/core-data/messages.sqlite`
   - Tables: `messages`, `meetTranscriptEvent`
   - Size: ~178 MB

2. **`search_fts5.sqlite`** - Full transcript text
   - Location: `~/Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application Support/Spark Desktop/core-data/search_fts5.sqlite`
   - Table: `messagesfts` (FTS5 full-text index)
   - Size: ~232 MB

### Transcript Types

**Calendar-Based Meetings (37 transcripts):**
- Scheduled meetings with calendar event info
- Stored in `meetTranscriptEvent` table
- Have `eventSummary` field

**Ad-Hoc Meetings (196 transcripts):**
- User-initiated transcriptions
- Not linked to calendar events
- This is the primary use case for most users

**Total: 228 kept transcripts** (233 including deleted)

## Safety Features

- ✅ Read-only database access
- ✅ No writes or modifications
- ✅ Graceful handling of schema changes
- ✅ Safe concurrent access with Spark

## Troubleshooting

### "Failed to connect to Spark databases"

1. Verify Spark Desktop is installed (App Store version)
2. Check database paths exist:
```bash
ls -la ~/Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application\ Support/Spark\ Desktop/core-data/
```

### No transcripts found

- Make sure you have meeting transcripts in Spark
- Check that transcripts are marked as "kept" (not deleted)
- Try running `get_transcript_statistics` to see counts

### Empty transcript text

Some transcripts may not have full text cached locally:
- Recent transcripts may still be syncing
- Deleted transcripts have no content
- Check `hasFullText` field in list results

## Development

```bash
# Install in development mode
pip install -e .

# Run tests (if you add them)
pytest
```

## Future Enhancements

See `PLAN.md` for detailed roadmap, including:
- General email search and processing
- Alternative data access methods (API, IMAP)
- Additional analytics and insights
- Export capabilities

## License

MIT
