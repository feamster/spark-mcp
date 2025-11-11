# Spark MCP Server - Implementation Plan

## Executive Summary

Found **228-233 meeting transcripts** stored locally in Spark's SQLite databases, with full text content cached and accessible. Building an MCP server to query these transcripts without needing API tokens or network access.

## Key Findings

### Transcript Storage Overview

**Total Transcripts:** 233 messages with transcript metadata
- **228 kept transcripts** (`mtskp = 1` flag)
- **37 calendar-based meetings** (stored in `meetTranscriptEvent` table with event metadata)
- **196 ad-hoc meetings** (transcripts without calendar events - THIS IS THE PRIMARY USE CASE)
- **5 deleted/not-kept** transcripts

**Full Text Availability:**
- Most transcripts have full text cached in FTS database
- Text lengths: 0 to 50,299 characters
- Average substantial transcript: 15,000-30,000 characters
- Some recent transcripts may be empty (not yet synced/cached)

## Database Architecture

### Database 1: `messages.sqlite` (Metadata)
**Location:**
```
~/Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application Support/Spark Desktop/core-data/messages.sqlite
```

**Size:** 178 MB

**Key Tables:**

#### `messages` - Primary transcript metadata
```sql
CREATE TABLE messages (
  pk INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
  accountPk INTEGER NOT NULL,
  messageType INTEGER NOT NULL,
  creationDate INTEGER NOT NULL,
  receivedDate INTEGER NOT NULL,
  messageId TEXT,
  messageFrom TEXT,
  messageTo TEXT,
  subject TEXT,
  meta TEXT,  -- JSON with transcript metadata
  ...
);
```

**Transcript Identification:**
- Messages with `meta LIKE '%mtid%'` are transcripts
- `meta` field contains JSON with transcript-specific fields

**Metadata JSON Structure:**
```json
{
  "mtid": "-8929133086933914113",           // Transcript ID
  "mtsd": 1762876740000,                    // Meeting start date (ms timestamp)
  "mted": 1762880691757,                    // Meeting end date (ms timestamp)
  "mtsl": "auto",                           // Transcript language
  "mtss": true,                             // Transcript status (true = successful)
  "mtskp": 1,                               // Keep transcript flag (1 = kept, null = deleted)
  "mtsap": true,                            // Auto-process flag
  "mtsdtl": false,                          // Detail flag
  "url": "/api/v1/collab/mails/manual/...", // Internal API endpoint
  "mtes": "Meeting Title",                  // Event summary (only for calendar meetings)
  "mtesd": 1756400400000                    // Event start date (only for calendar meetings)
}
```

**Sample Transcripts Found:**
- "Prior Art Review for Patent Claims 416 and 571" (Nov 11, 2025)
- "AI, Internet, and Digital Literacy: Transformations, Challenges, and Opportunities" (Nov 11, 2025)
- "Claim Construction and Prior Art Review Strategy" (Nov 10, 2025)
- "Expert Witness Engagement for Teen Mental Health Litigation" (Nov 7, 2025)
- "Hardware Acceleration Overhead Analysis" (Nov 10, 2025)
- Many "Meeting Summary" entries (ad-hoc meetings)
- "Ad-Hoc Meeting" entries (user-initiated transcriptions)

#### `meetTranscriptEvent` - Calendar meeting metadata (ONLY 37 entries)
```sql
CREATE TABLE meetTranscriptEvent (
  summary TEXT NOT NULL,       -- Calendar event title
  startDate INTEGER NOT NULL,  -- Event start timestamp
  messagePk INTEGER NOT NULL   -- FK to messages.pk
);
```

**Important:** This table only contains transcripts from calendar events. The majority of transcripts (196) are ad-hoc and NOT in this table. Must query `messages` table directly for complete coverage.

#### Other Relevant Tables
- `conversations` - Email thread information
- `messageAttachment` - File attachments
- `contacts` - Contact information
- `accounts` - Email account details

