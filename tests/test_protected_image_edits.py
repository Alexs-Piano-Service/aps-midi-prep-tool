"""Protected FAT entries must never open an invisible mdel confirmation prompt."""

import io
from pathlib import Path

import mido
import pytest

from aps_midi_prep_tool_app import floppy_image as image


def _song(title):
    midi = mido.MidiFile(type=0)
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage("track_name", name=title)]))
    output = io.BytesIO()
    midi.save(file=output)
    return output.getvalue()


def _set_attributes(path, name, attributes):
    _, geometry, _, directory = image._read_fat12_image_context(path)
    entry = next(entry for entry in image._iter_fat_directory_entries(directory)
                 if entry["name"].upper() == name.upper())
    with open(path, "r+b") as handle:
        handle.seek(geometry.root_offset + entry["offset"] + 11)
        handle.write(bytes([attributes]))


@pytest.mark.parametrize("attributes", [0x21, 0x24, 0x27])
@pytest.mark.parametrize("operation", ["delete", "replace", "title"])
def test_protected_song_changes_only_the_private_working_image(tmp_path, attributes, operation):
    original = tmp_path / "original.img"
    first, unrelated, replacement = (tmp_path / name for name in ("first.mid", "other.mid", "new.mid"))
    first.write_bytes(_song("Original"))
    unrelated.write_bytes(_song("Unrelated"))
    replacement.write_bytes(_song("Replacement"))
    image.create_floppy_images_from_files(
        [{"host_path": str(first), "image_path": "1MOMENT.FIL"},
         {"host_path": str(unrelated), "image_path": "OTHER.MID"}],
        str(original), "img", image.DISK_FORMAT_BY_KEY["ibm.720"],
    )
    for name in ("1MOMENT.FIL", "OTHER.MID"):
        _set_attributes(original, name, attributes)
    original_bytes = original.read_bytes()
    session = image.FloppyImageSession.load(original)
    try:
        baseline = Path(session.working_img_path).read_bytes()
        session._run_mtools = lambda args, message, cancel_callback=None: image._run_command(
            args, message, cancel_callback=cancel_callback, timeout=5,
        )
        kwargs = {
            "delete": {"deletes": {"1MOMENT.FIL"}},
            "replace": {"replacements": {"1MOMENT.FIL": str(replacement)}},
            "title": {"title_edits": {"1MOMENT.FIL": "Edited"}},
        }[operation]
        output = session.create_modified_image(**kwargs)
        entries = {entry.path: entry for entry in image._read_fat12_image_listing(output).entries}
        assert entries["OTHER.MID"].attributes == f"{attributes:02X}"
        assert image._read_fat12_file_bytes(output, "OTHER.MID") == unrelated.read_bytes()
        if operation == "delete":
            assert "1MOMENT.FIL" not in entries
        else:
            payload = image._read_fat12_file_bytes(output, "1MOMENT.FIL")
            expected = replacement.read_bytes() if operation == "replace" else _song("Edited")
            assert payload == expected
        assert Path(session.working_img_path).read_bytes() == baseline
        assert original.read_bytes() == original_bytes
    finally:
        session.cleanup()
