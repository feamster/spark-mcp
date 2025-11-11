# Spark MCP Server

**Your personal AI assistant for Spark Desktop** - Access meeting transcripts, emails, and calendar through the Model Context Protocol.

## 🎯 What Can It Do?

This MCP server gives Claude (or any MCP client) access to your Spark Desktop data to help you:

**📝 Meeting Transcripts:**
- List and search all your meeting transcripts (including ad-hoc recordings)
- Get full transcript text for analysis
- Find discussions about specific topics across all meetings

**📧 Email Intelligence:**
- List and search through your emails
- Find action items and todos from recent emails
- Identify emails needing responses
- Search full email content with natural language

**📅 Calendar Management:**
- View your schedule and upcoming events
- Get detailed event info with attendees and conference links
- Find meetings that need preparation

**🎯 Combined Intelligence:**
- Get daily briefings with everything you need to know
- Find email context for upcoming meetings
- Automatic action item detection

## ✨ Key Features

- 🔒 **Read-only & Safe** - Never modifies your data
- ⚡ **Lightning Fast** - Local SQLite queries, no network required
- 🎯 **Smart Search** - Full-text search across all content
- 📊 **Comprehensive** - 60,530+ emails, 233 transcripts, full calendar access
- 🔐 **Private** - All data stays on your machine

## 📋 Requirements

- macOS (Spark Desktop must be installed)
- Python 3.10+
- Spark Desktop for macOS (App Store version)

## 🚀 Installation

```bash
cd /path/to/spark-mcp
pip install -e .
```

## ⚙️ Configuration

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "spark": {
      "command": "python3",
      "args": ["-m", "spark_mcp.server"],
      "cwd": "/Users/YOUR_USERNAME/src/spark-mcp"
    }
  }
}
```

Restart Claude Desktop and you're ready!

## 🛠️ Available Tools

### Meeting Transcripts (4 tools)

#### `list_meeting_transcripts`
List meeting transcripts with filtering and pagination.

**Parameters:**
- `startDate` (optional): ISO date to filter after
- `endDate` (optional): ISO date to filter before
- `includeAdHoc` (optional, default: true): Include ad-hoc meetings
- `onlyKept` (optional, default: true): Exclude deleted transcripts
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
      "sender": "Nick Feamster <feamster@uchicago.edu>",
      "receivedDate": "2025-11-11 15:59:12",
      "transcriptId": "-8929133086933914113",
      "isCalendarEvent": false,
      "textLength": 29893,
      "hasFullText": true
    }
  ],
  "total": 228
}
```

#### `get_meeting_transcript`
Get full transcript content including complete text.

**Parameters:**
- `messagePk` OR `transcriptId`: Identifier for the transcript

**Returns:** Full transcript with metadata and complete text

#### `search_meeting_transcripts`
Full-text search across all transcript content.

**Parameters:**
- `query` (required): Search query (supports FTS5 syntax)
- `startDate`, `endDate`: Date filters
- `limit` (default: 20): Max results
- `includeContext` (default: true): Show excerpts

**FTS5 Syntax:**
- `neural network AND security` - Both terms required
- `"exact phrase"` - Exact match
- `word1 OR word2` - Either term
- `NOT word` - Exclude term

**Returns:** Matching transcripts with highlighted excerpts

#### `get_transcript_statistics`
Get overview of your transcript collection.

**Returns:** Total counts, date range, top senders, etc.

---

### Email Tools (5 tools)

#### `list_emails`
List emails with powerful filtering options.

**Parameters:**
- `folder` (default: "inbox"): inbox, sent, drafts, or all
- `unreadOnly` (default: false): Show only unread
- `startDate`, `endDate`: Date range filters
- `sender`: Filter by sender email
- `limit` (default: 50): Max results
- `offset` (default: 0): Pagination

**Example:**
```json
{
  "folder": "inbox",
  "unreadOnly": true,
  "limit": 10
}
```

**Returns:**
```json
{
  "emails": [
    {
      "messagePk": 63337,
      "subject": "RE: Huawei - documents on Box",
      "sender": "Nguyen, Tung T. <tnguyen@sidley.com>",
      "recipients": "feamster@uchicago.edu",
      "receivedDate": "2025-11-11 20:14:35",
      "unread": true,
      "starred": false,
      "hasAttachments": false
    }
  ],
  "total": 7
}
```

#### `search_emails`
Search through all email content using full-text search.

**Parameters:**
- `query` (required): Search query
- `startDate`, `endDate`: Date filters
- `limit` (default: 20): Max results

**Example:**
```json
{
  "query": "patent AND prior art"
}
```