### Database 2: `search_fts5.sqlite` (Full Text)
**Location:**
```
~/Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application Support/Spark Desktop/core-data/search_fts5.sqlite
```

**Size:** 232 MB

**Key Table:**

#### `messagesfts` - Full-text search index with transcript content
```sql
CREATE VIRTUAL TABLE messagesfts USING fts5(
  messagePk,      -- FK to messages.pk
  messageFrom,    -- Sender email
  messageTo,      -- Recipient emails
  subject,        -- Email subject
  searchBody,     -- FULL TRANSCRIPT TEXT stored here!
  additionalText  -- Extra searchable text
);
```

**Critical Finding:** The `searchBody` column contains the **complete transcript text** for most transcripts!

**Sample Content:**
```
the meeting addressed ai's impact on digital literacy, ethical concerns
in data usage, and the implications of zero-rating and measurement
techniques on internet access and user privacy. key points ai and
internet interrelations ai models rely on internet data for learning
and updates, reshaping web content. • ai-driven summaries reduce
direct web browsing, impacting publishers' traffic...
```

**Text Length Statistics (sample of 20 recent transcripts):**
- Longest: 50,299 characters
- Average: ~20,000 characters
- Empty: 3 transcripts (not yet cached or deleted)

### Database 3: `cache.sqlite` (Body Cache)
**Location:**
```
~/Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application Support/Spark Desktop/core-data/cache.sqlite
```

**Size:** 1.5 GB

**Key Tables:**
- `messageBodyParsedData` - Parsed email bodies (BLOB format)
- `messageBodyHtml` - HTML email content
- Contains 31,895 cached messages

**Note:** FTS database is easier to work with for transcripts since text is already extracted.

## Implementation Approaches

### ✅ Approach 1: SQLite + FTS (RECOMMENDED - IMPLEMENTING THIS)

**Rationale:**
- ✅ Fastest - no network latency
- ✅ No authentication required
- ✅ Read-only access - safe
- ✅ Full text already extracted in FTS database
- ✅ Complete coverage of all 228 transcripts
- ✅ Works offline
- ✅ Captures ad-hoc meetings (primary use case)

**Architecture:**
```
MCP Server
    ↓
├── messages.sqlite (metadata)
│   ├── Query messages WHERE meta LIKE '%mtid%'
│   ├── Extract JSON from meta field
│   └── Get subject, sender, dates
│
└── search_fts5.sqlite (full text)
    ├── Query messagesfts WHERE messagePk IN (...)
    └── Return searchBody field
```

**Implementation:**
- Use `better-sqlite3` for Node.js SQLite access
- Read-only mode for safety
- Join data from both databases in memory
- Support filtering, searching, date ranges

**Limitations:**
- Depends on Spark's database schema (may change with updates)
- Some recent transcripts may not be cached yet
- Text is extracted/summarized (may not be 100% verbatim)

### Approach 2: Internal API (PRESERVED OPTION)

**Rationale:**
- May provide more complete/authoritative content
- Potentially includes formatting, speakers, etc.
- Direct from Spark's server

**Challenges:**
- Requires authentication/session tokens
- Network latency
- May require intercepting Spark's auth
- API is internal/undocumented
- Could break with updates

**API Endpoints Found:**
```
/api/v1/collab/mails/manual/{accountId}/{mailboxId}/{transcriptUuid}
```

**Implementation Would Require:**
1. Extract session tokens from Spark
2. Make authenticated HTTP requests
3. Parse response format (unknown)
4. Handle auth refresh

**When to Consider:**
- If FTS text quality is insufficient
- Need real-time access to new transcripts
- Want speaker attribution or timing data

### Approach 3: IMAP Fallback (PRESERVED OPTION)

**Rationale:**
- Independent of Spark
- More stable long-term
- Works with any email client

**Challenges:**
- Network latency
- Requires IMAP setup
- Missing Spark-specific metadata
- Transcripts stored as email attachments or HTML

