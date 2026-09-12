"""Capacity previews account for the same songs as the prepared image."""

import os
from types import MethodType, SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA, ESEQ_CONTAINER_DISKLAVIER,
    convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.floppy_image import (
    FloppyImageError, FloppyImageSession, allocated_size, read_image_listing,
)
from test_eseq_image_contents import _image, _midi


def _capacity_window(session, variant="disklavier", generate=True):
    window = SimpleNamespace(
        image_session=session, imageEseqMode=generate, imageEseqVariant=variant,
        imageHasPianodir=True, pendingImageDeletes=set(), pendingDeletePianodir=False,
        pendingImageReplacements={}, pendingImageAdditions={},
        _should_generate_pianodir=lambda **_kwargs: generate,
        _generated_eseq_directory_size=lambda count=None: 160 + (count or 0) * 48 if variant == "clavinova" else 6144,
        _image_entry_for_path=lambda _path: None,
    )
    for name in ("_pending_image_space_remaining", "_pending_image_used_bytes", "_pending_eseq_image_used_bytes"):
        setattr(window, name, MethodType(getattr(MidiTitleWindow, name), window))
    return window


@pytest.mark.parametrize("variant", ("disklavier", "clavinova"))
def test_capacity_matches_pruned_image_for_pending_conversions_and_additions(tmp_path, variant):
    container = ESEQ_CONTAINER_CLAVINOVA_MDA if variant == "clavinova" else ESEQ_CONTAINER_DISKLAVIER
    song = convert_midi_bytes_to_eseq_bytes(_midi(), container_variant=container)
    image_path, _sources = _image(tmp_path, {
        "EXTLESS": song, "CONVERT.MID": _midi(),
        "PDISK.MNG": b"x" * (550 * 1024), "BOGUS.FIL": b"x" * (80 * 1024),
        "PIANODIR.FIL": b"Old directory" * 1000,
    })
    prepared = tmp_path / "converted-song"
    prepared.write_bytes(song + bytes(100 * 1024))
    sidecar = tmp_path / "management-data"
    sidecar.write_bytes(bytes(1024 * 1024))
    session = FloppyImageSession.load(str(image_path))
    try:
        window = _capacity_window(session, variant)
        window.pendingImageReplacements["CONVERT.MID"] = str(prepared)
        window.pendingImageAdditions.update({"NEWSONG": str(prepared), "PSONG.MNG": str(sidecar)})
        original_listing = session.list_entries()
        assert original_listing.free_space < prepared.stat().st_size * 2

        output = session.create_modified_image(
            replacements=window.pendingImageReplacements, additions=window.pendingImageAdditions,
            generate_pianodir=True, eseq_variant=variant, clean_eseq_delivery=True,
        )
        prepared_listing = read_image_listing(output)
        assert window._pending_image_space_remaining(for_export=True) == prepared_listing.free_space
        assert window._pending_image_space_remaining(for_export=True) > 0
        assert window._pending_image_used_bytes(for_export=True) == sum(entry.packed_size for entry in prepared_listing.entries)
        assert window._pending_image_space_remaining() < 0  # Ordinary Save retains the large unrelated files.

        extra = {"EXTRA": str(prepared), "EXTRA.FIL": str(sidecar)}
        output = session.create_modified_image(
            replacements=window.pendingImageReplacements,
            additions={**window.pendingImageAdditions, **extra},
            generate_pianodir=True, eseq_variant=variant, clean_eseq_delivery=True,
        )
        assert window._pending_image_space_remaining(extra, for_export=True) == read_image_listing(output).free_space
    finally:
        session.cleanup()


def test_missing_replacement_retains_conservative_capacity_and_save_error(tmp_path):
    song = convert_midi_bytes_to_eseq_bytes(_midi())
    image_path, _sources = _image(tmp_path, {"SONG.FIL": song})
    session = FloppyImageSession.load(str(image_path))
    try:
        window = _capacity_window(session)
        window.pendingImageReplacements["SONG.FIL"] = str(tmp_path / "missing-output")
        listing = session.list_entries()
        expected = sum(entry.packed_size for entry in listing.entries) + allocated_size(6144, listing.cluster_size)
        assert window._pending_image_used_bytes() == expected
        with pytest.raises(FloppyImageError, match="Replacement file no longer exists"):
            session.create_modified_image(replacements=window.pendingImageReplacements, generate_pianodir=True)
    finally:
        session.cleanup()


def test_unreadable_image_payload_is_not_counted_as_freed_space(tmp_path, monkeypatch):
    song = convert_midi_bytes_to_eseq_bytes(_midi())
    image_path, _sources = _image(tmp_path, {"SONG.FIL": song, "UNREAD.FIL": bytes(8192)})
    session = FloppyImageSession.load(str(image_path))
    try:
        window = _capacity_window(session)
        original_extract = session.extract_file

        def extract(path):
            if path == "UNREAD.FIL":
                raise FloppyImageError("Unreadable sectors")
            return original_extract(path)

        monkeypatch.setattr(session, "extract_file", extract)
        listing = session.list_entries()
        expected = sum(entry.packed_size for entry in listing.entries) + allocated_size(6144, listing.cluster_size)
        assert window._pending_image_used_bytes() == expected
        assert window._pending_image_space_remaining() == listing.free_space - allocated_size(6144, listing.cluster_size)
    finally:
        session.cleanup()


def test_ordinary_mixed_capacity_still_counts_management_files(tmp_path):
    image_path, _sources = _image(tmp_path, {"SONG.MID": _midi(), "PDISK.MNG": bytes(8192)})
    session = FloppyImageSession.load(str(image_path))
    try:
        window = _capacity_window(session, generate=False)
        listing = session.list_entries()
        assert window._pending_image_used_bytes() == sum(entry.packed_size for entry in listing.entries)
        assert window._pending_image_space_remaining() == listing.free_space
    finally:
        session.cleanup()


@pytest.mark.parametrize("variant", ("disklavier", "clavinova"))
def test_in_place_capacity_matches_image_with_retained_unrelated_payloads(tmp_path, variant):
    container = ESEQ_CONTAINER_CLAVINOVA_MDA if variant == "clavinova" else ESEQ_CONTAINER_DISKLAVIER
    song = convert_midi_bytes_to_eseq_bytes(_midi(), container_variant=container)
    image_path, _sources = _image(tmp_path, {
        "SONG.FIL": song, "NOTES.TXT": bytes(16 * 1024), "PSONG.MNG": bytes(4096),
        "PIANODIR.FIL": b"Disklavier catalog", "MUSIC.DIR": b"Clavinova catalog",
    })
    session = FloppyImageSession.load(str(image_path))
    try:
        window = _capacity_window(session, variant)
        output = session.create_modified_image(generate_pianodir=True, eseq_variant=variant)
        listing = read_image_listing(output)
        assert window._pending_image_space_remaining() == listing.free_space
        assert window._pending_image_used_bytes() == sum(entry.packed_size for entry in listing.entries)
    finally:
        session.cleanup()
