# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from aps_midi_prep_tool_app.markiv_backup.library import CancelledError, scan_library
from aps_midi_prep_tool_app.markiv_backup.metadata import MetadataCatalog


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name)
        (self.source / "songs").mkdir()
        self.catalog = MetadataCatalog()

    def write(self, name, content=b"original bytes"):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def scan(self):
        with patch("aps_midi_prep_tool_app.markiv_backup.metadata.load_metadata", return_value=self.catalog):
            return scan_library(self.source)

    def test_inventory_keeps_library_assets_formats_and_database_evidence(self):
        included = {
            "songs/user/1/SONG.MID",
            "songs/user/1/SONG.WAV",
            "songs/user/1/VOICE.MP3",
            "songs/user/1/LEGACY.FIL",
            "songs/user/1/nested/remote.xml",
            "songs/user/1/UNKNOWN.BIN",
            "songs/pianosoft/2/PIECE.pspg",
            "imports/old-song.eseq",
            "local/pgsql/data/base/123/456",
            "songs/tool/user_song.sql",
        }
        for name in included | {"local/unrelated.log"}:
            self.write(name)
        self.catalog.evidence_files = ["local/pgsql/data/base/123/456"]
        self.catalog.albums = {"songs/user/1": {"title": "My recordings"}}
        self.catalog.tracks = {"songs/user/1/SONG.MID": {"title": "First take", "track_number": 3}}
        plan = self.scan()
        by_source = {file.source: file for file in plan.files}
        self.assertEqual(set(by_source), included)
        self.assertEqual(by_source["songs/user/1/SONG.MID"].destination, "User/My recordings/03 - First take.MID")
        self.assertEqual(by_source["songs/user/1/SONG.WAV"].destination, "User/My recordings/03 - First take.WAV")
        self.assertEqual(by_source["songs/user/1/LEGACY.FIL"].kind, "E-SEQ")
        self.assertEqual(by_source["local/pgsql/data/base/123/456"].kind, "Metadata")
        self.assertEqual(by_source["songs/tool/user_song.sql"].kind, "Metadata")
        self.assertEqual(plan.total_bytes, sum(file.size for file in plan.files))

    def test_database_midi_title_wins_over_audio_management_fallback(self):
        for name in ("SONG.MID", "SONG.WAV"):
            self.write("songs/user/1/" + name)
        self.catalog.tracks = {"songs/user/1/SONG.MID": {
            "title": "Edited on piano", "track_number": 7, "metadata_source": "postgresql-7.3"
        }}
        side_tracks = {name: {"title": "Old display name", "track_number": 1, "metadata_source": "PSONG.MNG"}
                       for name in ("SONG.MID", "SONG.WAV")}
        with patch("aps_midi_prep_tool_app.markiv_backup.tags.read_folder_tags", return_value=({}, side_tracks)):
            plan = self.scan()
        self.assertEqual({Path(file.destination).stem for file in plan.files}, {"07 - Edited on piano"})

    def test_collision_keeps_companion_basename_with_unpaired_audio(self):
        for name in ("A.WAV", "B.MID", "B.WAV"):
            relative = "songs/user/1/" + name
            self.write(relative)
            self.catalog.tracks[relative] = {"title": "Same title"}
        plan = self.scan()
        destinations = {file.source: Path(file.destination) for file in plan.files}
        self.assertEqual(destinations["songs/user/1/B.MID"].stem, destinations["songs/user/1/B.WAV"].stem)
        self.assertNotEqual(destinations["songs/user/1/A.WAV"], destinations["songs/user/1/B.WAV"])
        self.assertEqual(len({file.destination.casefold() for file in plan.files}), len(plan.files))

    def test_root_management_filename_does_not_label_nested_file(self):
        self.write("songs/user/1/SAME.MID")
        self.write("songs/user/1/child/SAME.MID")
        side_tracks = {"SAME.MID": {"title": "Only root song", "track_number": 1}}
        with patch("aps_midi_prep_tool_app.markiv_backup.tags.read_folder_tags", side_effect=lambda folder: ({}, side_tracks if folder.name == "1" else {})):
            plan = self.scan()
        nested = next(file for file in plan.files if "/child/" in file.source)
        self.assertNotIn("Only root song", nested.destination)

    def test_presentation_cache_uses_song_id_title_and_preserves_asset_paths(self):
        self.write("songs/pianosoft/9/01_FurElise.pspg")
        self.write("songs/pspg/archive/cache/pianosoft116/PSPG.PGS")
        self.write("songs/pspg/archive/cache/pianosoft116/FURELISE.MID")
        self.write("songs/pspg/archive/cache/pianosoft116/bmp/title.bmp")
        self.catalog.albums = {"songs/pianosoft/9": {"title": "PianoSoft Plus Graphics (Demo)"}}
        self.catalog.tracks = {"songs/pianosoft/9/01_FurElise.pspg": {
            "title": "Fur Elise", "song_id": 116, "album_id": 9, "track_number": 3
        }}
        plan = self.scan()
        for file in plan.files:
            if "/cache/" in file.source:
                self.assertIn("Fur Elise", file.destination)
                original_nested = file.source.split("pianosoft116/", 1)[1]
                self.assertTrue(file.destination.endswith("/" + original_nested))

    def test_case_insensitive_album_collisions_and_unsafe_titles_are_distinct(self):
        for index, title in ((1, "A/B"), (2, "a\\b")):
            source = f"songs/user/{index}/SONG.MID"
            self.write(source)
            self.catalog.albums[f"songs/user/{index}"] = {"title": title}
            self.catalog.tracks[source] = {"title": "../CON: A?B", "track_number": 1}
        plan = self.scan()
        self.assertEqual(len({file.destination.casefold() for file in plan.files}), 2)
        self.assertEqual(len({Path(file.destination).parent.as_posix().casefold() for file in plan.files}), 2)
        for file in plan.files:
            self.assertNotIn("..", Path(file.destination).parts)
            self.assertNotIn(":", file.destination)
            self.assertNotIn("?", file.destination)
            self.assertFalse(Path(file.destination).is_absolute())

    def test_symlinks_are_skipped_and_cancellation_stops_scan(self):
        self.write("songs/user/1/SONG.MID")
        (self.source / "songs/user/1/linked.MID").symlink_to(self.source / "songs/user/1/SONG.MID")
        (self.source / "songs/linked").symlink_to(self.source / "songs/user", target_is_directory=True)
        plan = self.scan()
        self.assertEqual([file.source for file in plan.files], ["songs/user/1/SONG.MID"])
        self.assertEqual(len(plan.warnings), 2)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            scan_library(self.source, cancel=cancel)


if __name__ == "__main__":
    unittest.main()