**Implementation:**
1. Connect to IMAP server
2. Search for emails from Spark/transcript service
3. Parse email content
4. Extract transcript text

**When to Consider:**
- Spark schema changes significantly
- Need access from non-Mac systems
- Want to archive transcripts independently

### Approach 4: IndexedDB/LevelDB (INVESTIGATED)

**Location:**
```
~/Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application Support/Spark Desktop/IndexedDB/app_meeting-notes-renderer_0.indexeddb.leveldb/
```

**Size:** 116 KB (very small)

**Status:**
- Contains LevelDB format data
- Appears to be UI cache, not main storage
- Much smaller than expected for full transcripts
- FTS database is superior for our use case

**When to Consider:**
- If FTS database stops being updated
- Need additional metadata not in SQLite

## MCP Server Implementation

### Tools to Expose

#### 1. `list_meeting_transcripts`
**Purpose:** List all available transcripts with metadata

**Parameters:**
```typescript
{
  startDate?: string,      // ISO date filter
  endDate?: string,        // ISO date filter
  includeAdHoc?: boolean,  // Include non-calendar meetings (default: true)
  onlyKept?: boolean,      // Only kept transcripts (default: true)
  limit?: number,          // Max results (default: 50)
  offset?: number          // Pagination offset
}
```

**Returns:**
```typescript
{
  transcripts: [{
    messagePk: number,
    subject: string,
    sender: string,
    receivedDate: string,
    meetingStartDate: string | null,
    meetingEndDate: string | null,
    transcriptId: string,
    isCalendarEvent: boolean,
    eventSummary: string | null,
    textLength: number,
    hasFullText: boolean
  }],
  total: number
}
```

#### 2. `get_meeting_transcript`
**Purpose:** Get full transcript content

**Parameters:**
```typescript
{
  messagePk: number,
  // OR
  transcriptId?: string
}
```

**Returns:**
```typescript
{
  messagePk: number,
  subject: string,
  sender: string,
  recipients: string,
  receivedDate: string,
  meetingStartDate: string | null,
  meetingEndDate: string | null,
  transcriptId: string,
  fullText: string,
  metadata: {
    language: string,
    status: boolean,
    autoProcessed: boolean,
    isKept: boolean,
    eventSummary?: string
  }
}
```

#### 3. `search_meeting_transcripts`
**Purpose:** Full-text search across all transcripts

**Parameters:**
```typescript
{
  query: string,           // Search query
  startDate?: string,      // Filter by date range
  endDate?: string,
  limit?: number,          // Max results (default: 20)
  includeContext?: boolean // Include surrounding text
}
```

**Returns:**
```typescript
{
  results: [{
    messagePk: number,
    subject: string,
    sender: string,
    receivedDate: string,
    excerpt: string,        // Matched text with context
    relevanceScore: number
  }],
  total: number
}
```

#### 4. `get_transcript_statistics`
**Purpose:** Get overview of transcript collection

**Returns:**
```typescript
{
  totalTranscripts: number,
  calendarMeetings: number,
  adHocMeetings: number,
  keptTranscripts: number,
  deletedTranscripts: number,
  withFullText: number,
  dateRange: {
    earliest: string,
    latest: string
  },
  topSenders: [{
    email: string,
    count: number
  }]
}
```

### Future: Email Processing Tools (DOCUMENTED FOR LATER)

The same databases can be used for general email processing:

#### Potential Tools:
- `search_emails` - Search all email content
- `get_email_thread` - Get conversation threads
- `list_contacts` - Get contact information
- `get_email_attachments` - List/retrieve attachments
- `analyze_email_patterns` - Usage analytics

**All tables available:**
- `messages` - All emails (not just transcripts)
- `conversations` - Email threads
- `messageAttachment` - Attachments metadata
- `contacts` - Contact database
- `accounts` - Email account info
- `folders` - Folder structure

## Technical Specifications