**Returns:** Matching emails with excerpts

#### `get_email`
Get full email content including body text.

**Parameters:**
- `messagePk` (required): Message ID from list_emails

**Returns:** Complete email with full text, recipients, thread info

#### `find_action_items`
Automatically find emails with action items, todos, and deadlines.

Searches for keywords like: "todo", "action item", "please review", "need to", "deadline", "urgent", "waiting for", "can you", "could you"

**Parameters:**
- `days` (default: 7): Look back this many days
- `limit` (default: 20): Max results

**Example use:**
"Show me action items from this week's emails"

**Returns:** Emails with highlighted action-oriented text

####`find_pending_responses`
Find emails you may need to respond to.

Identifies inbox emails without sent replies in the same conversation thread.

**Parameters:**
- `days` (default: 7): Look back this many days
- `limit` (default: 20): Max results

**Example use:**
"Remind me of people I need to respond to"

**Returns:** Emails without replies, sorted by age

---

### Calendar Tools (3 tools)

#### `list_events`
List calendar events for any date range.

**Parameters:**
- `startDate` (optional): Start date (ISO format, default: today)
- `endDate` (optional): End date
- `daysAhead` (default: 1): Days to look ahead if no endDate
- `limit` (default: 50): Max results

**Example:**
```json
{
  "daysAhead": 7
}
```

**Returns:**
```json
{
  "events": [
    {
      "eventPk": 70785,
      "summary": "NetApp - 416 & 571 Prior Art Discussion",
      "startTime": "2025-11-11 10:00:00",
      "endTime": "2025-11-11 11:00:00",
      "location": "Microsoft Teams Meeting",
      "allDay": false,
      "hasConferenceLink": true
    }
  ],
  "total": 15
}
```

#### `get_event_details`
Get complete event information including attendees and conference links.

**Parameters:**
- `eventPk` (required): Event ID from list_events

**Returns:**
```json
{
  "eventPk": 70785,
  "summary": "NetApp - 416 & 571 Prior Art Discussion",
  "description": "...",
  "startTime": "2025-11-11 10:00:00",
  "endTime": "2025-11-11 11:00:00",
  "location": "",
  "conferenceInfo": "https://teams.microsoft.com/l/meetup-join/...",
  "organizer": {
    "name": "Liu, Charles",
    "email": "ccliu@winston.com"
  },
  "attendees": [
    {
      "name": "Nick Feamster",
      "email": "feamster@uchicago.edu",
      "status": 20602,
      "role": 1
    }
  ]
}
```

#### `find_events_needing_prep`
Find upcoming meetings that need preparation.

Identifies events with:
- External attendees (not just you)
- Conference/video links
- Duration > 30 minutes

**Parameters:**
- `hoursAhead` (default: 24): Look this many hours ahead
- `limit` (default: 20): Max results

**Example use:**
"Tell me what meetings I have today that I need to actually prepare for"

**Returns:**
```json
{
  "events": [
    {
      "eventPk": 70785,
      "summary": "NetApp - 416 & 571 Prior Art Discussion",
      "startTime": "2025-11-11 10:00:00",
      "attendeeCount": 3,
      "hasConferenceLink": true,
      "durationMinutes": 60,
      "hoursUntil": 2.5
    }
  ],
  "total": 5
}
```

---

### Combined Intelligence (2 tools)

#### `get_daily_briefing` ⭐
**Your AI-powered morning briefing!**

Get everything you need to start your day in one comprehensive overview.

**Parameters:** None

**Returns:**
```json
{
  "date": "2025-11-11",
  "todaysEvents": [...],           // All events today
  "totalEvents": 15,
  "unreadEmails": [...],           // Recent unread emails
  "totalUnread": 7,
  "actionItems": [...],            // Emails with todos/actions
  "pendingResponses": [...],       // Emails needing replies
  "eventsNeedingPrep": [...]       // Meetings requiring preparation
}
```

**Example use:**
- "Give me my daily briefing"
- "What do I need to know for today?"
- "Summarize my morning"

#### `find_context_for_meeting`
Find recent email context for an upcoming meeting.

Automatically finds emails from/to meeting attendees to help you prepare.

**Parameters:**
- `eventPk` (required): Event ID from list_events
- `daysBack` (default: 30): Look back this many days

**Example use:**
"Find email context for my 10am meeting"

**Returns:**
```json
{
  "event": {
    "summary": "NetApp - 416 & 571 Prior Art Discussion",
    "attendees": [...]
  },
  "relatedEmails": [
    {
      "messagePk": 63100,
      "subject": "RE: Prior art references for 416",
      "sender": "ccliu@winston.com",
      "receivedDate": "2025-11-05 14:22:00"
    }
  ],
  "total": 8
}
```

