# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
"""Synthetic fixtures for historical database parsing; no private music data."""

import struct
import tempfile
import unittest
from pathlib import Path

from aps_midi_prep_tool_app.markiv_backup.metadata import (
    MetadataCatalog, MetadataError, _Attribute, _PG73, _import_tables,
    decode_text, load_metadata,
)


def tuple_bytes(data, *, natts=1, xmin=2, xmax=0, mask=0x0900, nullmap=b"", oid=None):
    if nullmap:
        mask |= 1
    if oid is not None:
        mask |= 0x10
    header = (23 + len(nullmap) + (4 if oid is not None else 0) + 3) & ~3
    row = bytearray(header)
    struct.pack_into("<III", row, 0, xmin, xmax, 0)
    struct.pack_into("<HHB", row, 18, natts, mask, header)
    row[23:23 + len(nullmap)] = nullmap
    if oid is not None:
        struct.pack_into("<I", row, header - 4, oid)
    return bytes(row) + data


def heap_page(*rows):
    page = bytearray(8192)
    lower = 20 + 4 * len(rows)
    upper = 8192
    for index, row in enumerate(rows):
        upper = (upper - len(row)) & ~3
        page[upper:upper + len(row)] = row
        struct.pack_into("<I", page, 20 + 4 * index, upper | (1 << 15) | (len(row) << 17))
    struct.pack_into("<HHHH", page, 12, lower, upper, 8192, 0x2001)
    return bytes(page)


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_committed_update_excludes_old_row_without_hint_bits(self):
        cluster = self.root
        (cluster / "pg_clog").mkdir()
        statuses = bytearray(16)
        for xid, status in ((10, 1), (11, 1), (12, 2), (13, 0)):
            statuses[xid // 4] |= status << ((xid % 4) * 2)
        (cluster / "pg_clog/0000").write_bytes(statuses)
        rows = [
            tuple_bytes(b"old!", xmin=10, xmax=11, mask=0),
            tuple_bytes(b"new!", xmin=11, mask=0x0800),
            tuple_bytes(b"bad!", xmin=12, mask=0x0800),
            tuple_bytes(b"open", xmin=13, mask=0x0800),
        ]
        relation = cluster / "123"
        relation.write_bytes(heap_page(*rows))
        self.assertEqual([row[row[22]:] for row in _PG73(cluster).tuples(relation)], [b"new!"])

    def test_null_and_dropped_columns_preserve_offsets(self):
        attributes = [
            _Attribute("song_id", 20, 8, 1, "d"),
            _Attribute("comment", 25, -1, 2, "i"),
            _Attribute("old_field", 21, 2, 3, "s", dropped=True),
            _Attribute("title", 25, -1, 4, "i"),
        ]
        title = "Étude".encode()
        data = struct.pack("<qh2xI", 42, 123, len(title) + 4) + title
        row = tuple_bytes(data, natts=4, nullmap=b"\x0d")
        self.assertEqual(_PG73(self.root).values(row, attributes), {"song_id": 42, "comment": None, "title": "Étude"})

    def test_four_byte_alignment_for_int64(self):
        attributes = [_Attribute("order", 21, 2, 1, "s"), _Attribute("id", 20, 8, 2, "d")]
        row = tuple_bytes(struct.pack("<h2xq", 3, 123456789), natts=2)
        self.assertEqual(_PG73(self.root).values(row, attributes), {"order": 3, "id": 123456789})

    def test_corrupt_pointer_and_wrong_version_fail(self):
        relation = self.root / "123"
        page = bytearray(heap_page(tuple_bytes(b"name")))
        struct.pack_into("<I", page, 20, 8190 | (1 << 15) | (40 << 17))
        relation.write_bytes(page)
        with self.assertRaisesRegex(MetadataError, "pointer"):
            list(_PG73(self.root).tuples(relation))
        page = bytearray(heap_page(tuple_bytes(b"name")))
        struct.pack_into("<H", page, 18, 0x2004)
        relation.write_bytes(page)
        with self.assertRaisesRegex(MetadataError, "page"):
            list(_PG73(self.root).tuples(relation))

    def test_missing_commit_log_does_not_guess(self):
        relation = self.root / "123"
        relation.write_bytes(heap_page(tuple_bytes(b"name", xmin=1234, mask=0x0800)))
        with self.assertRaisesRegex(MetadataError, "transaction status"):
            list(_PG73(self.root).tuples(relation))

    def test_truncated_row_fails(self):
        row = tuple_bytes(struct.pack("<I", 100) + b"short", mask=0x0902)
        with self.assertRaisesRegex(MetadataError, "variable-length"):
            _PG73(self.root).values(row, [_Attribute("title", 25, -1, 1, "i")])

    def test_album_paths_order_and_unsafe_paths(self):
        catalog = MetadataCatalog()
        tables = {
            "user_album": [{"album_id": 1, "path": "/home/songs/user/1/", "title": "My performances"}],
            "user_song": [
                {"album_id": 1, "filename": "PIANO001.MID", "display_order": 4, "title": "Evening", "add_file": "VOICE.WAV"},
                {"album_id": 1, "filename": "../../../outside.MID", "title": "unsafe"},
            ],
            "usb_album": [{"album_id": 2, "path": "/mnt/usb/elsewhere", "title": "External"}],
        }
        _import_tables(catalog, tables)
        self.assertEqual(list(catalog.albums), ["songs/user/1"])
        track = catalog.tracks["songs/user/1/PIANO001.MID"]
        self.assertEqual((track["title"], track["track_number"], track["album"]), ("Evening", 4, "My performances"))
        self.assertEqual(catalog.tracks["songs/user/1/VOICE.WAV"], track)
        self.assertEqual(len(catalog.warnings), 1)

    def test_unsupported_version_keeps_database_evidence(self):
        cluster = self.root / "local/pgsql/data"
        cluster.mkdir(parents=True)
        (cluster / "PG_VERSION").write_text("16")
        (cluster / "original.db").write_bytes(b"unmodified")
        catalog = load_metadata(self.root)
        self.assertIn("local/pgsql/data/original.db", catalog.evidence_files)
        self.assertIn("Only the inspected", catalog.warnings[0])
        self.assertEqual(catalog.tracks, {})

    def test_loads_catalog_discovered_relation_and_actual_schema(self):
        cluster = self.root / "local/pgsql/data"
        database = cluster / "base/99999"
        database.mkdir(parents=True)
        (cluster / "PG_VERSION").write_text("7.3\n")
        schemas = {
            "user_album": (700, 900, [
                _Attribute("album_id", 20, 8, 1, "d"),
                _Attribute("path", 25, -1, 2, "i"),
                _Attribute("title", 25, -1, 3, "i"),
            ]),
            "user_song": (701, 901, [
                _Attribute("album_id", 20, 8, 1, "d"),
                _Attribute("filename", 25, -1, 2, "i"),
                _Attribute("title", 25, -1, 3, "i"),
                _Attribute("display_order", 21, 2, 4, "s"),
            ]),
        }
        classes, columns = [], []
        for name, (oid, node, attrs) in schemas.items():
            value = bytearray(120)
            value[:len(name)] = name.encode()
            struct.pack_into("<I", value, 80, node)
            value[102] = ord("r")
            classes.append(tuple_bytes(value, natts=24, oid=oid))
            for attr in attrs:
                value = bytearray(104)
                struct.pack_into("<I", value, 0, oid)
                value[4:4 + len(attr.name)] = attr.name.encode()
                struct.pack_into("<I", value, 68, attr.type_oid)
                struct.pack_into("<hh", value, 76, attr.length, attr.number)
                value[95] = ord(attr.alignment)
                columns.append(tuple_bytes(value, natts=18))
        (database / "1259").write_bytes(heap_page(*classes))
        (database / "1249").write_bytes(heap_page(*columns))

        def record(attrs, values):
            data = bytearray()
            for attr, value in zip(attrs, values):
                alignment = {"d": 4, "i": 4, "s": 2}[attr.alignment]
                data += b"\0" * ((-len(data)) % alignment)
                if attr.length == -1:
                    raw = value.encode()
                    data += struct.pack("<I", len(raw) + 4) + raw
                else:
                    data += value.to_bytes(attr.length, "little", signed=True)
            return tuple_bytes(data, natts=len(attrs))

        (database / "900").write_bytes(heap_page(record(schemas["user_album"][2], [15, "/home/songs/user/15/", "Recital"])))
        (database / "901").write_bytes(heap_page(record(schemas["user_song"][2], [15, "PIANO001.MID", "Sonata", 7])))
        catalog = load_metadata(self.root)
        self.assertEqual(catalog.warnings, [])
        self.assertEqual(catalog.albums["songs/user/15"]["title"], "Recital")
        self.assertEqual(catalog.tracks["songs/user/15/PIANO001.MID"]["title"], "Sonata")
        self.assertEqual(catalog.tracks["songs/user/15/PIANO001.MID"]["track_number"], 7)

    def test_symlinked_cluster_and_storage_are_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        local = self.root / "local"
        local.symlink_to(outside, target_is_directory=True)
        self.assertEqual(load_metadata(self.root).evidence_files, [])
        (self.root / "pg_clog").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(MetadataError, "symlink"):
            _PG73(self.root)

    def test_text_encodings(self):
        self.assertEqual(decode_text("月光".encode("cp932")), "月光")
        self.assertEqual(decode_text("Étude".encode("utf8")), "Étude")


if __name__ == "__main__":
    unittest.main()
