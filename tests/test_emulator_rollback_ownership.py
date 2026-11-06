"""Failed emulator exports preserve files changed by another application."""

import json
import os
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import emulator_image_builder as builder
from aps_midi_prep_tool_app.floppy_image import FloppyImageError
from aps_midi_prep_tool_app.helpers import file_batch
from test_emulator_image_builder import _midi_bytes


@pytest.mark.parametrize("failure_phase", ["publication", "verification"])
@pytest.mark.parametrize("output_ext", ["img", "hfe"])
@pytest.mark.parametrize("existed", [True, False], ids=["modified-existing", "replaced-new"])
def test_failed_export_preserves_external_changes_and_recovery(
    tmp_path, monkeypatch, existed, output_ext, failure_phase,
):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    output.mkdir()
    destinations = [output / f"ROLL{number:04d}.{output_ext}" for number in (1, 2)]
    originals = {}
    for number, destination in enumerate(destinations, start=1):
        album = source / f"Album {number}"
        album.mkdir()
        (album / "Song.mid").write_bytes(_midi_bytes(f"Song {number}"))
        if existed:
            originals[destination] = f"original image {number}".encode()
            destination.write_bytes(originals[destination])
    first, second = destinations
    recovery_root = tmp_path / "recovery"
    recovery_root.mkdir()
    real_mkdtemp = file_batch.tempfile.mkdtemp

    def make_recovery_directory(suffix=None, prefix=None, dir=None):
        return real_mkdtemp(suffix=suffix, prefix=prefix, dir=dir or recovery_root)

    monkeypatch.setattr(file_batch.tempfile, "mkdtemp", make_recovery_directory)
    real_write = file_batch.atomic_write_bytes
    real_verify = builder.verify_image_payloads
    published = {}
    external_contents = b"another application's changed disk image"
    failure_message = f"Second image {failure_phase} failed"

    def fail_second_publication(destination, payload, **kwargs):
        if failure_phase == "publication" and Path(destination) == second:
            raise OSError(failure_message)
        return real_write(destination, payload, **kwargs)

    def verify_and_change_first(delivered, prepared, disk_format, **kwargs):
        delivered = Path(delivered)
        if failure_phase == "verification" and delivered == second:
            raise FloppyImageError(failure_message)
        result = real_verify(delivered, prepared, disk_format, **kwargs)
        if delivered == first:
            published[first] = first.read_bytes()
            if existed:
                first.write_bytes(external_contents)
            else:
                replacement = tmp_path / "external-replacement"
                replacement.write_bytes(external_contents)
                os.replace(replacement, first)
        return result

    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_second_publication)
    monkeypatch.setattr(builder, "verify_image_payloads", verify_and_change_first)
    with pytest.raises((FloppyImageError, OSError)) as caught:
        builder.build_emulator_disk_images(
            source, output, prefix="ROLL", starting_number=1,
            output_ext=output_ext, output_content="midi", disk_layout="folders",
            overwrite_existing=existed,
        )

    assert published[first] != external_contents
    assert first.read_bytes() == external_contents
    if existed:
        assert second.read_bytes() == originals[second]
    else:
        assert not second.exists()

    error = caught.value
    assert isinstance(error, FloppyImageError)
    assert failure_message in str(error)
    assert "could not be restored" in str(error)
    assert first.name in str(error)
    assert len(error.rollback_errors) == 1
    assert str(first) in error.rollback_errors[0]
    recovery = Path(error.recovery_directory)
    assert str(recovery) in str(error)
    assert list(recovery_root.iterdir()) == [recovery]
    manifest = json.loads((recovery / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rollback_errors"] == error.rollback_errors
    records = {Path(record["destination"]): record for record in manifest["files"]}
    assert records[first]["published"] is True
    assert records[first]["restored"] is False
    assert (recovery / records[first]["prepared_file"]).read_bytes() == published[first]
    assert records[second]["restored"] is (failure_phase == "verification")
    for destination, record in records.items():
        assert (recovery / record["prepared_file"]).is_file()
        if destination in originals:
            assert (recovery / record["original_file"]).read_bytes() == originals[destination]
        else:
            assert record["original_file"] is None
            if destination not in destinations:
                assert not destination.exists()
