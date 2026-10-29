"""Database access layer for Spark SQLite databases."""

import sqlite3
import json
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta


_APPSTORE_LIB = Path.home() / "Library/Containers/com.readdle.SparkDesktop.appstore/Data/Library"
# Direct-download (non-App Store) Spark is not sandboxed and keeps its data under ~/Library
_SPARK_LIB = _APPSTORE_LIB if (_APPSTORE_LIB / "Application Support/Spark Desktop/core-data").exists() else Path.home() / "Library"
SPARK_BASE = _SPARK_LIB / "Application Support/Spark Desktop/core-data"
SPARK_CACHE = _SPARK_LIB / "Caches/Spark Desktop"

# Upper bound on FTS candidates scanned when search_emails has to filter or
# re-sort the match set (date/sender filters, sort_by="date").
FILTERED_FTS_CANDIDATE_CAP = 20000

# SQLite VM instructions between deadline checks in the progress handler.
_PROGRESS_HANDLER_STEPS = 10000


class QueryTimeout(Exception):
    """Raised when a database call runs past its deadline (see SparkDatabase.deadline)."""


# Generic localparts that should not be treated as person names when building
# fuzzy match candidates from a display name.
_GENERIC_LOCALPARTS = {
    "info", "support", "noreply", "no-reply", "do-not-reply",
    "hello", "team", "admin", "contact", "help", "service",
    "notifications", "alerts", "news", "marketing",
}


def _localpart_candidates(name: str) -> List[str]:
    """Generate plausible email localparts for a person's name.

    For "Christine Taylo" returns: christine.taylo, ctaylo, christinet,
    christine, taylo, christine_taylo, christinetaylo.
    Returns an empty list if the name doesn't look like a person name.
    """
    if not name:
        return []
    # Treat anything with @ or a TLD-looking suffix as not-a-name.
    if "@" in name or re.search(r"\.[a-z]{2,}$", name, re.IGNORECASE):
        return []
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    parts = [re.sub(r"[^A-Za-z\-']", "", p).lower() for p in parts]
    parts = [p for p in parts if p]
    if not parts:
        return []
    if len(parts) == 1:
        return [parts[0]]
    first, last = parts[0], parts[-1]
    candidates = {
        f"{first}.{last}",
        f"{first[0]}{last}",
        f"{first}{last[0]}",
        f"{first}_{last}",
        f"{first}{last}",
        first,
        last,
    }
    # Filter out single-char candidates and generic strings.
    return sorted(c for c in candidates if len(c) >= 2 and c not in _GENERIC_LOCALPARTS)


def _parse_display_name(message_from: str) -> Optional[str]:
    """Extract the display name from a From header. Returns None if none."""
    if not message_from:
        return None
    # Patterns: '"Display Name" <email@domain>', 'Display Name <email@domain>', 'email@domain'
    m = re.match(r'^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$', message_from)
    if m:
        name = m.group(1).strip()
        return name if name and "@" not in name else None
    return None


# SQL expression that extracts the display-name portion of a `From` header.
# For `"Marie Keup" <m.keup@taylorwessing.com>` it returns `"marie keup" `.
# For `ctaylo@uchicago.edu` (no `<...>`) it returns NULL so LIKE comparisons
# don't false-match a substring of the email address.
_DISPLAY_NAME_EXPR = (
    "CASE WHEN messageFrom LIKE '%<%>%' "
    "THEN LOWER(SUBSTR(messageFrom, 1, INSTR(messageFrom, '<') - 1)) "
    "ELSE NULL END"
)


