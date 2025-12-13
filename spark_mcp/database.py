"""Database access layer for Spark SQLite databases."""

import sqlite3
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime


SPARK_BASE = Path.home() / "Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Application Support/Spark Desktop/core-data"
SPARK_CACHE = Path.home() / "Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library/Caches/Spark Desktop"


class SparkDatabase:
    """Access Spark Desktop SQLite databases in read-only mode."""

    def __init__(self):
        """Initialize database connections."""
        self.messages_db_path = SPARK_BASE / "messages.sqlite"
        self.search_db_path = SPARK_BASE / "search_fts5.sqlite"
        self.calendar_db_path = SPARK_BASE / "calendarsapi.sqlite"

        if not self.messages_db_path.exists():
            raise FileNotFoundError(f"Messages database not found at {self.messages_db_path}")
        if not self.search_db_path.exists():
            raise FileNotFoundError(f"Search database not found at {self.search_db_path}")
        if not self.calendar_db_path.exists():
            raise FileNotFoundError(f"Calendar database not found at {self.calendar_db_path}")

    def _connect_messages(self) -> sqlite3.Connection:
        """Connect to messages database in read-only mode with timeout."""
        conn = sqlite3.connect(f"file:{self.messages_db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def _connect_search(self) -> sqlite3.Connection:
        """Connect to search database in read-only mode with timeout."""
        conn = sqlite3.connect(f"file:{self.search_db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def _connect_calendar(self) -> sqlite3.Connection:
        """Connect to calendar database in read-only mode with timeout."""
        conn = sqlite3.connect(f"file:{self.calendar_db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
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

    # ============================================================================
    # EMAIL METHODS
    # ============================================================================

    def list_emails(
        self,
        folder: str = "inbox",
        unread_only: bool = False,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sender: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List emails with filtering.

        Args:
            folder: Filter by folder (inbox, sent, drafts, all)
            unread_only: Only show unread emails
            start_date: Filter after this ISO date
            end_date: Filter before this ISO date
            sender: Filter by sender email
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dict with 'emails' list and 'total' count
        """
        conn = self._connect_messages()

        where_clauses = []
        params = []

        # Exclude transcripts
        where_clauses.append("(meta NOT LIKE '%mtid%' OR meta IS NULL)")

        # Folder filtering
        if folder == "inbox":
            where_clauses.append("inInbox = 1")
        elif folder == "sent":
            where_clauses.append("inSent = 1")
        elif folder == "drafts":
            where_clauses.append("inDrafts = 1")

        if unread_only:
            where_clauses.append("unseen = 1")

        if start_date:
            start_ts = int(datetime.fromisoformat(start_date).timestamp())
            where_clauses.append("receivedDate >= ?")
            params.append(start_ts)

        if end_date:
            end_ts = int(datetime.fromisoformat(end_date).timestamp())
            where_clauses.append("receivedDate <= ?")
            params.append(end_ts)

        if sender:
            where_clauses.append("messageFrom LIKE ?")
            params.append(f"%{sender}%")

        where_clause = " AND ".join(where_clauses)

        # Get total count
        count_query = f"SELECT COUNT(*) as count FROM messages WHERE {where_clause}"
        cursor = conn.execute(count_query, params)
        total = cursor.fetchone()['count']

        # Get emails
        query = f"""
            SELECT
                pk,
                subject,
                messageFrom as sender,
                messageTo as recipients,
                datetime(receivedDate, 'unixepoch') as receivedDate,
                unseen,
                starred,
                conversationPk,
                numberOfFileAttachments
            FROM messages
            WHERE {where_clause}
            ORDER BY receivedDate DESC
            LIMIT ? OFFSET ?
        """

        params.extend([limit, offset])
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        emails = []
        for row in rows:
            emails.append({
                'messagePk': row['pk'],
                'subject': row['subject'] or '(No Subject)',
                'sender': row['sender'] or 'Unknown',
                'recipients': row['recipients'] or '',
                'receivedDate': row['receivedDate'],
                'unread': row['unseen'] == 1,
                'starred': row['starred'] == 1,
                'conversationPk': row['conversationPk'],
                'hasAttachments': (row['numberOfFileAttachments'] or 0) > 0
            })

        return {'emails': emails, 'total': total}

    def search_emails(
        self,
        query: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Search emails using full-text search.

        Args:
            query: Search query (FTS5 syntax)
            start_date: Filter after this ISO date
            end_date: Filter before this ISO date
            limit: Maximum results

        Returns:
            Dict with 'results' list and 'total' count
        """
        search_conn = self._connect_search()

        # FTS5 query
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

        cursor = search_conn.execute(fts_query, (query, limit * 2))
        fts_rows = cursor.fetchall()
        search_conn.close()

        if not fts_rows:
            return {'results': [], 'total': 0}

        # Get message metadata (excluding transcripts)
        message_pks = [row['messagePk'] for row in fts_rows]
        conn = self._connect_messages()

        placeholders = ','.join('?' * len(message_pks))
        where_clauses = [
            f"pk IN ({placeholders})",
            "(meta NOT LIKE '%mtid%' OR meta IS NULL)"
        ]
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

        query_sql = f"""
            SELECT
                pk,
                subject,
                messageFrom as sender,
                datetime(receivedDate, 'unixepoch') as receivedDate
            FROM messages
            WHERE {where_clause}
        """

        cursor = conn.execute(query_sql, params)
        metadata_rows = cursor.fetchall()
        conn.close()

        # Join results
        metadata_map = {row['pk']: row for row in metadata_rows}

        results = []
        for fts_row in fts_rows:
            pk = fts_row['messagePk']
            if pk in metadata_map:
                meta = metadata_map[pk]
                results.append({
                    'messagePk': pk,
                    'subject': meta['subject'] or '(No Subject)',
                    'sender': meta['sender'] or 'Unknown',
                    'receivedDate': meta['receivedDate'],
                    'excerpt': fts_row['excerpt'] or '',
                    'relevanceScore': -fts_row['rank']
                })
                if len(results) >= limit:
                    break

        return {'results': results, 'total': len(results)}

    def get_email(self, message_pk: int) -> Optional[Dict[str, Any]]:
        """Get full email content.

        Args:
            message_pk: Message primary key

        Returns:
            Email dict or None if not found
        """
        conn = self._connect_messages()

        cursor = conn.execute("""
            SELECT
                pk,
                subject,
                messageFrom as sender,
                messageTo as recipients,
                messageCc as cc,
                messageBcc as bcc,
                datetime(receivedDate, 'unixepoch') as receivedDate,
                unseen,
                starred,
                conversationPk,
                numberOfFileAttachments,
                inReplyTo,
                messageReferences
            FROM messages
            WHERE pk = ?
        """, (message_pk,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        # Get full text
        search_conn = self._connect_search()
        cursor = search_conn.execute(
            "SELECT searchBody FROM messagesfts WHERE messagePk = ?",
            (message_pk,)
        )
        fts_row = cursor.fetchone()
        search_conn.close()

        return {
            'messagePk': row['pk'],
            'subject': row['subject'] or '(No Subject)',
            'sender': row['sender'] or 'Unknown',
            'recipients': row['recipients'] or '',
            'cc': row['cc'] or '',
            'bcc': row['bcc'] or '',
            'receivedDate': row['receivedDate'],
            'unread': row['unseen'] == 1,
            'starred': row['starred'] == 1,
            'conversationPk': row['conversationPk'],
            'hasAttachments': (row['numberOfFileAttachments'] or 0) > 0,
            'inReplyTo': row['inReplyTo'],
            'fullText': fts_row['searchBody'] if fts_row else ''
        }

    def find_action_items(
        self,
        days: int = 7,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Find emails with potential action items from recent days.

        Args:
            days: Look back this many days (default: 7)
            limit: Maximum results

        Returns:
            Dict with 'emails' list containing potential action items
        """
        search_conn = self._connect_search()

        # Search for action-oriented language
        action_query = 'todo OR "to do" OR "action item" OR "please review" OR "need to" OR "can you" OR "could you" OR deadline OR urgent OR "waiting for"'

        fts_query = """
            SELECT
                messagePk,
                snippet(messagesfts, 4, '<mark>', '</mark>', '...', 80) as excerpt,
                rank
            FROM messagesfts
            WHERE searchBody MATCH ?
            ORDER BY rank
            LIMIT ?
        """

        cursor = search_conn.execute(fts_query, (action_query, limit * 2))
        fts_rows = cursor.fetchall()
        search_conn.close()

        if not fts_rows:
            return {'emails': [], 'total': 0}

        # Get metadata for recent emails only
        message_pks = [row['messagePk'] for row in fts_rows]
        conn = self._connect_messages()

        placeholders = ','.join('?' * len(message_pks))
        cutoff_ts = int((datetime.now().timestamp() - (days * 86400)))

        query = f"""
            SELECT
                pk,
                subject,
                messageFrom as sender,
                datetime(receivedDate, 'unixepoch') as receivedDate,
                inInbox
            FROM messages
            WHERE pk IN ({placeholders})
                AND receivedDate >= ?
                AND (meta NOT LIKE '%mtid%' OR meta IS NULL)
            ORDER BY receivedDate DESC
        """

        params = list(message_pks) + [cutoff_ts]
        cursor = conn.execute(query, params)
        metadata_rows = cursor.fetchall()
        conn.close()

        # Join results
        metadata_map = {row['pk']: row for row in metadata_rows}
        fts_map = {row['messagePk']: row for row in fts_rows}

        emails = []
        for pk, meta in metadata_map.items():
            if pk in fts_map:
                emails.append({
                    'messagePk': pk,
                    'subject': meta['subject'] or '(No Subject)',
                    'sender': meta['sender'] or 'Unknown',
                    'receivedDate': meta['receivedDate'],
                    'excerpt': fts_map[pk]['excerpt'],
                    'inInbox': meta['inInbox'] == 1,
                    'relevanceScore': -fts_map[pk]['rank']
                })

        # Sort by relevance
        emails.sort(key=lambda x: x['relevanceScore'], reverse=True)

        return {'emails': emails[:limit], 'total': len(emails)}

    def find_pending_responses(
        self,
        days: int = 7,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Find emails you may need to respond to.

        Args:
            days: Look back this many days (default: 7)
            limit: Maximum results

        Returns:
            Dict with 'emails' list that may need responses
        """
        conn = self._connect_messages()

        cutoff_ts = int((datetime.now().timestamp() - (days * 86400)))

        # Find inbox emails without a sent reply in the same conversation
        query = """
            SELECT
                m.pk,
                m.subject,
                m.messageFrom as sender,
                datetime(m.receivedDate, 'unixepoch') as receivedDate,
                m.conversationPk,
                m.messageId
            FROM messages m
            WHERE m.inInbox = 1
                AND m.receivedDate >= ?
                AND (m.meta NOT LIKE '%mtid%' OR m.meta IS NULL)
                AND NOT EXISTS (
                    SELECT 1 FROM messages reply
                    WHERE reply.conversationPk = m.conversationPk
                        AND reply.inSent = 1
                        AND reply.receivedDate > m.receivedDate
                )
            ORDER BY m.receivedDate DESC
            LIMIT ?
        """

        cursor = conn.execute(query, (cutoff_ts, limit))
        rows = cursor.fetchall()
        conn.close()

        emails = []
        for row in rows:
            emails.append({
                'messagePk': row['pk'],
                'subject': row['subject'] or '(No Subject)',
                'sender': row['sender'] or 'Unknown',
                'receivedDate': row['receivedDate'],
                'conversationPk': row['conversationPk'],
                'daysOld': (datetime.now() - datetime.fromisoformat(row['receivedDate'])).days
            })

        return {'emails': emails, 'total': len(emails)}

    # ============================================================================
    # CALENDAR METHODS
    # ============================================================================

    def list_events(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days_ahead: int = 1,
        limit: int = 50
    ) -> Dict[str, Any]:
        """List calendar events.

        Args:
            start_date: Start date (ISO format, default: today)
            end_date: End date (ISO format, default: start + days_ahead)
            days_ahead: If no end_date, look this many days ahead
            limit: Maximum results

        Returns:
            Dict with 'events' list and 'total' count
        """
        conn = self._connect_calendar()

        if not start_date:
            start_ts = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        else:
            start_ts = int(datetime.fromisoformat(start_date).timestamp())

        if not end_date:
            end_ts = start_ts + (days_ahead * 86400)
        else:
            end_ts = int(datetime.fromisoformat(end_date).timestamp())

        query = """
            SELECT
                pk,
                summary,
                descriptionProperty,
                datetime(dstart, 'unixepoch', 'localtime') as startTime,
                datetime(dend, 'unixepoch', 'localtime') as endTime,
                location,
                locationTitle,
                allDay,
                status,
                conferenceInfo
            FROM RDCALAPIEvent
            WHERE dstart >= ? AND dstart < ?
            ORDER BY dstart
            LIMIT ?
        """

        cursor = conn.execute(query, (start_ts, end_ts, limit))
        rows = cursor.fetchall()

        events = []
        for row in rows:
            events.append({
                'eventPk': row['pk'],
                'summary': row['summary'] or '(No Title)',
                'description': row['descriptionProperty'] or '',
                'startTime': row['startTime'],
                'endTime': row['endTime'],
                'location': row['locationTitle'] or row['location'] or '',
                'allDay': row['allDay'] == 1,
                'status': row['status'],
                'hasConferenceLink': bool(row['conferenceInfo'])
            })

        conn.close()
        return {'events': events, 'total': len(events)}

    def get_event_details(self, event_pk: int) -> Optional[Dict[str, Any]]:
        """Get full event details including attendees.

        Args:
            event_pk: Event primary key

        Returns:
            Event dict with full details or None if not found
        """
        conn = self._connect_calendar()

        # Get event
        cursor = conn.execute("""
            SELECT
                pk,
                summary,
                descriptionProperty,
                datetime(dstart, 'unixepoch', 'localtime') as startTime,
                datetime(dend, 'unixepoch', 'localtime') as endTime,
                location,
                locationTitle,
                allDay,
                status,
                conferenceInfo,
                url
            FROM RDCALAPIEvent
            WHERE pk = ?
        """, (event_pk,))

        row = cursor.fetchone()
        if not row:
            conn.close()
            return None

        # Get attendees
        cursor = conn.execute("""
            SELECT name, email, partStat, role
            FROM RDCALAPIAttendee
            WHERE refEventPK = ?
        """, (event_pk,))

        attendees = []
        for att_row in cursor.fetchall():
            attendees.append({
                'name': att_row['name'] or '',
                'email': att_row['email'] or '',
                'status': att_row['partStat'],
                'role': att_row['role']
            })

        # Get organizer
        cursor = conn.execute("""
            SELECT name, email
            FROM RDCALAPIOrganizer
            WHERE refEventPK = ?
        """, (event_pk,))

        org_row = cursor.fetchone()
        organizer = None
        if org_row:
            organizer = {
                'name': org_row['name'] or '',
                'email': org_row['email'] or ''
            }

        conn.close()

        return {
            'eventPk': row['pk'],
            'summary': row['summary'] or '(No Title)',
            'description': row['descriptionProperty'] or '',
            'startTime': row['startTime'],
            'endTime': row['endTime'],
            'location': row['locationTitle'] or row['location'] or '',
            'allDay': row['allDay'] == 1,
            'status': row['status'],
            'conferenceInfo': row['conferenceInfo'] or '',
            'url': row['url'] or '',
            'organizer': organizer,
            'attendees': attendees
        }

    def find_events_needing_prep(
        self,
        hours_ahead: int = 24,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Find upcoming events that may need preparation.

        Identifies events with:
        - External attendees (not just you)
        - Conference links
        - Longer duration (> 30 min)

        Args:
            hours_ahead: Look this many hours ahead (default: 24)
            limit: Maximum results

        Returns:
            Dict with 'events' list needing preparation
        """
        conn = self._connect_calendar()

        now_ts = int(datetime.now().timestamp())
        end_ts = now_ts + (hours_ahead * 3600)

        # Get events
        query = """
            SELECT
                pk,
                summary,
                datetime(dstart, 'unixepoch', 'localtime') as startTime,
                datetime(dend, 'unixepoch', 'localtime') as endTime,
                location,
                locationTitle,
                conferenceInfo,
                dend - dstart as duration
            FROM RDCALAPIEvent
            WHERE dstart >= ? AND dstart < ?
                AND status != 3
            ORDER BY dstart
            LIMIT ?
        """

        cursor = conn.execute(query, (now_ts, end_ts, limit * 2))
        rows = cursor.fetchall()

        events = []
        for row in rows:
            event_pk = row['pk']

            # Get attendee count
            cursor_att = conn.execute(
                "SELECT COUNT(*) as count FROM RDCALAPIAttendee WHERE refEventPK = ?",
                (event_pk,)
            )
            attendee_count = cursor_att.fetchone()['count']

            # Needs prep if: has attendees OR has conference link OR > 30min
            needs_prep = (
                attendee_count > 1 or
                bool(row['conferenceInfo']) or
                (row['duration'] or 0) > 1800
            )

            if needs_prep:
                # Calculate time until event
                start_dt = datetime.fromisoformat(row['startTime'])
                hours_until = (start_dt - datetime.now()).total_seconds() / 3600

                events.append({
                    'eventPk': event_pk,
                    'summary': row['summary'] or '(No Title)',
                    'startTime': row['startTime'],
                    'endTime': row['endTime'],
                    'location': row['locationTitle'] or row['location'] or '',
                    'attendeeCount': attendee_count,
                    'hasConferenceLink': bool(row['conferenceInfo']),
                    'durationMinutes': (row['duration'] or 0) // 60,
                    'hoursUntil': round(hours_until, 1)
                })

        conn.close()
        events = events[:limit]
        return {'events': events, 'total': len(events)}

    # ============================================================================
    # ATTACHMENT METHODS
    # ============================================================================

    def list_attachments(self, message_pk: int) -> Dict[str, Any]:
        """List attachments for a specific email.

        Args:
            message_pk: Message primary key

        Returns:
            Dict with 'attachments' list and 'total' count
        """
        conn = self._connect_messages()

        cursor = conn.execute("""
            SELECT
                pk,
                attachmentName,
                attachmentMIMEType,
                attachmentSize,
                attachmentId,
                status
            FROM messageAttachment
            WHERE messagePk = ?
            ORDER BY pk
        """, (message_pk,))

        rows = cursor.fetchall()
        conn.close()

        attachments = []
        for i, row in enumerate(rows):
            # Check if file exists locally
            file_path = self._get_attachment_path(message_pk, row['attachmentName'])
            is_downloaded = file_path.exists() if file_path else False

            attachments.append({
                'attachmentPk': row['pk'],
                'filename': row['attachmentName'] or f'attachment_{i}',
                'mimeType': row['attachmentMIMEType'] or 'application/octet-stream',
                'size': row['attachmentSize'] or 0,
                'attachmentId': row['attachmentId'],
                'index': i,
                'isDownloaded': is_downloaded
            })

        return {'attachments': attachments, 'total': len(attachments)}

    def get_attachment(
        self,
        message_pk: int,
        attachment_index: int = 0,
        extract_text: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Get attachment content with optional text extraction.

        Args:
            message_pk: Message primary key
            attachment_index: Index of attachment (0-based)
            extract_text: Whether to extract text from PDFs/docs

        Returns:
            Dict with attachment content or None if not found
        """
        from .extractors import extract_text as do_extract_text

        conn = self._connect_messages()

        cursor = conn.execute("""
            SELECT
                pk,
                attachmentName,
                attachmentMIMEType,
                attachmentSize,
                attachmentId
            FROM messageAttachment
            WHERE messagePk = ?
            ORDER BY pk
            LIMIT 1 OFFSET ?
        """, (message_pk, attachment_index))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        filename = row['attachmentName'] or f'attachment_{attachment_index}'
        mime_type = row['attachmentMIMEType'] or 'application/octet-stream'
        file_path = self._get_attachment_path(message_pk, filename)

        if not file_path or not file_path.exists():
            return {
                'messagePk': message_pk,
                'attachmentPk': row['pk'],
                'filename': filename,
                'mimeType': mime_type,
                'size': row['attachmentSize'] or 0,
                'content': None,
                'contentType': 'not_downloaded',
                'error': 'Attachment not downloaded locally. Open the email in Spark to download.'
            }

        if extract_text:
            content, content_type = do_extract_text(str(file_path), mime_type)
        else:
            import base64
            content = base64.b64encode(file_path.read_bytes()).decode()
            content_type = 'base64'

        return {
            'messagePk': message_pk,
            'attachmentPk': row['pk'],
            'filename': filename,
            'mimeType': mime_type,
            'size': row['attachmentSize'] or 0,
            'content': content,
            'contentType': content_type
        }

    def search_attachments(
        self,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Search for emails with attachments matching criteria.

        Args:
            filename: Filename pattern (supports SQL wildcards %)
            mime_type: Filter by MIME type
            limit: Maximum results

        Returns:
            Dict with 'results' list and 'total' count
        """
        conn = self._connect_messages()

        where_clauses = []
        params = []

        if filename:
            # Support * as wildcard, convert to SQL %
            sql_pattern = filename.replace('*', '%')
            where_clauses.append("a.attachmentName LIKE ?")
            params.append(sql_pattern)

        if mime_type:
            if mime_type.endswith('/*'):
                # Handle type/* patterns like "application/*"
                base_type = mime_type[:-1]
                where_clauses.append("a.attachmentMIMEType LIKE ?")
                params.append(f"{base_type}%")
            else:
                where_clauses.append("a.attachmentMIMEType = ?")
                params.append(mime_type)

        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

        query = f"""
            SELECT
                m.pk as messagePk,
                m.subject,
                m.messageFrom as sender,
                datetime(m.receivedDate, 'unixepoch') as receivedDate,
                a.pk as attachmentPk,
                a.attachmentName,
                a.attachmentMIMEType,
                a.attachmentSize
            FROM messageAttachment a
            JOIN messages m ON a.messagePk = m.pk
            WHERE {where_clause}
            ORDER BY m.receivedDate DESC
            LIMIT ?
        """
        params.append(limit)

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        # Group by message
        messages = {}
        for row in rows:
            pk = row['messagePk']
            if pk not in messages:
                messages[pk] = {
                    'messagePk': pk,
                    'emailSubject': row['subject'] or '(No Subject)',
                    'sender': row['sender'] or 'Unknown',
                    'receivedDate': row['receivedDate'],
                    'attachments': []
                }
            messages[pk]['attachments'].append({
                'attachmentPk': row['attachmentPk'],
                'filename': row['attachmentName'],
                'mimeType': row['attachmentMIMEType'],
                'size': row['attachmentSize'] or 0
            })

        results = list(messages.values())
        return {'results': results, 'total': len(results)}

    def _get_attachment_path(self, message_pk: int, filename: str) -> Optional[Path]:
        """Get the filesystem path for an attachment.

        Args:
            message_pk: Message primary key
            filename: Attachment filename

        Returns:
            Path object or None if cannot be determined
        """
        if not filename:
            return None

        # Spark stores attachments in: Caches/Spark Desktop/messagesData/1/{messagePk}/{filename}
        # The "1" appears to be an account ID, checking if the file exists
        path = SPARK_CACHE / "messagesData" / "1" / str(message_pk) / filename
        if path.exists():
            return path

        # Try without account ID subfolder
        path = SPARK_CACHE / "messagesData" / str(message_pk) / filename
        if path.exists():
            return path

        # Return the most likely path even if it doesn't exist yet
        return SPARK_CACHE / "messagesData" / "1" / str(message_pk) / filename

    # ============================================================================
    # COMBINED INTELLIGENCE
    # ============================================================================

    def get_daily_briefing(self) -> Dict[str, Any]:
        """Get daily briefing: today's events, unread emails, action items.

        Returns:
            Dict with comprehensive daily overview
        """
        # Today's events
        events_result = self.list_events(days_ahead=1, limit=20)

        # Unread inbox emails
        unread_result = self.list_emails(folder="inbox", unread_only=True, limit=10)

        # Recent action items
        actions_result = self.find_action_items(days=3, limit=10)

        # Pending responses
        responses_result = self.find_pending_responses(days=7, limit=10)

        # Events needing prep
        prep_result = self.find_events_needing_prep(hours_ahead=24, limit=10)

        return {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'todaysEvents': events_result['events'],
            'totalEvents': events_result['total'],
            'unreadEmails': unread_result['emails'],
            'totalUnread': unread_result['total'],
            'actionItems': actions_result['emails'],
            'pendingResponses': responses_result['emails'],
            'eventsNeedingPrep': prep_result['events']
        }

    def find_context_for_meeting(
        self,
        event_pk: int,
        days_back: int = 30
    ) -> Dict[str, Any]:
        """Find recent email context related to a meeting.

        Args:
            event_pk: Event primary key
            days_back: Look back this many days for emails (default: 30)

        Returns:
            Dict with event details and related emails
        """
        # Get event details
        event = self.get_event_details(event_pk)
        if not event:
            return {'error': 'Event not found'}

        # Extract attendee emails
        attendee_emails = [a['email'] for a in event.get('attendees', []) if a['email']]
        if event.get('organizer') and event['organizer']['email']:
            attendee_emails.append(event['organizer']['email'])

        # Search for emails from/to attendees
        cutoff_ts = int((datetime.now().timestamp() - (days_back * 86400)))

        if not attendee_emails:
            return {
                'event': event,
                'relatedEmails': [],
                'total': 0
            }

        conn = self._connect_messages()

        # Build query for emails from/to any attendee
        email_conditions = []
        params = []
        for email in attendee_emails:
            email_conditions.append("messageFrom LIKE ?")
            params.append(f"%{email}%")

        where_clause = f"({' OR '.join(email_conditions)}) AND receivedDate >= ? AND (meta NOT LIKE '%mtid%' OR meta IS NULL)"
        params.append(cutoff_ts)

        query = f"""
            SELECT
                pk,
                subject,
                messageFrom as sender,
                datetime(receivedDate, 'unixepoch') as receivedDate
            FROM messages
            WHERE {where_clause}
            ORDER BY receivedDate DESC
            LIMIT 20
        """

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        emails = []
        for row in rows:
            emails.append({
                'messagePk': row['pk'],
                'subject': row['subject'] or '(No Subject)',
                'sender': row['sender'] or 'Unknown',
                'receivedDate': row['receivedDate']
            })

        return {
            'event': event,
            'relatedEmails': emails,
            'total': len(emails)
        }
