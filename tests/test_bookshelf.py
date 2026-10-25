#!/usr/bin/env python3
"""Unit tests for bookshelf-mcp-server: round-trips and security cases."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookshelf_server import (  # noqa: E402
    BookshelfStore,
    PathError,
    ToolError,
    protocol_loop,
    resolve_store_path,
    tool_call,
)


def make_store(tmp: Path, name: str = "shelf.json") -> BookshelfStore:
    return BookshelfStore(tmp / name, max_backups=2)


class StoreRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = make_store(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_add_and_reload_round_trip(self) -> None:
        b = self.store.add_book(
            title="The Massive AI Manual",
            author="Dylan Asdale",
            themes=["ai", "beginners"],
            notes=["Chapter 1 explains agents clearly."],
            quotes=["Explain like a friend would."],
        )
        self.assertEqual(b["id"], "b-1")
        reloaded = BookshelfStore(self.tmp / "shelf.json", max_backups=2)
        book = reloaded.get_book("b-1")
        self.assertEqual(book["title"], "The Massive AI Manual")
        self.assertEqual(book["quotes"][0]["text"], "Explain like a friend would.")
        self.assertEqual(book["themes"], ["ai", "beginners"])

    def test_add_note_and_quote_round_trip(self) -> None:
        self.store.add_book(title="Deep Work", author="Cal Newport")
        self.store.add_note("b-1", "Rituals beat willpower.")
        self.store.add_quote("b-1", "Attention is the new IQ.", page="42")
        reloaded = BookshelfStore(self.tmp / "shelf.json", max_backups=2)
        book = reloaded.get_book("b-1")
        self.assertEqual(book["notes"][0]["text"], "Rituals beat willpower.")
        self.assertEqual(book["quotes"][0]["page"], "42")

    def test_mark_finished_round_trip(self) -> None:
        self.store.add_book(title="Deep Work")
        self.store.mark_finished("b-1", "Focus is a skill you can train.")
        reloaded = BookshelfStore(self.tmp / "shelf.json", max_backups=2)
        book = reloaded.get_book("b-1")
        self.assertEqual(book["status"], "finished")
        self.assertEqual(book["take"], "Focus is a skill you can train.")
        self.assertTrue(book["finished_on"])

    def test_ids_increment_across_reloads(self) -> None:
        self.store.add_book(title="A")
        store2 = BookshelfStore(self.tmp / "shelf.json", max_backups=2)
        b = store2.add_book(title="B")
        self.assertEqual(b["id"], "b-2")

    def test_search_by_theme_and_query(self) -> None:
        self.store.add_book(title="Book A", themes=["focus"])
        self.store.add_book(title="Book B", themes=["local-ai"])
        self.store.add_book(title="Book C", notes=["mentions blackjack"])
        self.assertEqual(
            [b["id"] for b in self.store.search_books(theme="focus")], ["b-1"]
        )
        self.assertEqual(
            [b["id"] for b in self.store.search_books(query="blackjack")], ["b-3"]
        )
        self.assertEqual(
            len(self.store.search_books(theme="focus", query="nothingmatches")), 0
        )

    def test_get_quotes_filters_by_book_and_theme(self) -> None:
        self.store.add_book(title="A", themes=["focus"], quotes=["q1"])
        self.store.add_book(title="B", themes=["chess"], quotes=["q2"])
        all_q = self.store.get_quotes()
        self.assertEqual(len(all_q), 2)
        self.assertEqual(all_q[0]["book_title"], "A")
        focus = self.store.get_quotes(theme="focus")
        self.assertEqual([q["text"] for q in focus], ["q1"])
        single = self.store.get_quotes(book_id="b-2")
        self.assertEqual([q["text"] for q in single], ["q2"])

    def test_list_books_status_filter(self) -> None:
        self.store.add_book(title="A", status="reading")
        self.store.add_book(title="B")
        self.assertEqual([b["id"] for b in self.store.list_books(status="reading")], ["b-1"])

    def test_backup_rotation(self) -> None:
        self.store.add_book(title="First")
        self.store.add_book(title="Second")
        backup1 = self.tmp / "shelf.json.1"
        backup2 = self.tmp / "shelf.json.2"
        self.assertTrue(backup1.exists())
        self.assertEqual(len(json.loads(backup1.read_text())["books"]), 1)
        self.assertEqual(json.loads(backup1.read_text())["books"][0]["title"], "First")
        self.assertFalse(backup2.exists())
        store2 = BookshelfStore(self.tmp / "shelf.json", max_backups=2)
        store2.add_book(title="Third")
        books1 = json.loads(backup1.read_text())["books"]
        books2 = json.loads(backup2.read_text())["books"]
        self.assertEqual([b["title"] for b in books1], ["First", "Second"])
        self.assertEqual([b["title"] for b in books2], ["First"])
        self.assertEqual(
            [b["title"] for b in json.loads((self.tmp / "shelf.json").read_text())["books"]],
            ["First", "Second", "Third"],
        )

    def test_atomic_save_leaves_no_tmp_files(self) -> None:
        self.store.add_book(title="A")
        leftovers = [p.name for p in self.tmp.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])


class StoreValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = make_store(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_title_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_book(title="   ")

    def test_empty_note_rejected(self) -> None:
        self.store.add_book(title="A")
        with self.assertRaises(ValueError):
            self.store.add_note("b-1", "")

    def test_unknown_book_id_rejected(self) -> None:
        self.store.add_book(title="A")
        with self.assertRaises(Exception):
            self.store.add_note("b-999", "nope")

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_book(title="A", status="currently skimming")

    def test_overlong_text_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_book(title="x" * 301)

    def test_one_line_take_word_limit(self) -> None:
        self.store.add_book(title="A")
        with self.assertRaises(ValueError):
            self.store.mark_finished("b-1", "word " * 61)

    def test_corrupt_data_file_rejected(self) -> None:
        path = self.tmp / "bad.json"
        path.write_text("{not json")
        with self.assertRaises(ValueError):
            BookshelfStore(path)

    def test_foreign_data_version_rejected(self) -> None:
        path = self.tmp / "bad.json"
        path.write_text(json.dumps({"version": 99, "books": []}))
        with self.assertRaises(ValueError):
            BookshelfStore(path)


class PathSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_plain_name_ok(self) -> None:
        p = resolve_store_path("shelf.json", self.base)
        self.assertEqual(p.parent, self.base)

    def test_plain_name_yes_default(self) -> None:
        p = resolve_store_path(None, self.base)
        self.assertEqual(p, self.base / "bookshelf.json")

    def test_subdir_inside_base_ok(self) -> None:
        (self.base / "sub").mkdir()
        p = resolve_store_path("sub/shelf.json", self.base)
        self.assertEqual(p, self.base / "sub" / "shelf.json")

    def test_traversal_rejected(self) -> None:
        with self.assertRaises(PathError):
            resolve_store_path("../shelf.json", self.base)

    def test_nested_traversal_rejected(self) -> None:
        with self.assertRaises(PathError):
            resolve_store_path("sub/../../shelf.json", self.base)

    def test_sneaky_traversal_string_rejected(self) -> None:
        with self.assertRaises(PathError):
            resolve_store_path("x/../../shelf.json", self.base)

    def test_absolute_outside_base_rejected(self) -> None:
        with self.assertRaises(PathError):
            resolve_store_path("/etc/passwd.json", self.base)

    def test_absolute_inside_base_ok(self) -> None:
        p = resolve_store_path(str(self.base / "shelf.json"), self.base)
        self.assertEqual(p.name, "shelf.json")

    def test_non_json_rejected(self) -> None:
        with self.assertRaises(PathError):
            resolve_store_path("shelf.txt", self.base)

    def test_hidden_file_rejected(self) -> None:
        with self.assertRaises(PathError):
            resolve_store_path(".bookshelf.json", self.base)

    def test_symlink_escape_rejected(self) -> None:
        outside = Path(tempfile.mkdtemp())
        (outside / "target").mkdir()
        symlink = self.base / "escape"
        symlink.symlink_to(outside / "target", target_is_directory=True)
        with self.assertRaises(PathError):
            resolve_store_path("escape/shelf.json", self.base)


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = make_store(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_loop(self, lines: list[str]) -> list[dict]:
        reader = io.StringIO("\n".join(lines) + "\n")
        writer = io.StringIO()
        protocol_loop(self.store, reader=reader, writer=writer)
        return [json.loads(out) for out in writer.getvalue().splitlines() if out.strip()]

    def test_initialize_handshake(self) -> None:
        out = self.run_loop(
            [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18"}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})]
        )
        self.assertEqual(out[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "bookshelf-mcp-server")

    def test_tools_list_offers_all_tools(self) -> None:
        out = self.run_loop(
            [json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})]
        )
        names = {t["name"] for t in out[0]["result"]["tools"]}
        expected = {
            "add_book", "get_book", "add_note", "add_quote",
            "search_books", "get_quotes", "mark_finished", "list_books",
        }
        self.assertEqual(names, expected)

    def test_tools_call_round_trip(self) -> None:
        add = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                          "params": {"name": "add_book",
                                     "arguments": {"title": "A", "themes": ["focus"]}}})
        quotes = json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "get_quotes", "arguments": {"theme": "focus"}}})
        out = self.run_loop([add, quotes])
        content = json.loads(out[0]["result"]["content"][0]["text"])
        self.assertEqual(content["id"], "b-1")
        precision_check = json.loads(out[1]["result"]["content"][0]["text"])
        self.assertEqual(precision_check["count"], 0)
        self.assertEqual(json.loads(self.tmp.joinpath("shelf.json").read_text())["books"][0]["title"], "A")

    def test_tools_call_error_is_reported(self) -> None:
        msg = json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                          "params": {"name": "add_book", "arguments": {"title": "  "}}})
        out = self.run_loop([msg])
        self.assertTrue(out[0]["result"]["isError"])
        self.assertIn("title", out[0]["result"]["content"][0]["text"])

    def test_unknown_method_returns_error(self) -> None:
        out = self.run_loop(
            [json.dumps({"jsonrpc": "2.0", "id": 6, "method": "bogus/method"})]
        )
        self.assertEqual(out[0]["error"]["code"], -32601)

    def test_bad_json_returns_parse_error(self) -> None:
        out = self.run_loop(["{nope"])
        self.assertEqual(out[0]["error"]["code"], -32700)


class ToolErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = make_store(self.tmp)
        self.store.add_book(title="A", themes=["focus"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unknown_tool_raises(self) -> None:
        with self.assertRaises(ToolError):
            tool_call(self.store, "yolo", {})

    def test_missing_book_raises_tool_error(self) -> None:
        with self.assertRaises(ToolError):
            tool_call(self.store, "get_book", {"book_id": "b-9"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
