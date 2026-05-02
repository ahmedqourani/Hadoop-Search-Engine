#!/usr/bin/env python3
"""Build a SQLite index from Hadoop/MapReduce-style word counts.

Expected input format per line (tab-separated):

  <word>\t<file_key>:<count>[, <file_key>:<count>...]

In this workspace, <file_key> is typically base64 for a URL/path and count is an int.

Output SQLite schema:
  - terms(id, term)
  - files(id, raw_key, decoded_key)
  - postings(term_id, file_id, count)

Example:
  /usr/bin/python3 build_sqlite_index.py --input part-00000 --output index.sqlite
  /usr/bin/python3 build_sqlite_index.py --output index.sqlite --query aars
"""

from __future__ import annotations

import argparse
import base64
import glob
import os
import re
import sqlite3
import sys
from typing import Iterable, Iterator, Tuple


def _decode_base64_maybe(value: str) -> str | None:
    """Best-effort base64 decode; returns None if it doesn't look like base64."""
    # In this dataset, the raw key is often "<base64(url)>.txt".
    candidates: list[tuple[str, str]] = [(value, "")]
    if "." in value:
        prefix, suffix = value.split(".", 1)
        candidates.append((prefix, "." + suffix))

    for candidate, suffix in candidates:
        padded = candidate + ("=" * (-len(candidate) % 4))

        decoded: bytes | None = None
        # 1) Strict standard base64.
        try:
            decoded = base64.b64decode(padded, validate=True)
        except Exception:
            decoded = None

        # 2) Strict URL-safe base64 by translating to standard alphabet.
        if decoded is None:
            try:
                standard = padded.replace("-", "+").replace("_", "/")
                decoded = base64.b64decode(standard, validate=True)
            except Exception:
                decoded = None

        if decoded is None:
            continue

        try:
            decoded_text = decoded.decode("utf-8")
        except Exception:
            decoded_text = decoded.decode("utf-8", errors="replace")

        return decoded_text + suffix

    return None


def _parse_postings(postings: str, *, strict: bool, context: str) -> Iterator[Tuple[str, int]]:
    """Parse the postings list.

    We primarily split by ",<whitespace>" to avoid breaking on commas that might
    appear in URLs/paths. If we still encounter malformed chunks (e.g. a truncated
    last line), we either warn and skip or raise in strict mode.
    """

    # Typical format uses ", " as separator; tolerate any whitespace after comma.
    chunks = re.split(r",\s+", postings.strip()) if postings.strip() else []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        # file_key may contain ':' in other datasets; count is always the last ':' suffix.
        try:
            file_key, count_str = chunk.rsplit(":", 1)
        except ValueError:
            msg = f"{context}: Skipping malformed posting (missing ':count'): {chunk!r}"
            if strict:
                raise ValueError(msg)
            print(msg, file=sys.stderr)
            continue

        file_key = file_key.strip()
        if not file_key:
            msg = f"{context}: Skipping malformed posting (empty file key): {chunk!r}"
            if strict:
                raise ValueError(msg)
            print(msg, file=sys.stderr)
            continue

        try:
            count = int(count_str)
        except ValueError:
            msg = f"{context}: Skipping malformed posting count {count_str!r} in {chunk!r}"
            if strict:
                raise ValueError(msg)
            print(msg, file=sys.stderr)
            continue

        yield file_key, count


def _normalize_term(term: str, *, min_term_length: int) -> str | None:
    term = term.strip().casefold()
    if not term:
        return None
    if len(term) < min_term_length:
        return None
    return term


