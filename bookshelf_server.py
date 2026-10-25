#!/usr/bin/env python3
"""bookshelf-mcp-server

A small MCP server (Python stdlib only) that exposes a personal reading list
over stdio. Add books with notes and quotes, search by theme, pull saved
quotes, and mark books finished with a one-line take.

Storage is a single JSON file with path safety and automatic rotating backups.

Copyright (c) 2026 Asdale Dynamics, LLC. MIT licensed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SERVER_NAME = "bookshelf-mcp-server"
SERVER_VERSION = "1.0.0"
STORE_VERSION = 1
SCHEMA_VERSION = "1"
MAX_TEXT_LEN = 5000
MAX_LIST_ITEMS = 200
STATUSES = ("want to read", "reading", "finished")
MAX_BACKUPS = 3

DEFAULT_DATA_DIR = Path(
    os.environ.get("BOOKSHELF_DATA_DIR", Path.cwd() / "bookshelf_data")
)
DEFAULT_FILENAME = "bookshelf.json"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "add_book",
        "description": "Add a book to the shelf with optional themes, notes, and quotes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Book title (required)"},
                "author": {"type": "string", "description": "Author name"},
                "themes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Theme tags, e.g. ['focus', 'local-ai']",
                },
                "notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Initial notes",
                },
                "quotes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Initial quotes to save verbatim",
                },
                "status": {
                    "type": "string",
                    "enum": list(STATUSES),
                    "description": "Initial reading status (default: 'want to read')",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_book",
        "description": "Get one book by id, including notes, quotes, and take.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string", "description": "Book id, e.g. 'b-1'"}
            },
            "required": ["book_id"],
        },
    },
    {
        "name": "add_note",
        "description": "Append a note to a book.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string"},
                "note": {"type": "string", "description": "The note text"},
            },
            "required": ["book_id", "note"],
        },
    },
    {
        "name": "add_quote",
        "description": "Save a quote from a book, optionally with a page number.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string"},
                "quote": {"type": "string", "description": "The quote text verbatim"},
                "page": {"type": "string", "description": "Page or location, e.g. '42'"},
            },
            "required": ["book_id", "quote"],
        },
    },
    {
        "name": "search_books",
        "description": "Search the shelf by theme and/or free text across titles, authors, notes, and quotes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "description": "Theme tag to match (case-insensitive substring)"},
                "query": {"type": "string", "description": "Free-text query"},
            },
        },
    },
    {
        "name": "get_quotes",
        "description": "Pull saved quotes for writing, filtered by book and/or theme.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string"},
                "theme": {"type": "string", "description": "Only quotes from books with this theme"},
            },
        },
    },
    {
        "name": "mark_finished",
        "description": "Mark a book finished and record a one-line take.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string"},
                "take": {"type": "string", "description": "Your one-line take on the book"},
            },
            "required": ["book_id", "take"],
        },
    },
    {
        "name": "list_books",
        "description": "List all books, optionally filtered by status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": list(STATUSES)}
            },
        },
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PathError(ValueError):
    """Raised when a data-file path fails safety checks."""


def resolve_store_path(user_path: str | None, base_dir: Path) -> Path:
    """Resolve the target data file strictly inside base_dir.

    Rules: relative names stay inside base_dir, '..' components are rejected,
    existing symlinks are followed and the real path must still be inside
    base_dir, only .json filenames are allowed, and dotfiles are rejected.
    """
    base = base_dir.expanduser().resolve()
    if user_path is None or str(user_path).strip() == "":
        user_path = DEFAULT_FILENAME
    candidate = Path(user_path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        if ".." in candidate.parts:
            raise PathError("path traversal rejected: '..' is not allowed")
        resolved = (base / candidate).resolve()
    if resolved != base and base not in resolved.parents:
        raise PathError(f"path escapes the data directory {base}")
    if resolved.suffix.lower() != ".json":
        raise PathError("data file must have a .json extension")
    if resolved.name.startswith("."):
        raise PathError("hidden files are not allowed")
    return resolved


class BookshelfStore:
    """JSON-file-backed reading list with auto-backup on every write."""

    def __init__(self, path: Path, max_backups: int = MAX_BACKUPS):
        self.path = Path(path)
        self.max_backups = max_backups
        self.books: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.books = []
            return
        raw = self.path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"data file {self.path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("data file must contain a JSON object")
        if data.get("version") != STORE_VERSION:
            raise ValueError(f"unsupported data version: {data.get('version')!r}")
        books = data.get("books", [])
        if not isinstance(books, list):
            raise ValueError("'books' must be a list")
        for book in books:
            if not isinstance(book, dict) or not isinstance(book.get("id"), str):
                raise ValueError("every book needs a string id")
        self.books = books

    def save(self) -> None:
        """Back up the previous file, then write atomically via a temp file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.max_backups > 0:
            for i in range(self.max_backups - 1, 0, -1):
                src = self.path.with_name(self.path.name + f".{i}")
                dst = self.path.with_name(self.path.name + f".{i + 1}")
                if src.exists():
                    src.replace(dst)
            self.path.replace(self.path.with_name(self.path.name + ".1"))
        payload = {
            "version": STORE_VERSION,
            "updated": now_iso(),
            "books": self.books,
        }
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _next_id(self) -> str:
        max_n = 0
        for b in self.books:
            if str(b.get("id", "")).startswith("b-"):
                try:
                    max_n = max(max_n, int(b["id"][2:]))
                except ValueError:
                    continue
        return f"b-{max_n + 1}"

    def _get(self, book_id: str) -> dict[str, Any]:
        for b in self.books:
            if b["id"] == book_id:
                return b
        raise KeyError(f"no book with id {book_id!r}")

    @staticmethod
    def _clean_text(value: Any, what: str, max_len: int = MAX_TEXT_LEN) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{what} must not be empty")
        if len(text) > max_len:
            raise ValueError(f"{what} exceeds {max_len} characters")
        return text

    @staticmethod
    def _clean_themes(themes: Any) -> list[str]:
        if themes is None:
            return []
        if not isinstance(themes, list):
            raise ValueError("themes must be a list of strings")
        cleaned = []
        for t in themes[:MAX_LIST_ITEMS]:
            t_clean = str(t).strip().lower()
            if t_clean:
                cleaned.append(t_clean)
        return sorted(set(cleaned))

    def add_book(
        self,
        title: Any,
        author: Any = "",
        themes: Any = None,
        notes: Any = None,
        quotes: Any = None,
        status: Any = "want to read",
    ) -> dict[str, Any]:
        title = self._clean_text(title, "title", 300)
        author = str(author).strip()[:200]
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        book: dict[str, Any] = {
            "id": self._next_id(),
            "title": title,
            "author": author,
            "status": status,
            "themes": self._clean_themes(themes),
            "notes": [],
            "quotes": [],
            "take": "",
            "finished_on": None,
            "created": now_iso(),
            "updated": now_iso(),
        }
        for note in notes or []:
            book["notes"].append({"text": self._clean_text(note, "note"), "added": now_iso()})
        for quote in quotes or []:
            book["quotes"].append({"text": self._clean_text(quote, "quote"), "page": "", "added": now_iso()})
        if len(book["notes"]) >= MAX_LIST_ITEMS or len(book["quotes"]) >= MAX_LIST_ITEMS:
            raise ValueError(f"too many items per book (limit {MAX_LIST_ITEMS})")
        self.books.append(book)
        self.save()
        return book

    def get_book(self, book_id: str) -> dict[str, Any]:
        return self._get(str(book_id))

    def _append(self, book_id: str, key: str, entry: dict[str, Any]) -> dict[str, Any]:
        book = self._get(book_id)
        if len(book[key]) >= MAX_LIST_ITEMS:
            raise ValueError(f"too many {key} on this book (limit {MAX_LIST_ITEMS})")
        book[key].append(entry)
        book["updated"] = now_iso()
        self.save()
        return book

    def add_note(self, book_id: str, note: Any) -> dict[str, Any]:
        text = self._clean_text(note, "note")
        return self._append(book_id, "notes", {"text": text, "added": now_iso()})

    def add_quote(self, book_id: str, quote: Any, page: Any = "") -> dict[str, Any]:
        text = self._clean_text(quote, "quote")
        return self._append(
            book_id,
            "quotes",
            {"text": text, "page": str(page).strip()[:50], "added": now_iso()},
        )

    @staticmethod
    def _matches(book: dict[str, Any], theme: str | None, query: str | None) -> bool:
        if theme:
            tag = theme.strip().lower()
            if tag and tag not in {t.lower() for t in book.get("themes", [])}:
                return False
        if query:
            q = query.strip().lower()
            if q:
                haystack = " ".join(
                    [
                        book.get("title", ""),
                        book.get("author", ""),
                        book.get("take", ""),
                        " ".join(book.get("themes", [])),
                        " ".join(n.get("text", "") for n in book.get("notes", [])),
                        " ".join(quot.get("text", "") for quot in book.get("quotes", [])),
                    ]
                ).lower()
                if q not in haystack:
                    return False
        return True

    def search_books(self, theme: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        return [b for b in self.books if self._matches(b, theme, query)]

    def get_quotes(self, book_id: str | None = None, theme: str | None = None) -> list[dict[str, Any]]:
        if book_id:
            books = [self._get(book_id)]
        else:
            books = self.books
        out: list[dict[str, Any]] = []
        for b in books:
            if not self._matches(b, theme, None):
                continue
            for q in b.get("quotes", []):
                out.append({"book_id": b["id"], "book_title": b["title"], **q})
        return out

    def mark_finished(self, book_id: str, take: Any) -> dict[str, Any]:
        word_limit = 60
        take = self._clean_text(take, "take")
        words = take.split()
        if len(words) > word_limit:
            raise ValueError(f"take must be a one-line take ({word_limit} words max)")
        book = self._get(book_id)
        book["status"] = "finished"
        book["take"] = take
        book["finished_on"] = now_iso()[:10]
        book["updated"] = now_iso()
        self.save()
        return book

    def list_books(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status != "":
            if status not in STATUSES:
                raise ValueError(f"status must be one of {STATUSES}")
            return [b for b in self.books if b["status"] == status]
        return list(self.books)


class ToolError(Exception):
    pass


def call_tool(store: BookshelfStore, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "add_book":
            book = store.add_book(
                title=args.get("title"),
                author=args.get("author", ""),
                themes=args.get("themes"),
                notes=args.get("notes"),
                quotes=args.get("quotes"),
                status=args.get("status", "want to read"),
            )
            return {"id": book["id"], "title": book["title"], "status": book["status"]}
        if name == "get_book":
            return store.get_book(args["book_id"])
        if name == "add_note":
            book = store.add_note(args["book_id"], args.get("note"))
            return {"id": book["id"], "notes_count": len(book["notes"])}
        if name == "add_quote":
            book = store.add_quote(args["book_id"], args.get("quote"), args.get("page", ""))
            return {"id": book["id"], "quotes_count": len(book["quotes"])}
        if name == "search_books":
            matches = store.search_books(args.get("theme"), args.get("query"))
            return {"count": len(matches), "books": matches}
        if name == "get_quotes":
            quotes = store.get_quotes(args.get("book_id") or None, args.get("theme") or None)
            return {"count": len(quotes), "quotes": quotes}
        if name == "mark_finished":
            book = store.mark_finished(args["book_id"], args.get("take"))
            return {"id": book["id"], "status": book["status"], "take": book["take"]}
        if name == "list_books":
            books = store.list_books(args.get("status"))
            summary = [
                {
                    k: b[k]
                    for k in ("id", "title", "author", "status", "themes", "take")
                }
                for b in books
            ]
            return {"count": len(summary), "books": summary}
        raise ToolError(f"unknown tool: {name}")
    except KeyError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def protocol_loop(store: BookshelfStore, reader=None, writer=None) -> None:
    """Run the MCP stdio loop. Messages are newline-delimited JSON-RPC 2.0."""
    reader = reader if reader is not None else sys.stdin
    writer = writer if writer is not None else sys.stdout

    def send(msg: dict[str, Any]) -> None:
        writer.write(json.dumps(msg, ensure_ascii=False) + "\n")
        writer.flush()

    for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "parse error"},
                }
            )
            continue
        method = msg.get("method", "")
        msg_id = msg.get("id")
        if method == "initialize":
            requested = str(msg.get("params", {}).get("protocolVersion", SCHEMA_VERSION))
            supported = ("2024-11-05", "2025-03-26", "2025-06-18")
            version = requested if requested in supported else SCHEMA_VERSION
            send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": version,
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": SERVER_NAME,
                            "version": SERVER_VERSION,
                        },
                    },
                }
            )
            continue
        if method in ("notifications/initialized", "initialized"):
            continue
        if method == "ping":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            continue
        if method == "tools/list":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": TOOLS},
                }
            )
            continue
        if method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            args = params.get("arguments", {}) or {}
            try:
                result = tool_call(store, tool_name, args)
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(result, indent=2, ensure_ascii=False),
                                }
                            ]
                        },
                    }
                )
            except ToolError as exc:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "isError": True,
                            "content": [{"type": "text", "text": str(exc)}],
                        },
                    }
                )
            continue
        if method == "shutdown":
            continue
        if msg_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            )
    return None