### Dependencies
```json
{
  "better-sqlite3": "^11.0.0",  // Fast SQLite3 with sync API
  "@modelcontextprotocol/sdk": "latest"
}
```

### Database Paths (macOS)
```typescript
const SPARK_BASE = "~/Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application Support/Spark Desktop";

const DB_PATHS = {
  messages: `${SPARK_BASE}/core-data/messages.sqlite`,
  searchFts: `${SPARK_BASE}/core-data/search_fts5.sqlite`,
  cache: `${SPARK_BASE}/core-data/cache.sqlite`,
  settings: `${SPARK_BASE}/core-data/settings.sqlite`,
  calendars: `${SPARK_BASE}/core-data/calendarsapi.sqlite`
};
```

### Safety Considerations
- ✅ **Read-only access** - Use `readonly: true` in better-sqlite3
- ✅ **No modifications** - Never write to Spark's databases
- ✅ **Handle schema changes** - Graceful degradation if schema changes
- ✅ **Privacy** - User's personal email data - handle responsibly
- ⚠️ **Concurrent access** - Spark may lock databases while running
- ⚠️ **Database format** - SQLite WAL mode - need to read correctly

### Performance Optimization
- Use prepared statements for repeated queries
- Index-based lookups (messagePk is indexed)
- Limit result sets by default
- Consider caching frequently accessed transcripts
- FTS5 queries are optimized for full-text search

## Query Examples

### Get all transcripts with full metadata
```sql
-- From messages.sqlite
SELECT
  pk as messagePk,
  subject,
  messageFrom as sender,
  datetime(receivedDate, 'unixepoch') as receivedDate,
  json_extract(meta, '$.mtid') as transcriptId,
  json_extract(meta, '$.mtsd') as meetingStartMs,
  json_extract(meta, '$.mted') as meetingEndMs,
  json_extract(meta, '$.mtes') as eventSummary,
  json_extract(meta, '$.mtsl') as language,
  json_extract(meta, '$.mtss') as status,
  json_extract(meta, '$.mtskp') as isKept
FROM messages
WHERE meta LIKE '%mtid%'
  AND json_extract(meta, '$.mtskp') = 1
ORDER BY receivedDate DESC;
```

### Get full transcript text
```sql
-- From search_fts5.sqlite
SELECT
  messagePk,
  searchBody as fullText,
  length(searchBody) as textLength
FROM messagesfts
WHERE messagePk = ?;
```

### Search across transcripts
```sql
-- From search_fts5.sqlite
SELECT
  messagePk,
  snippet(messagesfts, 4, '<mark>', '</mark>', '...', 50) as excerpt,
  rank
FROM messagesfts
WHERE searchBody MATCH ?
ORDER BY rank
LIMIT 20;
```

### Get statistics
```sql
-- From messages.sqlite
SELECT
  COUNT(*) as total,
  SUM(CASE WHEN json_extract(meta, '$.mtes') IS NOT NULL THEN 1 ELSE 0 END) as calendarMeetings,
  SUM(CASE WHEN json_extract(meta, '$.mtes') IS NULL THEN 1 ELSE 0 END) as adHocMeetings,
  SUM(CASE WHEN json_extract(meta, '$.mtskp') = 1 THEN 1 ELSE 0 END) as kept
FROM messages
WHERE meta LIKE '%mtid%';
```

## Schema Change Mitigation

If Spark updates their database schema:

1. **Detection:** Check for expected tables/columns on startup
2. **Graceful degradation:** Fall back to basic functionality
3. **User notification:** Warn user of schema mismatch
4. **Fallback options:** Switch to API or IMAP approach
5. **Version tracking:** Log Spark version in use

## Next Steps

1. ✅ **Implement MCP server** using SQLite + FTS approach
2. ✅ **Expose 4 core tools** for transcript access
3. **Test with real data** from your Spark database
4. **Add error handling** for missing/corrupt data
5. **Document usage** with examples
6. **Consider future:** Email processing tools

## Success Metrics

