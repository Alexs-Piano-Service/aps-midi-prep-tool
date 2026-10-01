"""Source ingestion never mutates originals or publishes incomplete audio."""

from pathlib import Path
from types import SimpleNamespace
import errno
import threading
import wave

import pytest

from aps_midi_prep_tool_app import smart_pianosoft_media as media
from aps_midi_prep_tool_app.floppy_image import FloppyOperationCancelled, ImageEntry, ImageListing


TOC = """Table of contents (audio tracks only):
track        length               begin        copy pre ch
===========================================================
  1.       75 [00:01.00]        0 [00:00.00]    no   no  2
  3.      150 [00:02.00]      300 [00:04.00]   yes  yes  2
TOTAL 225 [00:03.00] (audio only)
"""


def _wav(path, sectors=1, *, channels=2, width=2, rate=44100):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"\0" * sectors * 588 * channels * width)


@pytest.fixture
def cd(monkeypatch):
    track = media.CDTrack(1, 0, 1)
    monkeypatch.setattr(media, "_linux_required", lambda: None)
    monkeypatch.setattr(media, "_device_identity", lambda _device: ("drive", 1))
    monkeypatch.setattr(media, "_cdparanoia", lambda: "cdparanoia")
    monkeypatch.setattr(media, "read_cd_toc", lambda _device, cancel=None: (track,))
    return track


def test_toc_keeps_actual_track_numbers_and_preemphasis_without_inventing_data_tracks():
    assert media._parse_cd_toc(TOC) == (
        media.CDTrack(1, 0, 75, False), media.CDTrack(3, 300, 150, True),
    )


@pytest.mark.parametrize("toc", [
    "", TOC.replace("audio tracks only", "all tracks"),
    TOC.replace("yes  yes  2", "yes  maybe  2"),
    TOC.replace("yes  yes  2", "yes  yes  4"),
    TOC.replace("300 [00:04.00]", "50 [00:04.00]"),
    TOC.replace("3.", "1."),
])
def test_ambiguous_or_unsupported_toc_is_rejected(toc):
    with pytest.raises(media.SmartPianoSoftMediaError):
        media._parse_cd_toc(toc)


def test_cd_toc_query_is_bounded_and_checks_device_identity(monkeypatch):
    monkeypatch.setattr(media, "_linux_required", lambda: None)
    monkeypatch.setattr(media, "_cdparanoia", lambda: "reader")
    identities = iter([("drive", 1), ("drive", 2)])
    monkeypatch.setattr(media, "_device_identity", lambda _device: next(identities))

    def query(args, **options):
        assert args == ["reader", "-Q", "-d", "/dev/sr0"]
        assert options["timeout"] == 30
        return TOC

    monkeypatch.setattr(media, "_run_command", query)
    with pytest.raises(media.SmartPianoSoftMediaError, match="drive changed"):
        media.read_cd_toc("/dev/sr0")


def test_secure_rip_publishes_validated_audio(cd, tmp_path, monkeypatch):
    destination = tmp_path / "track.wav"
    calls = []

    def rip(args, **options):
        calls.append(args)
        assert not destination.exists()
        assert options["timeout"] >= 180
        _wav(args[-1])
        return ""

    monkeypatch.setattr(media, "_run_command", rip)
    assert media.rip_cd_track("/dev/sr0", cd, destination) == destination
    assert calls[0][:7] == ["cdparanoia", "-w", "-X", "-z", "-d", "/dev/sr0", "1"]
    media._validate_cd_wav(destination, cd)
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("failure", ["truncated", "wrong_rate", "changed_cd", "reader_error", "cancelled"])
def test_failed_rip_leaves_no_published_file(cd, tmp_path, monkeypatch, failure):
    destination = tmp_path / "track.wav"

    def rip(args, **_options):
        _wav(args[-1], rate=48000 if failure == "wrong_rate" else 44100)
        if failure == "truncated":
            path = Path(args[-1])
            path.write_bytes(path.read_bytes()[:-4])
        if failure == "changed_cd":
            monkeypatch.setattr(media, "read_cd_toc", lambda *_a, **_k: (media.CDTrack(2, 75, 1),))
        if failure == "reader_error":
            raise media.SmartPianoSoftMediaError("Read error")
        if failure == "cancelled":
            raise FloppyOperationCancelled("Cancelled")
        return ""

    monkeypatch.setattr(media, "_run_command", rip)
    with pytest.raises((media.SmartPianoSoftMediaError, FloppyOperationCancelled)):
        media.rip_cd_track("/dev/sr0", cd, destination)
    assert not list(tmp_path.iterdir())


