"""Exercise the installed application's UI, image tools and audio preparation.

This command uses only bundled application code and standard-library fixtures.
The package runner supplies a fresh working directory, restricted PATH and a
process-tree deadline. A source-mode run is useful for development but is
explicitly distinguished from frozen-package acceptance in the report.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback
import wave

from .app_info import APP_VERSION
from .build_info import build_identity

SMOKE_ARGUMENT = "--aps-package-smoke"
SMART_PIANOSOFT_ARGUMENT = "--include-smart-pianosoft"
REPORT_FILENAME = "package-smoke.json"
CASE_NAMES = ("ui", "img", "image_source_changes", "hfe", "mp3")
OPTIONAL_CASE_NAMES = ("smart_pianosoft",)


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
        experimental_action = getattr(window, "utilitiesSmartPianoSoftAction", None)
        if ((experimental_action is not None
                and experimental_action in window.utilitiesMenu.actions())
                or any(spec["id"] == "utilities.smart_pianosoft"
                       for spec in window._keyboard_shortcut_specs())):
            raise RuntimeError("The deferred Smart PianoSoft utility is exposed in the release UI.")
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
        return {"settings_file": str(settings_file), "window_shown_and_closed": True,
                "smart_pianosoft_utility_hidden": True}
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


def _check_image_source_changes(directory, state):
    from .floppy_image import FloppyImageError, _copy_host_file_into_image, _require_command

    if "img" not in state:
        raise RuntimeError("IMG creation must pass before the external-change check can run.")
    # Modify a disposable copy directly through mcopy while the editor keeps
    # its original working image. This reproduces an edit outside the app.
    source = directory / "Externally changed é.img"
    recovered = directory / "Recovered pending edits é.img"
    shutil.copyfile(state["img"], source)
    external_file = directory / "OUTSIDE.MID"
    external_payload = _midi_bytes(84)
    external_file.write_bytes(external_payload)
    expected = state["expected"]
    updated_expected = {**expected, "OUTSIDE.MID": external_payload}
    renames = {"SONG1.MID": "STAGED.MID"}
    staged_expected = {renames.get(name, name): payload for name, payload in expected.items()}
    rejected = []
    with _opened_image(source) as stale:
        working_before = Path(stale.working_img_path).read_bytes()
        source_before = source.read_bytes()
        _copy_host_file_into_image(str(source), str(external_file), "OUTSIDE.MID")
        newer_source = source.read_bytes()
        if newer_source == source_before:
            raise RuntimeError("The external IMG edit did not change the source.")
        with _opened_image(source) as current:
            _assert_payloads(current, updated_expected)

        writes = (
            ("commit_to_source", lambda: stale.commit_to_source(renames=renames)),
            ("export_to_source", lambda: stale.export_to(str(source), "img", renames=renames)),
            ("export_to_images_source", lambda: stale.export_to_images(
                str(source), "img", stale.disk_format, renames=renames,
            )),
        )
        for operation, write in writes:
            try:
                write()
            except FloppyImageError as exc:
                if "Source image changed" not in str(exc):
                    raise RuntimeError(f"{operation} failed without the source-change safeguard: {exc}") from exc
            else:
                raise RuntimeError(f"Stale image save was accepted by {operation} after an external file was added.")
            if source.read_bytes() != newer_source:
                raise RuntimeError(f"{operation} changed the externally updated source IMG.")
            if Path(stale.working_img_path).read_bytes() != working_before:
                raise RuntimeError(f"{operation} discarded the original working image.")
            rejected.append(operation)

        with _opened_image(source) as current:
            _assert_payloads(current, updated_expected)
        stale.export_to(str(recovered), "img", renames=renames)
        with _opened_image(recovered) as saved_edits:
            _assert_payloads(saved_edits, staged_expected)
        if source.read_bytes() != newer_source:
            raise RuntimeError("Recovering pending edits changed the externally updated source IMG.")

    # Reloading adopts the outside changes. A second save also checks that the
    # source fingerprint is refreshed after this session's own successful save.
    with _opened_image(source) as refreshed:
        refreshed.commit_to_source(renames=renames)
        _assert_payloads(refreshed, {**staged_expected, "OUTSIDE.MID": external_payload})
        refreshed.commit_to_source(renames={"STAGED.MID": "SONG1.MID"})
    with _opened_image(source) as saved:
        _assert_payloads(saved, updated_expected)
    return {
        "mcopy": _require_command("mcopy"),
        "stale_writes_rejected": rejected,
        "external_source_bytes_preserved": True,
        "external_file_preserved": True,
        "pending_edits_recovered_to_new_image": True,
        "reload_and_repeat_save_succeeded": True,
        "external_edit_sha256": hashlib.sha256(newer_source).hexdigest(),
        "recovered_image": str(recovered), "recovered_image_sha256": _sha256(recovered),
        "reloaded_source_sha256": _sha256(source),
    }


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


def _check_smart_pianosoft(directory, state):
    """Exercise frozen MIDI, NumPy, UI and encoding with original synthetic audio."""
    import mido
    import numpy as np
    from PySide6.QtCore import QEvent, QSettings
    from PySide6.QtWidgets import QApplication

    from .smart_pianosoft import build_smart_pianosoft_song_record
    from .smart_pianosoft_dialog import SmartPianoSoftDialog
    from .smart_pianosoft_sync import _envelope
    from .smart_pianosoft_workflow import prepare_album
    from .floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files
    from .main_window import MidiTitleWindow

    application = QApplication.instance() or QApplication([sys.argv[0]])
    state["application"] = application
    settings = QSettings(str(directory / "settings" / "smart-pianosoft.ini"), QSettings.IniFormat)
    settings.setFallbacksEnabled(False)
    source = directory / "Synthetic Smart PianoSoft source"
    source.mkdir()
    times = np.arange(14 * 44100) / 44100
    knots = np.arange(0, 14.02, .02)
    amplitude = np.interp(times, knots, np.random.default_rng(1248).uniform(.05, .8, len(knots)))
    carrier = np.sin(2 * np.pi * 437 * times) + .3 * np.sin(2 * np.pi * 731 * times)
    mono = np.asarray(20000 * amplitude * carrier, dtype=np.int16)
    pcm = np.column_stack((mono, mono))
    audio_path = directory / "Synthetic accompaniment.wav"
    with wave.open(str(audio_path), "wb") as audio:
        audio.setparams((2, 2, 44100, len(pcm), "NONE", "not compressed"))
        audio.writeframes(pcm.astype("<i2").tobytes())
    # Algorithm accuracy has independent unit fixtures; this case proves the
    # complete dependency chain survives freezing and creates real outputs.
    envelope = _envelope(pcm)
    track = mido.MidiTrack()
    for index, kind in ((345, 0), (1723, 2)):
        subframes = round(index * 256 / 44100 * 9600)
        minute, subframes = divmod(subframes, 60 * 9600)
        second, subframes = divmod(subframes, 9600)
        frame, subframe = divmod(subframes, 128)
        segment = envelope[index:index + 256]
        samples = np.rint(segment / np.max(np.abs(segment)) * 60).astype(int)
        track.append(mido.Message("sysex", data=[
            0x43, 0x71, 0x7B, kind, minute, second, frame, subframe, kind // 2, 3,
            *[int(value) & 127 for value in samples],
        ]))
    track.extend([
        mido.MetaMessage("set_tempo", tempo=1000000),
        mido.Message("note_on", note=60, velocity=80, time=2000),
        mido.Message("note_off", note=60, time=1000),
        mido.MetaMessage("end_of_track", time=11000),
    ])
    midi = mido.MidiFile(type=0, ticks_per_beat=1000)
    midi.tracks.append(track)
    midi.save(source / "01.MID")
    header = bytearray(b" " * 128)
    header[:48] = b"PSONG   MNG   \r\nMAX001        \r\nFILE001       \r\n"
    (source / "PSONG.MNG").write_bytes(
        bytes(header) + build_smart_pianosoft_song_record("01.MID", "Original package phrase")
    )
    album_title = "Original package album"
    disk_catalog = bytearray(b" " * 128)
    disk_catalog[:48] = b"PDISK   MNG   \r\nP.PLAYER      \r\nVer1.01DMV0.53\r\n"
    disk_catalog[48:112] = album_title.encode("ascii").ljust(64, b" ")
    disk_catalog[112:114] = b"\r\n"
    (source / "PDISK.MNG").write_bytes(disk_catalog)
    originals = {path.name: _sha256(path) for path in source.iterdir()}
    original_audio = _sha256(audio_path)
    source_image = directory / "Synthetic Smart PianoSoft.img"
    outputs = create_floppy_images_from_files(
        [{"host_path": str(path), "image_path": path.name} for path in sorted(source.iterdir())],
        str(source_image), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )
    if outputs != [str(source_image)]:
        raise RuntimeError("The Smart PianoSoft source IMG was not created.")
    original_image = _sha256(source_image)
    offline_image = directory / "Synthetic Smart PianoSoft temporarily offline.img"
    settings.setValue("sps_source", str(directory / "unavailable remembered source.img"))
    window = MidiTitleWindow(settings=settings)
    dialog = None
    try:
        window.image_session = FloppyImageSession.load(str(source_image))
        window.table.setSortingEnabled(False)
        window._load_image_rows(window.image_session.list_entries().entries)
        loaded_source = window._smart_pianosoft_loaded_source()
        if loaded_source is None or loaded_source.error:
            raise RuntimeError("The main song list did not supply a Smart PianoSoft album: "
                               + (loaded_source.error if loaded_source is not None else "no source"))
        if (len(loaded_source.tracks) != 1
                or loaded_source.tracks[0].catalog_filename != "01.MID"
                or loaded_source.tracks[0].filename != "01.MID"
                or loaded_source.tracks[0].title != "Original package phrase"
                or hashlib.sha256(loaded_source.tracks[0].midi_bytes).hexdigest() != originals["01.MID"]
                or hashlib.sha256(loaded_source.song_catalog).hexdigest() != originals["PSONG.MNG"]
                or hashlib.sha256(loaded_source.disk_catalog).hexdigest() != originals["PDISK.MNG"]):
            raise RuntimeError("The main song list changed the Smart PianoSoft source metadata or bytes.")
        # Prove that scanning and encoding depend only on the loaded list. The
        # remembered source is also unavailable, so a silent fallback fails.
        source_image.rename(offline_image)
        dialog = SmartPianoSoftDialog(settings, window, discover_on_open=False, loaded_source=loaded_source)
        dialog.show()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            application.processEvents()
            if dialog._operation == "scan" and not dialog.is_busy:
                break
            time.sleep(.01)
        if dialog.is_busy or dialog.album is None:
            raise RuntimeError("Smart PianoSoft did not automatically scan the loaded song list: "
                               + dialog.details.toPlainText())
        album = dialog.album
        if (not dialog.isVisible() or not dialog.source_edit.isReadOnly()
                or dialog.source_edit.text() != dialog.text("current_list_source", source=loaded_source.label)
                or dialog.table.rowCount() != 1
                or [dialog.table.item(0, column).text() for column in range(3)]
                   != ["1", "Original package phrase", "01.MID"]
                or album.title != album_title or album.tracks[0].number != 1
                or dialog.album_label.text() != dialog.text("album", title=album_title)
                or not dialog.album_label.isVisible() or dialog.cd_combo.count() != 1
                or source_image.exists()):
            raise RuntimeError("Smart PianoSoft did not retain loaded album, title, track and filename metadata.")
        workspace = Path(dialog._workspace.name)
        output = prepare_album(album, directory, audio_paths={1: audio_path})
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("status") != "complete" or len(manifest["tracks"]) != 1:
            raise RuntimeError("Smart PianoSoft album preparation did not complete.")
        for name, digest in originals.items():
            if _sha256(source / name) != digest or _sha256(output / "MIDI" / name) != digest:
                raise RuntimeError("Smart PianoSoft preparation changed the original MIDI/catalog.")
        if _sha256(audio_path) != original_audio or _sha256(output / "WAV/Paired01.wav") != original_audio:
            raise RuntimeError("Smart PianoSoft preparation changed the original accompaniment.")
        result = manifest["tracks"][0]
        alignment = result["alignment"]
        if (alignment["confidence"] < .98 or abs(alignment["offset_seconds"]) > .01
                or abs(alignment["time_scale"] - 1) > .001):
            raise RuntimeError("Smart PianoSoft synchronization did not recover the synthetic timing.")
        encoded_path = output / result["encoded"]
        with wave.open(str(encoded_path), "rb") as encoded:
            if (encoded.getnchannels(), encoded.getsampwidth(), encoded.getframerate()) != (2, 2, 44100):
                raise RuntimeError("Smart PianoSoft encoding produced an invalid WAV format.")
            encoded.setpos(round(result["encoding"]["audio_preroll_seconds"] * 44100))
            frames = np.frombuffer(encoded.readframes(len(pcm)), dtype="<i2").reshape(-1, 2)
        if (len(frames) != len(pcm) or not np.array_equal(frames[:, 0], mono)
                or not np.any(frames[:, 1]) or np.array_equal(frames[:, 0], frames[:, 1])):
            raise RuntimeError("Smart PianoSoft encoded music/control channels are invalid.")
        if _sha256(offline_image) != original_image:
            raise RuntimeError("Smart PianoSoft preparation changed the original source image.")
        if not dialog.close():
            raise RuntimeError("The Smart PianoSoft dialog refused to close.")
        application.processEvents()
        if dialog.isVisible() or workspace.exists():
            raise RuntimeError("The Smart PianoSoft dialog did not clean up its snapshot on Close.")
        return {"dialog_shown_and_closed": True, "synthetic_audio_only": True,
                "loaded_image_list_verified": True, "automatic_scan_verified": True,
                "source_reread_not_required": True, "snapshot_workspace_cleaned": True,
                "album_title": album_title, "catalog_track": 1,
                "song_title": "Original package phrase", "song_filename": "01.MID",
                "source_image": str(source_image), "source_image_sha256": original_image,
                "original_hashes": originals, "audio_sha256": original_audio,
                "originals_preserved": True, "confidence": alignment["confidence"],
                "encoded_wav": str(encoded_path), "encoded_sha256": _sha256(encoded_path),
                "numpy_version": np.__version__, "hardware_verified": False}
    finally:
        if offline_image.exists():
            offline_image.rename(source_image)
        if dialog is not None:
            dialog.close()
            deadline = time.monotonic() + 15
            while dialog.is_busy and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(.01)
            if dialog.is_busy:
                # Never destroy a running QThread. Keep its owner alive until
                # the package runner's process-tree deadline can stop the run.
                state["unfinished_smart_pianosoft_window"] = window
                state["unfinished_smart_pianosoft_dialog"] = dialog
                raise RuntimeError("The Smart PianoSoft scan did not stop after cancellation.")
            dialog.close()
            dialog.deleteLater()
        window._cleanup_for_close()
        window.close()
        window.deleteLater()
        application.sendPostedEvents(None, QEvent.DeferredDelete)
        application.processEvents()


def run_package_smoke(output_directory, *, include_smart_pianosoft=False):
    """Run release acceptance, optionally adding the retained experimental check."""
    case_names = CASE_NAMES + (OPTIONAL_CASE_NAMES if include_smart_pianosoft else ())
    directory = Path(output_directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1, "app_version": APP_VERSION,
        "build_identity": build_identity(),
        "frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
        "executable_sha256": _sha256(sys.executable), "platform": sys.platform,
        "started_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
        "cases": {name: {"status": "pending"} for name in case_names},
    }
    _write_report(directory, report)
    _isolate_user_state(directory)
    state = {}
    checks = {
        "ui": _check_ui, "img": _check_img,
        "image_source_changes": _check_image_source_changes,
        "hfe": _check_hfe, "mp3": _check_mp3,
        "smart_pianosoft": _check_smart_pianosoft,
    }
    for name in case_names:
        report["cases"][name] = {"status": "running"}
        _write_report(directory, report)
        started = time.monotonic()
        try:
            details = checks[name](directory, state)
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
    if len(argv) != 3 and not (len(argv) == 4 and argv[3] == SMART_PIANOSOFT_ARGUMENT):
        return 2
    try:
        if len(argv) == 4:
            return run_package_smoke(argv[2], include_smart_pianosoft=True)
        return run_package_smoke(argv[2])
    except Exception:
        if sys.stderr is not None:
            traceback.print_exc()
        return 2
