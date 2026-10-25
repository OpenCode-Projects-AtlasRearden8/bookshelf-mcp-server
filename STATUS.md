# STATUS — bookshelf-mcp-server

Last updated: 2026-10-25

## What was built

A personal reading-list MCP server for Dylan Asdale's local-first reading and
writing workflow (Asdale Dynamics, LLC). Python 3.10+ standard library only —
no third-party dependencies.

Features:

- **8 MCP tools over stdio**: `add_book`, `get_book`, `add_note`, `add_quote`,
  `search_books`, `get_quotes`, `mark_finished`, `list_books`.
- Add books with notes and quotes; search by theme or free text across titles,
  authors, notes, and quotes; pull saved quotes (filter by book/theme) for
  writing; mark finished with a one-line take (60-word cap enforces the "one
  line" rule).
- **JSON storage** (`bookshelf.json`) with explicit `version` field and
  validation on load (corrupt JSON / wrong version rejected).
- **Path safety**: data file is always resolved strictly inside the data dir;
  `..`-traversal, absolute paths outside it, symlink escapes, non-`.json`
  names, and hidden files are rejected with a clear error and exit code 2.
- **Auto-backup + atomic writes**: every write first rotates the previous file
  into `bookshelf.json.1/.2/.3` (cap 3), then writes via temp-file rename.
- **Sample shelf included** (`sample_shelf.json`); `--seed-sample` copies it
  into an empty shelf, and re-running with `--seed-sample` does not duplicate.

## How to run

```bash
# Server (talks MCP JSON-RPC over stdio)
python3 bookshelf_server.py [--data-dir DIR] [--data NAME.json] [--seed-sample]
```

- Data: `$BOOKSHELF_DATA_DIR/bookshelf.json` (env) or `./bookshelf_data/…`
  (default), or `--data-dir`/`--data` flags.
- MCP client config example is in `README.md`.

## Verification results

- `python3 -m unittest discover -s tests` — **36 tests, all pass**, covering:
  - storage round-trips (add/reload, notes, quotes, finish, id continuity),
  - search/quote/status filtering,
  - backup rotation and no leftover temp files,
  - input validation (empty fields, overlong text, unknown ids, one-line take limit),
  - corrupt/foreign-version data files,
  - security cases: `../` traversal, nested traversal, absolute-outside-base,
    symlink escape, non-`.json`, hidden files,
  - protocol level: initialize handshake (client version echoed within
    supported set), `tools/list`, `tools/call` round-trip through the wire,
    tool errors reported as `isError`, unknown methods, parse errors.
- Live smoke test via printf|python3: initialize → tools/list → add_book →
  get_quotes(theme) → mark_finished → search_books all returned correct
  results; data file on disk matched.
- `--seed-sample` verified: seeds 3 sample books, idempotent on re-run.
- Path-safety CLI check verified: `--data ../evil.json` → error + exit 2.

## How it fits the family

Complements notes-mcp-server and flashcards-mcp-server: one JSON file per
concern, same stdlib-only style, same backup/atomic-write conventions.

## Known gaps / future ideas

- `--seed-sample` recreates the sample via `add_book`, so a sample book that
  was already `finished` (with its `take`/`finished_on`) loses the take on
  seeding; only title/author/status/themes/notes/quotes carry over.
- Theme matching is exact tag compare (case-insensitive), not fuzzy.
- No pagination listing; fine for personal shelf sizes.
- Protocol version advertised defaults to `"1"` for unsupported/missing
  client versions rather than failing; `ping` and `tools/*` implemented,
  resources/prompts not (not needed for this server's purpose).
- No multi-shelf support; one JSON file per server instance.