def _build_sender_clause(
    sender_name: Optional[str] = None,
    sender_email: Optional[str] = None,
    sender_domain: Optional[str] = None,
    fuzzy: bool = True,
    body_match_pks: Optional[List[int]] = None,
) -> Tuple[Optional[str], List[Any], List[str]]:
    """Build a SQL fragment AND-ing the structured sender filters.

    For `sender_name`, the substring match runs against the *display name*
    portion of `messageFrom` only — NOT the email address — so "Taylo" no
    longer false-matches `m.keup@taylorwessing.com` or `taylor.swift@...`.

    When `body_match_pks` is supplied, those pks are OR-ed into the sender_name
    branch — this is how the signature body fallback contributes header-less
    rows (e.g. `ctaylo@uchicago.edu` signed "Christine Taylo").

    Returns (clause_or_None, params, fuzzy_matches_used).
    """
    clauses: List[str] = []
    params: List[Any] = []
    fuzzy_used: List[str] = []

    if sender_name:
        sub_clauses = [f"{_DISPLAY_NAME_EXPR} LIKE ?"]
        params.append(f"%{sender_name.lower()}%")
        if fuzzy:
            cands = _localpart_candidates(sender_name)
            for c in cands:
                sub_clauses.append("LOWER(messageFromMailbox) LIKE ?")
                params.append(f"{c}@%")
            fuzzy_used = cands
        if body_match_pks:
            placeholders = ",".join("?" * len(body_match_pks))
            sub_clauses.append(f"pk IN ({placeholders})")
            params.extend(body_match_pks)
        clauses.append("(" + " OR ".join(sub_clauses) + ")")

    if sender_email:
        if "@" in sender_email:
            clauses.append("LOWER(messageFromMailbox) = ?")
            params.append(sender_email.lower())
        else:
            clauses.append("LOWER(messageFromMailbox) LIKE ?")
            params.append(f"%{sender_email.lower()}%@%")

    if sender_domain:
        clauses.append("LOWER(messageFromDomain) = ?")
        params.append(sender_domain.lower())

    if not clauses:
        return None, [], fuzzy_used

    return " AND ".join(clauses), params, fuzzy_used


