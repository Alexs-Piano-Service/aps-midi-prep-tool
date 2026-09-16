# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
import struct
from pathlib import Path
import tempfile
import unittest
import zipfile

from aps_midi_prep_tool_app.markiv_backup.tags import read_folder_tags, read_music_title


def midi(*tracks, kind=1):
    return b"MThd" + struct.pack(">IHHH", 6, kind, len(tracks), 480) + b"".join(
        b"MTrk" + struct.pack(">I", len(track)) + track for track in tracks
    )


def title(value):
    raw = value.encode()
    length = bytes([len(raw)]) if len(raw) < 128 else bytes([128 | (len(raw) >> 7), len(raw) & 127])
    return b"\x00\xff\x03" + length + raw + b"\x00\xff\x2f\x00"


class TagTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)

    def write(self, name, data):
        path = self.folder / name
        path.write_bytes(data)
        return path

    def test_management_titles_order_and_case_insensitive_pair(self):
        self.write("pdisk.mng", b"PDISK   MNG   \r\nP.PLAYER      \r\nVer1.01DMV0.54\r\nPianoSoft Solo (Demo)   \r\n")
        # Full 8-character DOS stem and a 16-character title on the first line.
        self.write("psong.mng", b"PSONG   MNG   \r\nMAX001        \r\nFILE001       \r\n" + b" " * 78 + b"\r\n02SCABO2MID   \r\nScarborough Fair                P.PLAYER      \r\nVer1.01DMV0.54\r\nA,I,P,M,SMF0,0\r\nL             \r\n              \r\n              \r\n              \r\n")
        self.write("02scabo2.mid", midi(title("Conflicting MIDI title"), kind=0))
        self.write("02SCABO2.WAV", b"audio payload is not parsed")
        album, tracks = read_folder_tags(self.folder)
        self.assertEqual(album["title"], "PianoSoft Solo (Demo)")
        self.assertEqual(tracks["02scabo2.mid"]["title"], "Scarborough Fair")
        self.assertEqual(tracks["02SCABO2.WAV"]["track_number"], 1)
        self.assertEqual(tracks["02SCABO2.WAV"], tracks["02scabo2.mid"])

    def test_midi_first_track_only(self):
        path = self.write("song.mid", midi(b"\x00\xff\x2f\x00", title("Grand Piano")))
        self.assertIsNone(read_music_title(path))
        path.write_bytes(midi(title("Song title"), title("Grand Piano")))
        self.assertEqual(read_music_title(path), "Song title")

    def test_title_removes_observed_demo_annotation_with_nested_quotes(self):
        path = self.write("song.mid", midi(title('"Improvisation on "Air" (Handel)"     from the Yamaha PianoSoft "Rainy Day Romance" / Artist: Dick Hyman / available from your local authorized Yamaha dealer'), kind=0))
        self.assertEqual(read_music_title(path), 'Improvisation on "Air" (Handel)')

    def test_running_status_and_sysex_are_parsed(self):
        prefix = b"\x00\xf0\x03\x43\x01\xf7\x00\x90\x3c\x40\x01\x3d\x40"
        path = self.write("song.mid", midi(prefix + title("After events"), kind=0))
        self.assertEqual(read_music_title(path), "After events")

    def test_invalid_and_non_sequence_names_return_none(self):
        for data in (b"not midi", midi(title("Piano"), kind=0), midi(title("Song"), kind=2), midi(b"\x00\xff\x03\x81"), midi(b"\x80\x80\x80\x80\x80")):
            with self.subTest(data=data):
                self.assertIsNone(read_music_title(self.write("song.mid", data)))

    def test_pspg_reads_single_midi_without_extracting_assets(self):
        path = self.folder / "01_FurElise.pspg"
        with zipfile.ZipFile(path, "w") as package:
            package.writestr("FurElise.MID", midi(title("Fur Elise"), kind=0))
            package.writestr("title.bmp", b"image")
            package.writestr("piece.pgs", b'<smil><body><img src="title.bmp"/></body></smil>')
        _, tracks = read_folder_tags(self.folder)
        self.assertEqual(tracks[path.name]["title"], "Fur Elise")
        self.assertEqual(tracks[path.name]["track_number"], 1)
        self.assertEqual(list(self.folder.iterdir()), [path])

    def test_unknown_eseq_and_symlinks_remain_unparsed(self):
        unknown = self.write("SONG.FIL", b"undocumented E-SEQ bytes")
        self.assertIsNone(read_music_title(unknown))
        linked = self.folder / "linked.mid"
        linked.symlink_to(self.write("original.mid", midi(title("Song"), kind=0)))
        self.assertIsNone(read_music_title(linked))
        self.assertNotIn("linked.mid", read_folder_tags(self.folder)[1])


if __name__ == "__main__":
    unittest.main()
