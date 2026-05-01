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
    build_index(input_paths, args.output, strict=args.strict)
    return 0


if name == "main":
    raise SystemExit(main(sys.argv[1:]))