def _resolve_legacy_sender(sender: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Map a legacy `sender` value to a priority-ordered list of attempts.

    Each attempt is (matched_on, structured_kwargs) tried until one returns rows.
    """
    s = sender.strip()
    attempts: List[Tuple[str, Dict[str, Any]]] = []
    # If it has an @, try email first (highest precision).
    if "@" in s:
        attempts.append(("sender_email", {"sender_email": s}))
    else:
        # Name first (covers display-name substring + fuzzy localpart).
        attempts.append(("sender_name", {"sender_name": s}))
        # If it looks like a localpart (no spaces, ascii-ish), try email substring too.
        if re.match(r"^[A-Za-z0-9._\-]+$", s):
            attempts.append(("sender_email", {"sender_email": s}))
        # If it looks like a domain, try domain.
        if re.search(r"\.[A-Za-z]{2,}$", s):
            attempts.append(("sender_domain", {"sender_domain": s}))
    return attempts


class SparkDatabase:
    """Access Spark Desktop SQLite databases in read-only mode."""

    def __init__(self, base_dir: Optional[Path] = None, cache_dir: Optional[Path] = None):
        """Initialize database connections.

        Args:
            base_dir: Override the Spark core-data directory (for tests).
            cache_dir: Override the Spark cache directory (for tests).
        """
        base = Path(base_dir) if base_dir else SPARK_BASE
        self.base_dir = base
        self.cache_dir = Path(cache_dir) if cache_dir else SPARK_CACHE
        self.messages_db_path = base / "messages.sqlite"
        self.search_db_path = base / "search_fts5.sqlite"
        self.calendar_db_path = base / "calendarsapi.sqlite"

        if not self.messages_db_path.exists():
            raise FileNotFoundError(f"Messages database not found at {self.messages_db_path}")
        if not self.search_db_path.exists():
            raise FileNotFoundError(f"Search database not found at {self.search_db_path}")
        if not self.calendar_db_path.exists():
            raise FileNotFoundError(f"Calendar database not found at {self.calendar_db_path}")

        # Per-thread deadline (time.monotonic() value) enforced on every
        # connection via a progress handler; None means no deadline.
        self._local = threading.local()

    @contextmanager
    def deadline(self, seconds: float):
        """Abort any SQLite work on this thread that runs longer than `seconds`.

        Queries past the deadline are interrupted by the progress handler and
        surface as QueryTimeout, so a pathological query fails fast instead of
        holding the request until the MCP client gives up.
        """
        self._local.deadline = time.monotonic() + seconds
        self._local.timeout = seconds
        try:
            yield
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e) and self._deadline_passed():
                raise QueryTimeout(f"query exceeded {seconds:g}s and was aborted") from e
            raise
        finally:
            self._local.deadline = None

    def _deadline_passed(self) -> bool:
        deadline = getattr(self._local, "deadline", None)
        return deadline is not None and time.monotonic() > deadline

    def _install_deadline(self, conn: sqlite3.Connection) -> None:
        # Returning non-zero from the handler makes SQLite abort the statement
        # with "interrupted".
        conn.set_progress_handler(lambda: 1 if self._deadline_passed() else 0, _PROGRESS_HANDLER_STEPS)

    def _connect(self, db_path: Path) -> sqlite3.Connection:
        """Open a Spark SQLite database read-only, resilient to live writes.

        Spark Desktop keeps these databases in WAL mode and writes to them
        continuously. A plain ``mode=ro`` open has to map the live ``-shm``
        shared-memory file, and when Spark checkpoints or rotates ``-shm``/``-wal``
        between calls the open fails with SQLITE_CANTOPEN ("unable to open
        database file"). We retry a few times to ride out transient checkpoint
        windows, then fall back to ``immutable=1``, which reads the main database
        file directly and ignores ``-wal``/``-shm`` entirely.

        Trade-off: the ``immutable=1`` fallback does not see un-checkpointed rows
        still sitting in the ``-wal`` file, so the very newest emails may be
        missing until Spark checkpoints. The normal ``mode=ro`` path (tried
        first) sees everything.
        """
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = ON")
                self._install_deadline(conn)
                return conn
            except sqlite3.OperationalError as e:
                last_err = e
                time.sleep(0.25 * (attempt + 1))

        # Fallback: immutable open ignores the live -wal/-shm and cannot fail
        # on checkpoint contention. May miss the newest un-checkpointed rows.
        conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        self._install_deadline(conn)
        return conn

    def _connect_messages(self) -> sqlite3.Connection:
        """Connect to messages database in read-only mode with timeout."""
        return self._connect(self.messages_db_path)

    def _connect_search(self) -> sqlite3.Connection:
        """Connect to search database in read-only mode with timeout."""
        return self._connect(self.search_db_path)

    def _connect_calendar(self) -> sqlite3.Connection:
        """Connect to calendar database in read-only mode with timeout."""
        return self._connect(self.calendar_db_path)

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
        folder: Optional[str] = None,
        unread_only: bool = False,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sender: Optional[str] = None,
        sender_name: Optional[str] = None,
        sender_email: Optional[str] = None,
        sender_domain: Optional[str] = None,
        fuzzy: bool = True,
        signature_fallback: bool = True,
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List emails with filtering.

        Args:
            folder: Filter by folder (inbox, sent, drafts, all). If None
                (omitted), defaults to "inbox" UNLESS a sender filter is set,
                in which case it broadens to "all" — archived emails are a
                common source of "not findable" surprises.
            unread_only: Only show unread emails
            start_date: Filter after this ISO date
            end_date: Filter before this ISO date
            sender: Legacy convenience filter; tries name -> email -> domain.
            sender_name: Match display name in From header (substring, case-insensitive)
                or signature body (fallback). Optional localpart heuristics via `fuzzy`.
            sender_email: Exact match if contains '@', else substring of localpart.
            sender_domain: Exact match (case-insensitive) on sender's email domain.
            fuzzy: When matching sender_name, also try localpart variants like
                'ctaylo' or 'christine.taylo' against the email address.
            signature_fallback: When sender_name matches no headers, search the
                message body via FTS5 for the name (catches emails where the
                display name only appears in the signature).
            verbose: Include a `diagnostics` block in the response.
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dict with 'emails' list, 'total' count, and (when verbose) 'diagnostics'.
        """
        has_sender_filter = bool(sender or sender_name or sender_email or sender_domain)
        if folder is None:
            folder = "all" if has_sender_filter else "inbox"

        base_clauses, base_params = self._build_email_base_filters(
            folder, unread_only, start_date, end_date
        )

        emails, total, diagnostics = self._query_emails_with_sender(
            base_clauses,
            base_params,
            sender=sender,
            sender_name=sender_name,
            sender_email=sender_email,
            sender_domain=sender_domain,
            fuzzy=fuzzy,
            signature_fallback=signature_fallback,
            limit=limit,
            offset=offset,
        )

        result: Dict[str, Any] = {'emails': emails, 'total': total}
        if verbose:
            result['diagnostics'] = diagnostics
        return result

    def _build_email_base_filters(
        self,
        folder: str,
        unread_only: bool,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> Tuple[List[str], List[Any]]:
        """Build the non-sender WHERE clauses shared between list/search emails."""
        where_clauses = ["(meta NOT LIKE '%mtid%' OR meta IS NULL)"]
        params: List[Any] = []

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

        return where_clauses, params

    def _query_emails_with_sender(
        self,
        base_clauses: List[str],
        base_params: List[Any],
        sender: Optional[str],
        sender_name: Optional[str],
        sender_email: Optional[str],
        sender_domain: Optional[str],
        fuzzy: bool,
        signature_fallback: bool,
        limit: int,
        offset: int,
    ) -> Tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
        """Run the list_emails query with sender filtering and fallbacks.

        Returns (emails, total, diagnostics).
        """
        diagnostics: Dict[str, Any] = {
            "matched_on": None,
            "fuzzy_matches_used": [],
            "signature_match_used": False,
        }

        def run(where_clauses: List[str], params: List[Any]) -> Tuple[List[Dict[str, Any]], int]:
            return self._run_messages_query(where_clauses, params, limit, offset)

        # Case 1: structured params given (one or more of sender_name/email/domain).
        if sender_name or sender_email or sender_domain:
            body_pks: List[int] = []
            if sender_name and signature_fallback:
                body_pks = self._fts_pks_for_name(sender_name)
                diagnostics["signature_match_used"] = bool(body_pks)
            clause, sparams, fuzzy_used = _build_sender_clause(
                sender_name=sender_name,
                sender_email=sender_email,
                sender_domain=sender_domain,
                fuzzy=fuzzy,
                body_match_pks=body_pks,
            )
            diagnostics["fuzzy_matches_used"] = fuzzy_used
            wc = list(base_clauses) + ([clause] if clause else [])
            params = list(base_params) + sparams
            emails, total = run(wc, params)
            if sender_name:
                diagnostics["matched_on"] = "sender_name"
            elif sender_email:
                diagnostics["matched_on"] = "sender_email"
            else:
                diagnostics["matched_on"] = "sender_domain"
            return emails, total, diagnostics

        # Case 2: legacy `sender` param. Try name -> email -> domain in priority
        # order; return results from the first non-empty branch. The name
        # branch unions header-display-name, fuzzy localpart, AND signature
        # body matches (so sender="Taylo" still finds the Christine Taylo body).
        if sender:
            for matched_on, kwargs in _resolve_legacy_sender(sender):
                body_pks_legacy: List[int] = []
                if matched_on == "sender_name" and signature_fallback:
                    body_pks_legacy = self._fts_pks_for_name(kwargs["sender_name"])
                clause, sparams, fuzzy_used = _build_sender_clause(
                    fuzzy=fuzzy, body_match_pks=body_pks_legacy, **kwargs
                )
                if not clause:
                    continue
                wc = list(base_clauses) + [clause]
                params = list(base_params) + sparams
                emails, total = run(wc, params)
                if total > 0:
                    diagnostics["matched_on"] = matched_on
                    diagnostics["fuzzy_matches_used"] = fuzzy_used
                    diagnostics["signature_match_used"] = bool(body_pks_legacy)
                    return emails, total, diagnostics
            return [], 0, diagnostics

        # Case 3: no sender filter at all.
        emails, total = run(list(base_clauses), list(base_params))
        return emails, total, diagnostics

    def _fts_pks_for_name(self, name: str, limit: int = 500) -> List[int]:
        """Return message pks whose FTS body matches the given name as a phrase.

        Used to surface emails where the sender's name appears only in the
        signature, not the From header. Returns [] if FTS5 is unavailable or
        the name has no usable tokens.
        """
        tokens = [re.sub(r"[^A-Za-z0-9]", "", t) for t in re.split(r"\s+", name.strip())]
        tokens = [t for t in tokens if t]
        if not tokens:
            return []
        fts_phrase = '"' + " ".join(tokens) + '"'
        try:
            search_conn = self._connect_search()
        except sqlite3.OperationalError:
            return []
        try:
            cursor = search_conn.execute(
                "SELECT messagePk FROM messagesfts WHERE searchBody MATCH ? LIMIT ?",
                (fts_phrase, limit),
            )
            return [row['messagePk'] for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            search_conn.close()

    def _run_messages_query(
        self,
        where_clauses: List[str],
        params: List[Any],
        limit: int,
        offset: int,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Execute the standard messages SELECT and return (emails, total)."""
        conn = self._connect_messages()
        try:
            where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
            count_query = f"SELECT COUNT(*) as count FROM messages WHERE {where_clause}"
            cursor = conn.execute(count_query, params)
            total = cursor.fetchone()['count']

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
            cursor = conn.execute(query, list(params) + [limit, offset])
            rows = cursor.fetchall()
        finally:
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
        return emails, total

    def search_emails(
        self,
        query: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sender: Optional[str] = None,
        sender_name: Optional[str] = None,
        sender_email: Optional[str] = None,
        sender_domain: Optional[str] = None,
        fuzzy: bool = True,
        sort_by: str = "relevance",
        verbose: bool = False,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Search emails using full-text search.

        Args:
            query: Search query (FTS5 syntax)
            start_date: Filter after this ISO date
            end_date: Filter before this ISO date
            sender: Legacy convenience filter; tries name -> email -> domain.
            sender_name: Match display name in From header (substring) or
                signature body (fallback). Honors `fuzzy` for localpart heuristics.
            sender_email: Exact match if contains '@', else substring of localpart.
            sender_domain: Exact match (case-insensitive) on sender's email domain.
            fuzzy: When matching sender_name, also try common localpart variants.
            sort_by: "relevance" or "date" (newest first)
            verbose: Include a `diagnostics` block in the response.
            limit: Maximum results

        Returns:
            Dict with 'results' list, 'total' count, and (when verbose) 'diagnostics'.
        """
        diagnostics: Dict[str, Any] = {
            "matched_on": None,
            "fuzzy_matches_used": [],
            "signature_fallback_used": False,
        }

        # With filters or date ordering, the top-by-relevance slice can miss
        # every qualifying message (e.g. a common word plus a recent date
        # window), so filter across the whole match set and only compute
        # snippets for the rows we return.
        filtered = sort_by == "date" or any(
            [start_date, end_date, sender, sender_name, sender_email, sender_domain]
        )
        if filtered:
            fts_rows = self._fts_candidates(query, FILTERED_FTS_CANDIDATE_CAP)
            diagnostics["candidates_scanned"] = len(fts_rows)
            diagnostics["candidates_truncated"] = len(fts_rows) >= FILTERED_FTS_CANDIDATE_CAP
        else:
            fts_rows = self._fts_body_search(query, limit * 2)
        if not fts_rows:
            result: Dict[str, Any] = {'results': [], 'total': 0}
            if verbose:
                result['diagnostics'] = diagnostics
            return result

        results, used_diag = self._filter_fts_by_sender(
            fts_rows,
            start_date=start_date,
            end_date=end_date,
            sender=sender,
            sender_name=sender_name,
            sender_email=sender_email,
            sender_domain=sender_domain,
            fuzzy=fuzzy,
            sort_by=sort_by,
            limit=limit,
        )
        diagnostics.update(used_diag)
        if filtered and results:
            excerpts = self._fts_excerpts(query, [r['messagePk'] for r in results])
            for r in results:
                r['excerpt'] = excerpts.get(r['messagePk'], '')

        result = {'results': results, 'total': len(results)}
        if verbose:
            result['diagnostics'] = diagnostics
        return result

    def _fts_body_search(self, query: str, limit: int) -> List[sqlite3.Row]:
        """Run the FTS5 body search and return matching rows."""
        search_conn = self._connect_search()
        try:
            cursor = search_conn.execute(
                """
                SELECT
                    messagePk,
                    snippet(messagesfts, 4, '<mark>', '</mark>', '...', 64) as excerpt,
                    rank
                FROM messagesfts
                WHERE searchBody MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            )
            return cursor.fetchall()
        finally:
            search_conn.close()

    def _fts_candidates(self, query: str, cap: int) -> List[Dict[str, Any]]:
        """Return up to `cap` FTS matches (pk + rank, best first) without snippets."""
        search_conn = self._connect_search()
        try:
            cursor = search_conn.execute(
                """
                SELECT messagePk, rank
                FROM messagesfts
                WHERE searchBody MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, cap),
            )
            return [
                {'messagePk': row['messagePk'], 'rank': row['rank'], 'excerpt': None}
                for row in cursor.fetchall()
            ]
        finally:
            search_conn.close()

    def _fts_excerpts(self, query: str, message_pks: List[int]) -> Dict[int, str]:
        """Compute FTS snippets for just the given message pks."""
        pk_list = ','.join(str(int(pk)) for pk in message_pks)
        search_conn = self._connect_search()
        try:
            cursor = search_conn.execute(
                f"""
                SELECT
                    messagePk,
                    snippet(messagesfts, 4, '<mark>', '</mark>', '...', 64) as excerpt
                FROM messagesfts
                WHERE searchBody MATCH ? AND messagePk IN ({pk_list})
                """,
                (query,),
            )
            return {row['messagePk']: row['excerpt'] or '' for row in cursor.fetchall()}
        finally:
            search_conn.close()

    def _filter_fts_by_sender(
        self,
        fts_rows: List[sqlite3.Row],
        start_date: Optional[str],
        end_date: Optional[str],
        sender: Optional[str],
        sender_name: Optional[str],
        sender_email: Optional[str],
        sender_domain: Optional[str],
        fuzzy: bool,
        sort_by: str,
        limit: int,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Filter FTS-matched messages by sender and date, return ordered results."""
        diagnostics: Dict[str, Any] = {}
        # pks are integers from the FTS index; inline them rather than binding
        # one parameter each, which can exceed SQLite's variable limit.
        pk_list = ','.join(str(int(row['messagePk'])) for row in fts_rows)
        base_clauses = [
            f"pk IN ({pk_list})",
            "(meta NOT LIKE '%mtid%' OR meta IS NULL)",
        ]
        base_params: List[Any] = []

        if start_date:
            start_ts = int(datetime.fromisoformat(start_date).timestamp())
            base_clauses.append("receivedDate >= ?")
            base_params.append(start_ts)
        if end_date:
            end_ts = int(datetime.fromisoformat(end_date).timestamp())
            base_clauses.append("receivedDate <= ?")
            base_params.append(end_ts)

        # Determine sender clause.
        sender_clause: Optional[str] = None
        sender_params: List[Any] = []
        matched_on: Optional[str] = None
        fuzzy_used: List[str] = []

        if sender_name or sender_email or sender_domain:
            sender_clause, sender_params, fuzzy_used = _build_sender_clause(
                sender_name=sender_name,
                sender_email=sender_email,
                sender_domain=sender_domain,
                fuzzy=fuzzy,
            )
            if sender_name:
                matched_on = "sender_name"
            elif sender_email:
                matched_on = "sender_email"
            else:
                matched_on = "sender_domain"
        elif sender:
            # Legacy: try priority chain, picking the first non-empty match.
            for attempt_label, kwargs in _resolve_legacy_sender(sender):
                clause, sparams, used = _build_sender_clause(fuzzy=fuzzy, **kwargs)
                if not clause:
                    continue
                metadata = self._fetch_message_metadata(
                    base_clauses + [clause], base_params + sparams
                )
                if metadata:
                    sender_clause = clause
                    sender_params = sparams
                    matched_on = attempt_label
                    fuzzy_used = used
                    break
            else:
                metadata = []
        else:
            metadata = None  # sentinel: fetch below

        # If sender filter didn't already resolve metadata, fetch now.
        where_clauses = list(base_clauses)
        params = list(base_params)
        if sender_clause:
            where_clauses.append(sender_clause)
            params.extend(sender_params)
        metadata_rows = self._fetch_message_metadata(where_clauses, params)

        diagnostics["matched_on"] = matched_on
        diagnostics["fuzzy_matches_used"] = fuzzy_used

        # Join FTS results with metadata.
        metadata_map = {row['pk']: row for row in metadata_rows}
        results: List[Dict[str, Any]] = []
        for fts_row in fts_rows:
            pk = fts_row['messagePk']
            if pk in metadata_map:
                meta = metadata_map[pk]
                results.append({
                    'messagePk': pk,
                    'subject': meta['subject'] or '(No Subject)',
                    'sender': meta['sender'] or 'Unknown',
                    'receivedDate': meta['receivedDate'],
                    'receivedTimestamp': meta['receivedTimestamp'],
                    'excerpt': fts_row['excerpt'] or '',
                    'relevanceScore': -fts_row['rank']
                })

        if sort_by == "date":
            results.sort(key=lambda x: x['receivedTimestamp'], reverse=True)

        results = results[:limit]
        for r in results:
            del r['receivedTimestamp']
        return results, diagnostics

    def _fetch_message_metadata(
        self, where_clauses: List[str], params: List[Any]
    ) -> List[sqlite3.Row]:
        """Fetch metadata rows for the join in search_emails."""
        conn = self._connect_messages()
        try:
            where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
            cursor = conn.execute(
                f"""
                SELECT
                    pk,
                    subject,
                    messageFrom as sender,
                    datetime(receivedDate, 'unixepoch') as receivedDate,
                    receivedDate as receivedTimestamp
                FROM messages
                WHERE {where_clause}
                """,
                params,
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def index_status(self, stale_threshold_minutes: int = 30) -> Dict[str, Any]:
        """Report the state of Spark's underlying message store.

        This is a read-only diagnostic. The Spark client owns sync; this MCP
        only reads its store. Use this when a query returns no results to
        distinguish "no such email" from "Spark hasn't fetched it yet".

        Args:
            stale_threshold_minutes: An account is flagged stale if its newest
                message is older than this many minutes. Default: 30.

        Returns:
            Dict with per-account counts/timestamps, folder counts, FTS row
            count, and an `owned_by` note clarifying who manages sync.
        """
        conn = self._connect_messages()
        try:
            cursor = conn.execute(
                """
                SELECT pk, accountTitle, ownerFullName
                FROM accounts
                ORDER BY pk
                """
            )
            accounts_rows = cursor.fetchall()

            accounts: List[Dict[str, Any]] = []
            now_ts = datetime.now().timestamp()
            stale_cutoff = stale_threshold_minutes * 60
            stale_any = False

            for acc in accounts_rows:
                acc_pk = acc['pk']
                cursor = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total,
                        MIN(receivedDate) as oldest_ts,
                        MAX(receivedDate) as newest_ts
                    FROM messages
                    WHERE accountPk = ?
                      AND (meta NOT LIKE '%mtid%' OR meta IS NULL)
                    """,
                    (acc_pk,),
                )
                msg_row = cursor.fetchone()
                total = msg_row['total'] or 0
                newest_ts = msg_row['newest_ts']
                oldest_ts = msg_row['oldest_ts']

                # Per-folder counts (top-level folders by name).
                cursor = conn.execute(
                    """
                    SELECT folderName, COUNT(*) as count
                    FROM folders
                    WHERE accountPk = ?
                    GROUP BY folderName
                    ORDER BY folderName
                    """,
                    (acc_pk,),
                )
                folder_counts = [
                    {"name": row['folderName'], "folder_count": row['count']}
                    for row in cursor.fetchall()
                ]

                is_stale = bool(
                    newest_ts is not None and (now_ts - newest_ts) > stale_cutoff
                )
                if is_stale or total == 0:
                    stale_any = True

                accounts.append({
                    "accountPk": acc_pk,
                    "accountTitle": acc['accountTitle'],
                    "ownerFullName": acc['ownerFullName'],
                    "totalMessages": total,
                    "newestMessageAt": (
                        datetime.fromtimestamp(newest_ts).isoformat()
                        if newest_ts else None
                    ),
                    "oldestMessageAt": (
                        datetime.fromtimestamp(oldest_ts).isoformat()
                        if oldest_ts else None
                    ),
                    "stale": is_stale,
                    "folders": folder_counts,
                })
        finally:
            conn.close()

        # FTS row count.
        fts_row_count = 0
        try:
            search_conn = self._connect_search()
            try:
                cursor = search_conn.execute(
                    "SELECT COUNT(*) as count FROM messagesfts"
                )
                fts_row_count = cursor.fetchone()['count'] or 0
            finally:
                search_conn.close()
        except sqlite3.OperationalError:
            fts_row_count = -1

        return {
            "accounts": accounts,
            "fts_index": {"row_count": fts_row_count},
            "anyStale": stale_any,
            "staleThresholdMinutes": stale_threshold_minutes,
            "owned_by": (
                "Spark Desktop owns sync. This MCP reads its store read-only; "
                "to force a refresh, open Spark and let it fetch new mail."
            ),
        }

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
