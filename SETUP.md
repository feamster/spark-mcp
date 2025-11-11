# Setup Instructions

## Installation Complete! ✓

Your Spark MCP server is installed and tested successfully.

**Found:**
- 233 total transcripts
- 196 ad-hoc meetings (your primary use case)
- 37 calendar-based meetings
- 228 kept transcripts

## Configure Claude Desktop

1. **Open Claude Desktop config file:**
   ```bash
   open ~/Library/Application\ Support/Claude/claude_desktop_config.json
   ```

2. **Add this configuration:**
   ```json
   {
     "mcpServers": {
       "spark": {
         "command": "python3",
         "args": ["-m", "spark_mcp.server"],
         "cwd": "/Users/feamster/src/spark-mcp"
       }
     }
   }
   ```

   **OR** if you have other MCP servers, just add the `"spark"` section to your existing `"mcpServers"` object.

   A complete example is in `claude_config.json` in this directory.

3. **Restart Claude Desktop**

4. **Test it!** Ask Claude:
   - "List my recent meeting transcripts"
   - "Search my transcripts for discussions about neural networks"
   - "Get transcript statistics"
   - "Show me the transcript from message 63336"

## Verify Installation

Run the test script:
```bash
python3 test_server.py
```

You should see:
- ✓ Successfully connected to Spark databases
- Statistics about your transcripts
- Recent transcripts listed

## Available Tools in Claude

Once configured, Claude will have access to these tools:

1. **list_meeting_transcripts** - List/filter your transcripts
2. **get_meeting_transcript** - Get full transcript text
3. **search_meeting_transcripts** - Full-text search
4. **get_transcript_statistics** - Overview stats

## Troubleshooting

### Server not showing up in Claude Desktop

1. Check config file syntax (must be valid JSON)
2. Verify paths are correct
3. Check Claude Desktop logs:
   ```bash
   tail -f ~/Library/Logs/Claude/mcp*.log
   ```

### Database errors

1. Verify Spark is installed:
   ```bash
   ls ~/Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application\ Support/Spark\ Desktop/core-data/
   ```

2. Make sure files exist:
   - `messages.sqlite`
   - `search_fts5.sqlite`

### No transcripts found

- Ensure you have meeting transcripts in Spark
- Check they're marked as "kept" (not deleted)
- Run `python3 test_server.py` to see what's detected

## Next Steps

See `PLAN.md` for:
- Future email processing capabilities
- Alternative access methods
- Enhancement ideas

## Questions?

Check the README.md or examine:
- `spark_mcp/database.py` - Database queries
- `spark_mcp/server.py` - MCP server implementation
- `PLAN.md` - Full technical documentation