def iter_records(
    input_paths: Iterable[str], *, strict: bool, min_term_length: int
) -> Iterator[Tuple[str, str, int]]:
    for path in input_paths:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    term, postings = line.split("\t", 1)
                except ValueError:
                    raise ValueError(f"{path}:{line_no}: Expected a tab-separated line")
                term = _normalize_term(term, min_term_length=min_term_length)
                if not term:
                    continue
                context = f"{path}:{line_no} (term={term!r})"
                for file_key, count in _parse_postings(postings, strict=strict, context=context):
                    yield term, file_key, count


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS terms (
            id   INTEGER PRIMARY KEY,
            term TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS files (
            id          INTEGER PRIMARY KEY,
            raw_key     TEXT NOT NULL UNIQUE,
            decoded_key TEXT
        );

        CREATE TABLE IF NOT EXISTS postings (
            term_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            count   INTEGER NOT NULL,
            PRIMARY KEY (term_id, file_id),
            FOREIGN KEY (term_id) REFERENCES terms(id),
            FOREIGN KEY (file_id) REFERENCES files(id)
        );

        CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(term);
        CREATE INDEX IF NOT EXISTS idx_postings_term ON postings(term_id);
        CREATE INDEX IF NOT EXISTS idx_postings_file ON postings(file_id);
        """
    )


def build_index(
    input_paths: list[str],
    output_path: str,
    *,
    strict: bool,
    min_term_length: int,
) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    conn = sqlite3.connect(output_path)
    try:
        _init_db(conn)
        cur = conn.cursor()

        term_id_cache: dict[str, int] = {}
        file_id_cache: dict[str, int] = {}

        def get_term_id(term: str) -> int:
            cached = term_id_cache.get(term)
            if cached is not None:
                return cached
            cur.execute("INSERT OR IGNORE INTO terms(term) VALUES (?)", (term,))
            cur.execute("SELECT id FROM terms WHERE term = ?", (term,))
            term_id = int(cur.fetchone()[0])
            term_id_cache[term] = term_id
            return term_id

        def get_file_id(raw_key: str) -> int:
            cached = file_id_cache.get(raw_key)
            if cached is not None:
                return cached
            decoded = _decode_base64_maybe(raw_key)
            cur.execute(
                "INSERT OR IGNORE INTO files(raw_key, decoded_key) VALUES (?, ?)",
                (raw_key, decoded),
            )
            cur.execute("SELECT id FROM files WHERE raw_key = ?", (raw_key,))
            file_id = int(cur.fetchone()[0])
            file_id_cache[raw_key] = file_id
            return file_id

        inserted = 0
        conn.execute("BEGIN")
        for term, raw_key, count in iter_records(
            input_paths,
            strict=strict,
            min_term_length=min_term_length,
        ):
            term_id = get_term_id(term)
            file_id = get_file_id(raw_key)
            cur.execute(
                "INSERT OR REPLACE INTO postings(term_id, file_id, count) VALUES (?, ?, ?)",
                (term_id, file_id, count),
            )
            inserted += 1
            if inserted % 50000 == 0:
                conn.commit()
                conn.execute("BEGIN")

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM terms")
        terms_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM files")
        files_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM postings")
        postings_count = cur.fetchone()[0]

        print(
            f"Built {output_path} from {len(input_paths)} file(s): "
            f"terms={terms_count}, files={files_count}, postings={postings_count}"
        )
    finally:
        conn.close()


def query_term(db_path: str, term: str, limit: int = 50) -> int:
    term = term.strip().casefold()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COALESCE(files.decoded_key, files.raw_key) AS file,
                postings.count
            FROM postings
            JOIN terms ON terms.id = postings.term_id
            JOIN files ON files.id = postings.file_id
            WHERE terms.term = ?
            ORDER BY postings.count DESC, file ASC
            LIMIT ?
            """,
            (term, limit),
        )
        rows = cur.fetchall()

        if not rows:
            print(f"No results for term={term!r}")
            return 1

        for file_path, count in rows:
            print(f"{count}\t{file_path}")
        return 0
    finally:
        conn.close()


def _expand_inputs(inputs: list[str]) -> list[str]:
    expanded: list[str] = []
    for item in inputs:
        matches = glob.glob(item)
        if matches:
            expanded.extend(matches)
        else:
            expanded.append(item)
    # Keep deterministic ordering.
    expanded = sorted(dict.fromkeys(expanded))
    missing = [p for p in expanded if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(f"Input file(s) not found: {missing}")
    return expanded


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Convert word-count text to a searchable SQLite index")
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Input file path(s) or glob(s). Can be repeated. Default: ./part-*",
    )
    parser.add_argument("--output", default="index.sqlite", help="Output sqlite path")
    parser.add_argument("--query", help="If set, query this term instead of building")
    parser.add_argument("--limit", type=int, default=50, help="Max rows to print for --query")
    parser.add_argument(
        "--min-term-length",
        type=int,
        default=3,
        help="Skip terms shorter than this length",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail fast on malformed input instead of skipping bad postings",
    )

    args = parser.parse_args(argv)

    if args.query:
        if not os.path.exists(args.output):
            print(f"DB not found: {args.output}", file=sys.stderr)
            return 2
        return query_term(args.output, args.query, args.limit)

    inputs = args.input or ["part-*"]
    input_paths = _expand_inputs(inputs)
    build_index(
        input_paths,
        args.output,
        strict=args.strict,
        min_term_length=args.min_term_length,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