- ✅ Access all 228 kept transcripts
- ✅ Retrieve full text for transcripts with >0 length
- ✅ Search across all transcript content
- ✅ Sub-second query performance
- ✅ No Spark database corruption
- ✅ Works offline

## MCP Server Development Best Practices (Lessons Learned)

### Critical Issues Encountered and Solutions

#### 1. **Stdio Contamination** (CRITICAL - Server Won't Connect)

**Problem:** The MCP protocol uses stdio (stdin/stdout) for JSON-RPC communication. ANY output to stdout/stderr breaks the protocol.

**Symptoms:**
- Claude Desktop shows "this isn't working right now" errors
- Server appears to start in logs but tools don't work
- No error details provided to user

**Root Cause:**
```python
# ❌ THIS BREAKS MCP PROTOCOL
try:
    db = SparkDatabase()
except Exception as e:
    print(f"Error: Failed to connect: {e}", flush=True)  # BREAKS STDIO!
    raise
```

**Solution:**
```python
# ✅ Let MCP framework handle errors
db = SparkDatabase()  # Errors logged by MCP framework
```

**Key Takeaways:**
- NEVER use `print()`, `console.log()`, or any stdout/stderr output
- MCP framework will log errors automatically
- Use proper logging libraries if needed (configure to file output)
- Test server initialization without any console output

#### 2. **Tool Description Bloat** (Performance Issue)

**Problem:** Verbose tool descriptions and parameter documentation caused performance issues in Claude Desktop.

**Before (5000+ characters):**
```python
Tool(
    name="list_meeting_transcripts",
    description=(
        "List meeting transcripts with metadata. Returns transcripts sorted by date (newest first). "
        "Supports filtering by date range and type. Includes both calendar-based meetings and ad-hoc transcriptions. "
        "Use this tool when you need to see available transcripts..."  # etc etc
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "startDate": {
                "type": "string",
                "description": "Filter transcripts after this ISO date (e.g., '2025-01-01'). Use ISO 8601 format..."
            },
            # ... 6 more verbose parameters
        }
    }
)
```

**After (1290 characters total for all 4 tools):**
```python
Tool(
    name="list_meeting_transcripts",
    description="List recent meeting transcripts",
    inputSchema={
        "type": "object",
        "properties": {
            "limit": {"type": "number", "description": "Max results (default: 10)", "default": 10}
        }
    }
)
```

