# bookshelf-mcp-server

A small MCP server (stdio, Python standard library only) that exposes your
personal reading list. Add books with notes and quotes, search by theme, pull
saved quotes when you're writing, and mark books finished with a one-line take.

Storage is a single JSON file with path safety and automatic rotating backups.
Pairs well with [notes-mcp-server](https://www.npmjs.com/search?q=notes-mcp-server)
and flashcards-mcp-server for a local-first reading/writing workflow.

Built by **Asdale Dynamics, LLC** as part of the beginner-friendly AI tooling
behind Dylan Asdale's *The Massive AI Manual* and
[dylanasdale.github.io](https://dylanasdale.github.io).

## Tools

| Tool | What it does |
| --- | --- |
| `add_book` | Add a book with title (required), author, themes, notes, quotes, status |
| `get_book` | Fetch one book by id, notes and quotes included |
| `add_note` | Append a note to a book |
| `add_quote` | Save a quote verbatim, optionally with a page |
| `search_books` | Search by theme and/or free text across titles, authors, notes, quotes |
| `get_quotes` | Pull saved quotes for writing, filtered by book and/or theme |
| `mark_finished` | Mark finished and record your one-line take |
| `list_books` | List all books, optionally filtered by status |

## Requirements

- Python 3.10+ (standard library only, no pip installs)

## Run it

```bash
python3 bookshelf_server.py --seed-sample
```

The server speaks MCP over stdio, so normal use is configuring it in an MCP
client instead of running it by hand.

### MCP client config

```json
{
  "mcpServers": {
    "bookshelf": {
      "command": "python3",
      "args": [
        "/path/to/bookshelf-mcp-server/bookshelf_server.py"
      ],
      "env": { "BOOKSHELF_DATA_DIR": "/path/to/bookshelf-mcp-server/bookshelf_data" }
    }
  }
}
```

## Storage, path safety, backup

- Data lives in one JSON file: `$BOOKSHELF_DATA_DIR/bookshelf.json`
  (defaults to `./bookshelf_data/bookshelf.json`, or use `--data-dir` / `--data`).
- Path safety: the data file is always resolved strictly inside the data
  directory. `..`, absolute paths outside the directory, symlinks that escape,
  non-`.json` names, and hidden files are rejected.
- Writes are atomic (temp file + rename), and every write first rotates the
  previous file into `bookshelf.json.1`, `.2`, `.3` (up to 3 backups).
- `sample_shelf.json` is included; run with `--seed-sample` once to copy its
  books into an empty shelf. No secrets and no personal data go in your shelf.

## Smoke-test guide

Quick manual test straight through the wire (no client needed). Make a data
directory first so `initialize` doesn't auto-create anything you didn't expect:

### 1. Handshake + tool list

```bash
mkdir -p bookshelf_data
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 bookshelf_server.py --data-dir bookshelf_data --seed-sample
```

Response 1 echoes the protocol version and server info; response 2 lists all
eight tools. `--seed-sample` was copied from `sample_shelf.json` only if the
shelf file did not exist yet.

### 2. Full round trip

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add_book","arguments":{"title":"Deep Work","author":"Cal Newport","themes":["focus"]}}}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_books","arguments":{"theme":"focus"}}}' \
  | python3 bookshelf_server.py --data-dir bookshelf_data
```

Line 2 (`add_book`) should echo the new book id (`b-1` unless seeded) and
line 3 (`search_books`) should return `count: 1`.

### 3. Verify storage

```bash
cat bookshelf_data/bookshelf.json
```

Change something with another write; `bookshelf.json.1` should then exist as
the auto-backup of the state before that write.

### 4. Security check

```bash
python3 bookshelf_server.py --data '../evil.json'
> error: path traversal rejected: '..' is not allowed
```

## Tests

```bash
python3 -m unittest discover -s tests -v     # from repo root
```

Covers storage round-trips, theme/quote search, status filters, backup
rotation, invalid inputs, corrupt files, and the path-safety/security cases
(traversal, outside-base absolutes, symlink escapes).

## License

MIT — Copyright (c) 2026 Asdale Dynamics, LLC. See [LICENSE](LICENSE).
