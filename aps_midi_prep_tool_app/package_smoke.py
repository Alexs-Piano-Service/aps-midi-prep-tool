"""Exercise the installed application's UI, image tools and MP3 encoder.

This command uses only bundled application code and standard-library fixtures.
The Windows runner supplies a fresh working directory, restricted PATH and a
process-tree deadline. A source-mode run is useful for development but is
explicitly distinguished from frozen-package acceptance in the report.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
import wave

from .app_info import APP_VERSION

SMOKE_ARGUMENT = "--aps-package-smoke"
REPORT_FILENAME = "package-smoke.json"
CASE_NAMES = ("ui", "img", "hfe", "mp3")


def _sha256(path):
    with open(path, "rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def _write_report(directory, report):
    staged = directory / "package-smoke.json.tmp"
    with staged.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staged, directory / REPORT_FILENAME)


def _midi_bytes(note):
    title = b"Package smoke recording"
    track = (b"\x00\xff\x03" + bytes([len(title)]) + title
             + b"\x00\xff\x51\x03\x07\xa1\x20\x00\xc0\x00"
             + bytes([0, 0x90, note, 90, 0x83, 0x60, 0x80, note, 0])
             + b"\x00\xff\x2f\x00")
    return (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0"
            + b"MTrk" + len(track).to_bytes(4, "big") + track)


def _isolate_user_state(directory):
    # This command exits immediately after the checks. Keep its settings,
    # recovery and subprocess scratch files inside the new results directory.
    for name in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                 "XDG_CACHE_HOME", "XDG_STATE_HOME", "TEMP", "TMP", "TMPDIR"):
        location = directory / "state" / name.lower()
        location.mkdir(parents=True)
        os.environ[name] = str(location)
    tempfile.tempdir = os.environ["TEMP"]
    os.environ["APS_MIDI_RENAME_RECOVERY_DIR"] = str(directory / "state" / "rename-recovery")


def _check_ui(directory, state):
    from PySide6.QtCore import QEvent, QSettings
    from PySide6.QtWidgets import QApplication

    settings_dir = directory / "settings"
    settings = QSettings(str(settings_dir / "package-smoke.ini"), QSettings.IniFormat)
    settings.setFallbacksEnabled(False)
    application = QApplication.instance() or QApplication([sys.argv[0]])
    state["application"] = application  # Keep Qt alive for the remaining checks.
    from .main_window import MidiTitleWindow

    window = MidiTitleWindow(settings=settings)
    try:
        settings_file = Path(window.settings.fileName()).resolve()
        if not settings_file.is_relative_to(settings_dir.resolve()):
            raise RuntimeError("The package check did not isolate application settings.")
        window.show()
        application.processEvents()
        if not window.isVisible():
            raise RuntimeError("The main application window did not become visible.")
        if not window.close():
            raise RuntimeError("The main application window refused to close.")
        application.processEvents()
        if window.isVisible():
            raise RuntimeError("The main application window remained visible after Close.")
        window.settings.sync()
        return {"settings_file": str(settings_file), "window_shown_and_closed": True}
    finally:
        window.deleteLater()
        application.sendPostedEvents(None, QEvent.DeferredDelete)
        application.processEvents()


@contextmanager
def _opened_image(path):
    from .floppy_image import FloppyImageSession

    session = FloppyImageSession.load(str(path))
    try:
        yield session
    finally:
        session.cleanup()


def _assert_payloads(session, expected):
    payloads = {entry.path: Path(session.extract_file(entry.path)).read_bytes()
                for entry in session.list_entries().entries if not entry.directory}
    if payloads != expected:
        raise RuntimeError("Reopened image contents do not match the original recordings.")


def _check_img(directory, state):
    from .floppy_image import DISK_FORMAT_BY_KEY, create_floppy_images_from_files

    source = directory / "Source songs é with spaces"
    source.mkdir()
    expected = {"SONG1.MID": _midi_bytes(60), "SONG2.MID": _midi_bytes(72)}
    specs = []
    for name, payload in expected.items():
        path = source / name
        path.write_bytes(payload)
        specs.append({"host_path": str(path), "image_path": name})
    img = directory / "Package test é.img"
    outputs = create_floppy_images_from_files(specs, str(img), "img", DISK_FORMAT_BY_KEY["ibm.720"])
    if outputs != [str(img)] or img.stat().st_size != 737280:
        raise RuntimeError("The image builder did not produce one 720 KB IMG.")
    with _opened_image(img) as session:
        _assert_payloads(session, expected)
    state.update(img=img, expected=expected)
    return {"files_verified": len(expected), "sha256": _sha256(img)}


def _check_hfe(directory, state):
    if "img" not in state:
        raise RuntimeError("IMG creation must pass before the HFE round trip can run.")
    from .floppy_image import _find_gw

    img = state["img"]
    before = _sha256(img)
    hfe = directory / "Package test é.hfe"
    restored = directory / "Restored from HFE.img"
    with _opened_image(img) as session:
        session.export_to(str(hfe), "hfe")
    if hfe.read_bytes()[:8] != b"HXCPICFE":
        raise RuntimeError("The image converter did not produce an HFE file.")
    with _opened_image(hfe) as session:
        _assert_payloads(session, state["expected"])
        session.export_to(str(restored), "img")
    with _opened_image(restored) as session:
        _assert_payloads(session, state["expected"])
    if _sha256(img) != before:
        raise RuntimeError("The HFE round trip changed the original IMG.")
    return {"files_verified": len(state["expected"]), "greaseweazle": _find_gw(),
            "hfe_sha256": _sha256(hfe), "restored_img_sha256": _sha256(restored)}


def _check_mp3(directory, _state):
    from .main_window import (
        _convert_wav_for_audio_export, _find_lame_command, _inspect_midi_bytes, _write_preview_wav,
    )

    inspection = _inspect_midi_bytes(_midi_bytes(60))
    if not inspection["notes"]:
        raise RuntimeError("The bundled MIDI parser did not find the piano note.")
    wav = directory / "Built-in piano é.wav"
    mp3 = directory / "Built-in piano é.mp3"
    _write_preview_wav(inspection["notes"], str(wav), inspection["duration"])
    with wave.open(str(wav), "rb") as audio:
        frames = audio.readframes(audio.getnframes())
        if not frames or not any(frames):
            raise RuntimeError("The built-in piano renderer produced empty or silent audio.")
    deadline = time.monotonic() + 60
    _convert_wav_for_audio_export(
        str(wav), str(mp3), "mp3", cancel_callback=lambda: time.monotonic() >= deadline,
    )
    encoded = mp3.read_bytes()
    if len(encoded) < 100 or not (encoded[0] == 0xFF and encoded[1] & 0xE0 == 0xE0):
        raise RuntimeError("LAME did not create an MPEG audio stream.")
    return {"renderer": "built-in piano", "lame": _find_lame_command(),
            "mp3_bytes": len(encoded), "sha256": _sha256(mp3)}


def run_package_smoke(output_directory):
    directory = Path(output_directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1, "app_version": APP_VERSION,
        "frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
        "executable_sha256": _sha256(sys.executable), "platform": sys.platform,
        "started_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
        "cases": {name: {"status": "pending"} for name in CASE_NAMES},
    }
    _write_report(directory, report)
    _isolate_user_state(directory)
    state = {}
    for name, check in zip(CASE_NAMES, (_check_ui, _check_img, _check_hfe, _check_mp3)):
        report["cases"][name] = {"status": "running"}
        _write_report(directory, report)
        started = time.monotonic()
        try:
            details = check(directory, state)
            result = {"status": "passed", **details}
        except Exception as exc:
            result = {"status": "failed", "error": str(exc), "traceback": traceback.format_exc()}
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["cases"][name] = result
        _write_report(directory, report)
    passed = all(case["status"] == "passed" for case in report["cases"].values())
    report["status"] = "passed" if passed else "failed"
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    _write_report(directory, report)
    return 0 if passed else 1


def run_package_smoke_from_argv(argv):
    if len(argv) < 2 or argv[1] != SMOKE_ARGUMENT:
        return None
    if len(argv) != 3:
        return 2
    try:
        return run_package_smoke(argv[2])
    except Exception:
        if sys.stderr is not None:
            traceback.print_exc()
        return 2
