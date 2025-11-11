"""Database access layer for Spark SQLite databases."""

import sqlite3
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime


SPARK_BASE = Path.home() / "Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application Support/Spark Desktop/core-data"


class SparkDatabase:
    """Access Spark Desktop SQLite databases in read-only mode."""

    def __init__(self):
        """Initialize database connections."""
        self.messages_db_path = SPARK_BASE / "messages.sqlite"
        self.search_db_path = SPARK_BASE / "search_fts5.sqlite"

        if not self.messages_db_path.exists():
            raise FileNotFoundError(f"Messages database not found at {self.messages_db_path}")
        if not self.search_db_path.exists():
            raise FileNotFoundError(f"Search database not found at {self.search_db_path}")

    def _connect_messages(self) -> sqlite3.Connection:
        """Connect to messages database in read-only mode."""
        conn = sqlite3.connect(f"file:{self.messages_db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_search(self) -> sqlite3.Connection:
        """Connect to search database in read-only mode."""
        conn = sqlite3.connect(f"file:{self.search_db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def list_transcripts(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        include_ad_hoc: bool = True,
        only_kept: bool = True,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List meeting transcripts with metadata.

        Args:
            start_date: Filter transcripts after this ISO date
            end_date: Filter transcripts before this ISO date
            include_ad_hoc: Include ad-hoc meetings (default: True)
            only_kept: Only show kept transcripts (default: True)
            limit: Maximum results (default: 50)
            offset: Pagination offset (default: 0)

        Returns:
            Dict with 'transcripts' list and 'total' count
        """
        conn = self._connect_messages()

        where_clauses = ["meta LIKE '%mtid%'"]
        params = []

        if only_kept:
            where_clauses.append("json_extract(meta, '$.mtskp') = 1")

        if start_date:
            start_ts = int(datetime.fromisoformat(start_date).timestamp())
            where_clauses.append("receivedDate >= ?")
            params.append(start_ts)

        if end_date:
            end_ts = int(datetime.fromisoformat(end_date).timestamp())
            where_clauses.append("receivedDate <= ?")
            params.append(end_ts)

        if not include_ad_hoc:
            where_clauses.append("json_extract(meta, '$.mtes') IS NOT NULL")

        where_clause = " AND ".join(where_clauses)

        # Get total count
        count_query = f"SELECT COUNT(*) as count FROM messages WHERE {where_clause}"
        cursor = conn.execute(count_query, params)
        total = cursor.fetchone()['count']

        # Get transcripts
        query = f"""
            SELECT
                pk as messagePk,
                subject,
                messageFrom as sender,
                datetime(receivedDate, 'unixepoch') as receivedDate,
                json_extract(meta, '$.mtid') as transcriptId,
                json_extract(meta, '$.mtsd') as meetingStartMs,
                json_extract(meta, '$.mted') as meetingEndMs,
                json_extract(meta, '$.mtes') as eventSummary,
                meta
            FROM messages
            WHERE {where_clause}
            ORDER BY receivedDate DESC
            LIMIT ? OFFSET ?
        """

        params.extend([limit, offset])
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

        # Get text lengths from FTS database
        message_pks = [row['messagePk'] for row in rows]
        text_lengths = self._get_text_lengths(message_pks)

        transcripts = []
        for row in rows:
            pk = row['messagePk']
            transcripts.append({
                'messagePk': pk,
                'subject': row['subject'] or 'Untitled',
                'sender': row['sender'] or 'Unknown',
                'receivedDate': row['receivedDate'],
                'meetingStartDate': datetime.fromtimestamp(row['meetingStartMs'] / 1000).isoformat() if row['meetingStartMs'] else None,
                'meetingEndDate': datetime.fromtimestamp(row['meetingEndMs'] / 1000).isoformat() if row['meetingEndMs'] else None,
                'transcriptId': row['transcriptId'],
                'isCalendarEvent': row['eventSummary'] is not None,
                'eventSummary': row['eventSummary'],
                'textLength': text_lengths.get(pk, 0),
                'hasFullText': text_lengths.get(pk, 0) > 0
            })

        conn.close()
        return {'transcripts': transcripts, 'total': total}

    def get_transcript(
        self,
        message_pk: Optional[int] = None,
        transcript_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get full transcript content.

        Args:
            message_pk: Message primary key
            transcript_id: Transcript ID (mtid)

        Returns:
            Transcript dict or None if not found
        """
        conn = self._connect_messages()

        # Look up by transcript_id if provided
        if not message_pk and transcript_id:
            cursor = conn.execute(
                "SELECT pk FROM messages WHERE json_extract(meta, '$.mtid') = ?",
                (transcript_id,)
            )
            row = cursor.fetchone()
            if not row:
                conn.close()
                return None
            message_pk = row['pk']

        if not message_pk:
            conn.close()
            return None

        # Get message metadata
        cursor = conn.execute("""
            SELECT
                pk as messagePk,
                subject,
                messageFrom as sender,
                messageTo as recipients,
                datetime(receivedDate, 'unixepoch') as receivedDate,
                meta
            FROM messages
            WHERE pk = ?
        """, (message_pk,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        # Parse metadata
        try:
            metadata = json.loads(row['meta']) if row['meta'] else {}
        except json.JSONDecodeError:
            metadata = {}

        if 'mtid' not in metadata:
            return None

        # Get full text from FTS
        search_conn = self._connect_search()
        cursor = search_conn.execute(
            "SELECT searchBody FROM messagesfts WHERE messagePk = ?",
            (message_pk,)
        )
        fts_row = cursor.fetchone()
        search_conn.close()

        full_text = fts_row['searchBody'] if fts_row else ''

        return {
            'messagePk': row['messagePk'],
            'subject': row['subject'] or 'Untitled',
            'sender': row['sender'] or 'Unknown',
            'recipients': row['recipients'] or '',
            'receivedDate': row['receivedDate'],
            'meetingStartDate': datetime.fromtimestamp(metadata.get('mtsd', 0) / 1000).isoformat() if metadata.get('mtsd') else None,
            'meetingEndDate': datetime.fromtimestamp(metadata.get('mted', 0) / 1000).isoformat() if metadata.get('mted') else None,
            'transcriptId': metadata.get('mtid'),
            'fullText': full_text or '',
            'metadata': {
                'language': metadata.get('mtsl'),
                'status': metadata.get('mtss', False),
                'autoProcessed': metadata.get('mtsap', False),
                'isKept': metadata.get('mtskp') == 1,
                'eventSummary': metadata.get('mtes')
            }
        }

    def search_transcripts(
        self,
        query: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 20,
        include_context: bool = True
    ) -> Dict[str, Any]:
        """Search across transcripts using FTS5.

        Args:
            query: Search query (supports FTS5 syntax)
            start_date: Filter after this ISO date
            end_date: Filter before this ISO date
            limit: Maximum results (default: 20)
            include_context: Include highlighted excerpts (default: True)

        Returns:
            Dict with 'results' list and 'total' count
        """
        search_conn = self._connect_search()

        # FTS5 query
        if include_context:
            fts_query = """
                SELECT
                    messagePk,
                    snippet(messagesfts, 4, '<mark>', '</mark>', '...', 64) as excerpt,
                    rank
                FROM messagesfts
                WHERE searchBody MATCH ?
                ORDER BY rank
                LIMIT ?
            """
        else:
            fts_query = """
                SELECT
                    messagePk,
                    searchBody as excerpt,
                    rank
                FROM messagesfts
                WHERE searchBody MATCH ?
                ORDER BY rank
                LIMIT ?
            """

        cursor = search_conn.execute(fts_query, (query, limit * 2))
        fts_rows = cursor.fetchall()
        search_conn.close()

        if not fts_rows:
            return {'results': [], 'total': 0}

        # Get message metadata for matched transcripts
        message_pks = [row['messagePk'] for row in fts_rows]
        conn = self._connect_messages()

        placeholders = ','.join('?' * len(message_pks))
        where_clauses = [f"pk IN ({placeholders})", "meta LIKE '%mtid%'"]
        params = list(message_pks)

        if start_date:
            start_ts = int(datetime.fromisoformat(start_date).timestamp())
            where_clauses.append("receivedDate >= ?")
            params.append(start_ts)

        if end_date:
            end_ts = int(datetime.fromisoformat(end_date).timestamp())
            where_clauses.append("receivedDate <= ?")
            params.append(end_ts)

        where_clause = " AND ".join(where_clauses)

        query = f"""
            SELECT
                pk as messagePk,
                subject,
                messageFrom as sender,
                datetime(receivedDate, 'unixepoch') as receivedDate
            FROM messages
            WHERE {where_clause}
        """

        cursor = conn.execute(query, params)
        metadata_rows = cursor.fetchall()
        conn.close()

        # Join FTS results with metadata
        metadata_map = {row['messagePk']: row for row in metadata_rows}

        results = []
        for fts_row in fts_rows:
            pk = fts_row['messagePk']
            if pk in metadata_map:
                meta = metadata_map[pk]
                results.append({
                    'messagePk': pk,
                    'subject': meta['subject'] or 'Untitled',
                    'sender': meta['sender'] or 'Unknown',
                    'receivedDate': meta['receivedDate'],
                    'excerpt': fts_row['excerpt'] or '',
                    'relevanceScore': -fts_row['rank']  # Negative rank = higher is better
                })
                if len(results) >= limit:
                    break

        return {'results': results, 'total': len(results)}

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about transcript collection.

        Returns:
            Dict with statistics about all transcripts
        """
        conn = self._connect_messages()

        # Get counts and date range
        cursor = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN json_extract(meta, '$.mtes') IS NOT NULL THEN 1 ELSE 0 END) as calendarMeetings,
                SUM(CASE WHEN json_extract(meta, '$.mtes') IS NULL THEN 1 ELSE 0 END) as adHocMeetings,
                SUM(CASE WHEN json_extract(meta, '$.mtskp') = 1 THEN 1 ELSE 0 END) as kept,
                MIN(datetime(receivedDate, 'unixepoch')) as earliest,
                MAX(datetime(receivedDate, 'unixepoch')) as latest
            FROM messages
            WHERE meta LIKE '%mtid%'
        """)
        counts = cursor.fetchone()

        # Get all transcript PKs for text length check
        cursor = conn.execute("SELECT pk FROM messages WHERE meta LIKE '%mtid%'")
        all_pks = [row['pk'] for row in cursor.fetchall()]

        text_lengths = self._get_text_lengths(all_pks)
        with_full_text = sum(1 for length in text_lengths.values() if length > 0)

        # Get top senders
        cursor = conn.execute("""
            SELECT
                messageFrom as email,
                COUNT(*) as count
            FROM messages
            WHERE meta LIKE '%mtid%'
            GROUP BY messageFrom
            ORDER BY count DESC
            LIMIT 10
        """)
        top_senders = [
            {'email': row['email'] or 'Unknown', 'count': row['count']}
            for row in cursor.fetchall()
        ]

        conn.close()

        return {
            'totalTranscripts': counts['total'] or 0,
            'calendarMeetings': counts['calendarMeetings'] or 0,
            'adHocMeetings': counts['adHocMeetings'] or 0,
            'keptTranscripts': counts['kept'] or 0,
            'deletedTranscripts': (counts['total'] or 0) - (counts['kept'] or 0),
            'withFullText': with_full_text,
            'dateRange': {
                'earliest': counts['earliest'],
                'latest': counts['latest']
            },
            'topSenders': top_senders
        }

    def _get_text_lengths(self, message_pks: List[int]) -> Dict[int, int]:
        """Get text lengths for multiple messages from FTS database.

        Args:
            message_pks: List of message primary keys

        Returns:
            Dict mapping message_pk to text length
        """
        if not message_pks:
            return {}

        conn = self._connect_search()
        placeholders = ','.join('?' * len(message_pks))
        query = f"""
            SELECT messagePk, length(searchBody) as len
            FROM messagesfts
            WHERE messagePk IN ({placeholders})
        """

        cursor = conn.execute(query, message_pks)
        results = {row['messagePk']: row['len'] or 0 for row in cursor.fetchall()}
        conn.close()

        return results