def tool_call(store: BookshelfStore, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return call_tool(store, name, args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=SERVER_NAME)
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        help="Directory allowed to contain the data file (default: ./bookshelf_data or BOOKSHELF_DATA_DIR)",
    )
    parser.add_argument(
        "--data",
        default=None,
        help="Data file name/path inside the data dir (default: bookshelf.json)",
    )
    parser.add_argument(
        "--seed-sample",
        action="store_true",
        help="Seed the shelf with sample books on first run",
    )
    args = parser.parse_args(argv)

    base_dir = Path(args.data_dir).expanduser()
    try:
        store_path = resolve_store_path(args.data, base_dir)
    except PathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    store = BookshelfStore(store_path)
    if not store.books and args.seed_sample:
        sample_path = Path(__file__).resolve().parent / "sample_shelf.json"
        if sample_path.exists():
            try:
                sample = json.loads(sample_path.read_text(encoding="utf-8"))
                for s in sample.get("books", []):
                    base = dict(s)
                    base.setdefault("themes", s.get("themes", []))
                    store.add_book(
                        title=base.get("title"),
                        author=base.get("author", ""),
                        themes=base.get("themes"),
                        notes=base.get("notes"),
                        quotes=base.get("quotes"),
                        status=base.get("status", "want to read"),
                    )
            except (json.JSONDecodeError, ValueError, PathError) as exc:
                print(f"warning: could not seed sample shelf: {exc}", file=sys.stderr)

    protocol_loop(store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