---

## 💡 Example Use Cases

### Morning Routine
```
You: "Give me my daily briefing"
Claude: Calls get_daily_briefing() and summarizes:
- 15 events today including NetApp meeting at 10am
- 7 unread emails (2 urgent)
- 3 action items from this week
- 2 events need prep (NetApp discussion, Client call)
```

### Email Management
```
You: "Show me action items from this week's emails"
Claude: Uses find_action_items() to show:
- 5 emails with todos and deadlines
- Highlights: "please review by Friday", "need your input"

You: "Who do I need to respond to?"
Claude: Uses find_pending_responses() to list:
- Email from Tung (3 days old, no reply)
- Email from John (2 days old, no reply)
```

### Meeting Prep
```
You: "What meetings today need prep?"
Claude: Uses find_events_needing_prep() then get_event_details():
- NetApp meeting at 10am (3 attendees, 60min, Teams link)
- Client strategy call at 2pm (5 attendees, 90min)

You: "Find email context for the NetApp meeting"
Claude: Uses find_context_for_meeting():
- 8 related emails from Charles Liu and Kyle
- Recent thread about prior art references
- [Provides summary of key points]
```

### Research & Analysis
```
You: "Search my meeting transcripts for discussions about neural networks"
Claude: Uses search_meeting_transcripts():
- 12 transcripts mention neural networks
- Most recent: "AI and Digital Literacy" (Nov 11)
- [Summarizes key discussion points]

You: "Get the full transcript from that meeting"
Claude: Uses get_meeting_transcript():
- Returns 32,000 characters of transcript text
- [Provides detailed summary and analysis]
```

---

## 📊 Data Access

### Databases Used

**messages.sqlite** (~178 MB)
- 60,530 emails
- 233 meeting transcripts
- Conversation threads
- Metadata and flags

**search_fts5.sqlite** (~232 MB)
- Full-text search index
- Complete email/transcript content
- FTS5 for fast searching

**calendarsapi.sqlite** (~101 MB)
- Calendar events
- Attendees and organizers
- Conference links
- Recurring events

### Transcript Types

**Ad-Hoc Meetings: 196**
- User-initiated transcriptions
- Quick recordings without calendar
- Primary use case for most users

**Calendar Meetings: 37**
- Scheduled meetings with event info
- Linked to calendar invites
- Full attendee metadata

**Total: 228 kept transcripts** (233 including deleted)

---

## 🔒 Safety & Privacy

- ✅ **Read-only access** - Never writes to databases
- ✅ **Local processing** - All data stays on your machine
- ✅ **No network** - Works completely offline
- ✅ **Safe concurrent access** - Compatible with Spark running
- ✅ **No modifications** - Your Spark data is untouched

## 🧪 Testing

```bash
# Test database connection
python3 test_server.py

# Test individual features
python3 -c "
from spark_mcp.database import SparkDatabase
db = SparkDatabase()

# Get briefing
briefing = db.get_daily_briefing()
print(f'Events today: {briefing[\"totalEvents\"]}')
print(f'Unread emails: {briefing[\"totalUnread\"]}')

# Find action items
actions = db.find_action_items(days=7)
print(f'Action items: {actions[\"total\"]}')
"
```

## 🐛 Troubleshooting

### Server not connecting

Check databases exist:
```bash
ls ~/Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application\ Support/Spark\ Desktop/core-data/
```

Should see: `messages.sqlite`, `search_fts5.sqlite`, `calendarsapi.sqlite`

### No transcripts/emails found

- Verify Spark Desktop is installed
- Check you have meeting transcripts in Spark
- Transcripts must be marked as "kept" (not deleted)
- Run `get_transcript_statistics` to see counts

### Empty transcript text

Some transcripts may not be cached locally:
- Recent transcripts still syncing
- Check `hasFullText` field
- Deleted transcripts have no content

## 📚 Documentation

- **PLAN.md** - Complete technical documentation
- **SETUP.md** - Detailed setup instructions
- **README.md** - This file

## 🔮 Future Enhancements

See [PLAN.md](PLAN.md) for roadmap including:
- Attachment handling
- Email composition analysis
- Contact insights
- Thread visualization
- Export capabilities
- Alternative access methods (API, IMAP)

## 🤝 Contributing

This is a personal project, but feel free to fork and adapt for your needs!

## 📄 License

MIT

---

**Built by Nick Feamster** | [GitHub](https://github.com/feamster/spark-mcp)

*Your personal AI assistant for Spark Desktop - making email, calendar, and meetings work for you!*