def test_preemphasis_and_existing_destination_fail_before_ripping(cd, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_run_command", lambda *_a, **_k: pytest.fail("Unexpected read"))
    with pytest.raises(media.SmartPianoSoftMediaError, match="pre-emphasis"):
        media.rip_cd_track("/dev/sr0", media.CDTrack(1, 0, 1, True), tmp_path / "new.wav")
    existing = tmp_path / "existing.wav"
    existing.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        media.rip_cd_track("/dev/sr0", cd, existing)
    assert existing.read_bytes() == b"original"


def test_destination_created_during_rip_is_preserved(cd, tmp_path, monkeypatch):
    destination = tmp_path / "track.wav"

    def rip(args, **_options):
        _wav(args[-1])
        destination.write_bytes(b"another operation")

    monkeypatch.setattr(media, "_run_command", rip)
    with pytest.raises(FileExistsError):
        media.rip_cd_track("/dev/sr0", cd, destination)
    assert destination.read_bytes() == b"another operation"
    assert list(tmp_path.iterdir()) == [destination]


def test_rip_publishes_on_filesystem_without_hardlinks(cd, tmp_path, monkeypatch):
    def no_links(*_args):
        raise OSError(errno.EOPNOTSUPP, "Hard links unavailable")

    monkeypatch.setattr(media.os, "link", no_links)
    monkeypatch.setattr(media, "_run_command", lambda args, **_kwargs: _wav(args[-1]))
    destination = tmp_path / "track.wav"
    assert media.rip_cd_track("/dev/sr0", cd, destination) == destination
    media._validate_cd_wav(destination, cd)
    assert list(tmp_path.iterdir()) == [destination]


def test_media_fallback_cancel_uses_worker_cancellation_error(tmp_path, monkeypatch):
    def no_links(*_args):
        raise OSError(errno.EOPNOTSUPP, "Hard links unavailable")

    monkeypatch.setattr(media.os, "link", no_links)
    staged, destination = tmp_path / "stage.wav", tmp_path / "track.wav"
    staged.write_bytes(b"x" * (2 * 1024 * 1024))
    with pytest.raises(FloppyOperationCancelled):
        media._publish_new_file(staged, destination,
                                cancel=lambda: destination.exists() and destination.stat().st_size > 0)
    assert not destination.exists()
    assert staged.exists()


def test_folder_snapshot_preserves_long_names_and_exact_catalog_bytes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    expected = {"PSONG.MNG": b"opaque\r\ncatalog\xff", "pdisk.mng": b"album",
                "01 - Don't Know Why.mid": b"MThd\0untouched", "02 Other.MIDI": b"second"}
    for name, content in expected.items():
        (source / name).write_bytes(content)
    (source / "cover.jpg").write_bytes(b"not a song")
    original = {path.name: path.read_bytes() for path in source.iterdir()}
    destination = media.read_floppy_source(source, tmp_path / "snapshot")
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == expected
    assert {path.name: path.read_bytes() for path in source.iterdir()} == original


def test_cancelled_snapshot_does_not_publish_or_touch_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.mid").write_bytes(b"original")
    cancelled = False

    def progress(*_args):
        nonlocal cancelled
        cancelled = True

    with pytest.raises(FloppyOperationCancelled):
        media.read_floppy_source(source, tmp_path / "snapshot", cancel=lambda: cancelled, progress=progress)
    assert list(tmp_path.iterdir()) == [source]
    assert (source / "a.mid").read_bytes() == b"original"


def test_snapshot_accepts_worker_cancellation_events(tmp_path, monkeypatch):
    source = tmp_path / "disk.img"
    source.write_bytes(b"image")
    cancelled = threading.Event()

    def loader(_path, *, cancel_callback, progress_callback):
        assert callable(cancel_callback)
        assert not cancel_callback()
        cancelled.set()
        assert cancel_callback()
        raise FloppyOperationCancelled("Cancelled")

    monkeypatch.setattr(media.FloppyImageSession, "load", loader)
    with pytest.raises(FloppyOperationCancelled):
        media.read_floppy_source(source, tmp_path / "snapshot", cancel=cancelled)
    assert not (tmp_path / "snapshot").exists()


def test_cd_apis_accept_pre_cancelled_worker_events(cd, tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(FloppyOperationCancelled):
        media.rip_cd_track("/dev/sr0", cd, tmp_path / "track.wav", cancel=cancelled)


def test_snapshot_rejects_source_changes_between_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.mid").write_bytes(b"original")
    (source / "b.mid").write_bytes(b"second")

    def progress(step, _total, _message):
        if step == 1:
            (source / "a.mid").write_bytes(b"changed")

    with pytest.raises(media.SmartPianoSoftMediaError, match="changed"):
        media.read_floppy_source(source, tmp_path / "snapshot", progress=progress)
    assert not (tmp_path / "snapshot").exists()


def test_snapshot_rejects_links_duplicate_case_and_nested_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.mid"
    outside.write_bytes(b"do not follow")
    try:
        (source / "song.mid").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    with pytest.raises(media.SmartPianoSoftMediaError, match="not links"):
        media.read_floppy_source(source, tmp_path / "snapshot")
    (source / "song.mid").unlink()
    (source / "song.mid").write_bytes(b"song")
    with pytest.raises(media.SmartPianoSoftMediaError, match="outside"):
        media.read_floppy_source(source, source / "snapshot")


def test_image_snapshot_uses_session_and_always_cleans_up(tmp_path, monkeypatch):
    source = tmp_path / "disk.img"
    source.write_bytes(b"image")
    original = tmp_path / "song.mid"
    original.write_bytes(b"original midi")
    calls = []
    session = SimpleNamespace(
        list_entries=lambda: ImageListing([ImageEntry("SONG.MID", 13, 13)], 0, 512),
        extract_file=lambda path: original,
        _assert_source_unchanged=lambda: calls.append("identity checked"),
        cleanup=lambda: calls.append("cleaned"),
    )
    monkeypatch.setattr(media.FloppyImageSession, "load", lambda *_a, **_k: session)
    destination = media.read_floppy_source(source, tmp_path / "snapshot")
    assert (destination / "SONG.MID").read_bytes() == b"original midi"
    assert calls == ["identity checked", "cleaned"]
    assert source.read_bytes() == b"image"


def test_image_path_traversal_is_rejected_and_session_cleaned(tmp_path, monkeypatch):
    source = tmp_path / "disk.img"
    source.write_bytes(b"image")
    cleaned = []
    session = SimpleNamespace(
        list_entries=lambda: ImageListing([ImageEntry("../SONG.MID", 1, 1)], 0, 512),
        cleanup=lambda: cleaned.append(True),
    )
    monkeypatch.setattr(media.FloppyImageSession, "load", lambda *_a, **_k: session)
    with pytest.raises(media.SmartPianoSoftMediaError, match="unsafe"):
        media.read_floppy_source(source, tmp_path / "snapshot")
    assert cleaned == [True]
    assert not (tmp_path / "snapshot").exists()


def test_helpers_are_terminated_on_timeout(monkeypatch):
    process = SimpleNamespace(poll=lambda: None, pid=123)
    monkeypatch.setattr(media.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(media.os, "killpg", lambda *_args: None, raising=False)
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(media.time, "monotonic", lambda: next(ticks))
    terminated = []
    monkeypatch.setattr(media, "_terminate_process", lambda child: terminated.append(child))
    with pytest.raises(media.SmartPianoSoftMediaError, match="within"):
        media._run_command(["helper"], timeout=1)
    assert terminated == [process]


def test_helpers_are_terminated_when_cancelled(monkeypatch):
    process = SimpleNamespace(poll=lambda: None, pid=123)
    monkeypatch.setattr(media.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(media.os, "killpg", lambda *_args: None, raising=False)
    checks = iter([False, True])
    terminated = []
    monkeypatch.setattr(media, "_terminate_process", lambda child: terminated.append(child))
    with pytest.raises(FloppyOperationCancelled):
        media._run_command(["helper"], timeout=30, cancel=lambda: next(checks))
    assert terminated == [process]


def test_physical_cd_unsupported_platform_has_manual_fallback_message(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "win32")
    assert media.discover_cd_drives() == []
    with pytest.raises(media.SmartPianoSoftMediaError, match="pair existing audio"):
        media.read_cd_toc("D:")


def test_helper_dispatch_does_not_intercept_normal_startup():
    assert media.run_media_helper_from_argv(["app"]) is None
    assert media.run_media_helper_from_argv(["app", media.MEDIA_HELPER_ARG]) == 2