**Key Takeaways:**
- Keep descriptions under 10 words
- Remove usage examples from descriptions
- Minimize parameters (only what's essential)
- Total tool definitions should be <2000 chars
- Claude can figure out how to use tools from minimal descriptions

#### 3. **Response Size Management** (Timeout/Performance Issue)

**Problem:** Large default limits caused timeouts and performance issues.

**Before:**
- `list_transcripts`: default 50 results
- `search_transcripts`: default 20 results
- `get_transcript`: could return 30KB+ of text
- Total response size: up to 100KB+

**After:**
- `list_transcripts`: default 10 results
- `search_transcripts`: default 5 results
- Response sizes: typically <5KB

**Key Takeaways:**
- Start with SMALL limits (5-10 results)
- Users can request more if needed
- Large responses cause:
  - Network timeouts
  - Claude processing delays
  - UI lag in Claude Desktop
- Better to make multiple small requests than one huge one

#### 4. **Database Query Timeouts** (Reliability Issue)

**Problem:** Queries could hang indefinitely, causing tool calls to timeout.

**Solution:**
```python
# ✅ Add timeouts to all database connections
conn = sqlite3.connect(
    f"file:{db_path}?mode=ro",
    uri=True,
    timeout=5.0  # 5 second timeout
)
conn.execute("PRAGMA query_only = ON")  # Extra safety
```

**Key Takeaways:**
- ALWAYS set database timeouts (5 seconds is good)
- Use `PRAGMA query_only = ON` for read-only safety
- Test with slow queries to verify timeout behavior
- Better to fail fast than hang forever

#### 5. **Parameter Complexity** (Usability Issue)

**Problem:** Too many optional parameters confused Claude and caused errors.

**Before:**
```python
def list_transcripts(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_ad_hoc: bool = True,
    only_kept: bool = True,
    limit: int = 50,
    offset: int = 0
) -> Dict[str, Any]:
```

**After:**
```python
def list_transcripts(
    limit: int = 10,
    only_kept: bool = True
) -> Dict[str, Any]:
```

**Key Takeaways:**
- Minimize optional parameters
- Use sensible defaults for everything
- Required parameters should be truly required
- Claude struggles with complex parameter combinations
- Simpler APIs = fewer errors

### Development Workflow Best Practices

#### Testing Checklist

Before deploying MCP server changes:

1. **Static checks:**
   ```bash
   grep -r "print(" *.py  # Should return nothing
   ```

2. **Local testing:**
   ```bash
   python test_server.py  # Verify database queries work
   ```

3. **Response size check:**
   ```python
   result = db.list_transcripts(limit=10)
   print(len(json.dumps(result)))  # Should be <10KB
   ```

4. **Restart Claude Desktop:**
   - Always restart after config changes
   - Check logs: `~/Library/Logs/Claude/mcp-server-spark.log`
   - Verify tools appear in Claude UI

5. **Test tool calls:**
   - Start with simplest tool (statistics)
   - Then list with small limit
   - Then search with simple query
   - Finally get full transcript

#### When Claude Desktop Shows Errors

**Error: "this isn't working right now"**

Possible causes (in order of likelihood):
1. ✅ **Stdio contamination** - Check for print statements
2. ✅ **Initialization failure** - Check server logs
3. ✅ **Tool description too large** - Simplify descriptions
4. ⚠️ **Response too large** - Reduce default limits
5. ⚠️ **Query timeout** - Add database timeouts
6. ⚠️ **Claude Desktop issue** - Try disabling all MCP servers

**Debugging steps:**
1. Check logs: `tail -f ~/Library/Logs/Claude/mcp-server-spark.log`
2. Look for "Server started and connected successfully"
3. Look for "Message from client" / "Message from server"
4. If no messages, check stdio contamination
5. If messages but errors, check response sizes

### Design Principles for MCP Servers

1. **Simplicity First**
   - Start with 2-3 tools maximum
   - Add complexity only when needed
   - Each tool should do ONE thing well

2. **Performance Matters**
   - Keep responses small (<10KB typical)
   - Add timeouts everywhere (5 seconds)
   - Test with realistic data volumes

3. **Silent Operation**
   - No stdout/stderr output EVER
   - Let MCP framework handle logging
   - Errors should be returned as tool results

4. **Fail Gracefully**
   - Return error messages as strings
   - Don't raise exceptions for expected failures
   - Provide helpful error messages

5. **User-Centric**
   - Minimal required parameters
   - Sensible defaults for everything
   - Clear, brief tool descriptions

### Example: Minimal Working Tool

```python
Tool(
    name="get_stats",
    description="Get transcript statistics",
    inputSchema={"type": "object", "properties": {}}
)

@server.call_tool()
async def call_tool(name: str, arguments: Any) -> Sequence[TextContent]:
    try:
        if name == "get_stats":
            result = db.get_statistics()
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
```

### What NOT To Do

❌ **Don't add these "features":**
- Verbose logging to stdout
- Complex parameter validation with error messages
- Large default result sets (>20 items)
- Optional parameters "just in case"
- Detailed usage documentation in descriptions
- Multiple ways to call the same tool
- Synchronous operations without timeouts
- Error handling that prints to console

✅ **Do this instead:**
- Silent operation (MCP handles logging)
- Return errors as tool results
- Small default result sets (5-10 items)
- Minimal required parameters
- Brief descriptions (3-5 words)
- One clear way to do each thing
- Async operations with timeouts
- Return errors as JSON/text responses
