"""Package acceptance runs the real app entry point and binds reports to its executable."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from aps_midi_prep_tool_app import app, package_smoke

ROOT = Path(__file__).resolve().parents[1]
IMAGE_TOOLS = ("mformat", "mcopy", "mdir", "mren", "mdel")


def _require_tools(names):
    missing = [name for name in names if not shutil.which(name)]
    if missing:
        pytest.skip("Native image/audio tools unavailable: " + ", ".join(missing))


def _invoke(tmp_path, *args, code=None):
    working = tmp_path / "fresh working directory"
    working.mkdir()
    command = ([sys.executable, "-c", code] if code else
               [sys.executable, str(ROOT / "aps_midi_prep_tool.py")])
    return subprocess.run(
        [*command, *map(str, args)], cwd=working, capture_output=True, text=True,
        timeout=45, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )


def test_real_app_smoke_exercises_ui_img_source_changes_hfe_and_mp3(tmp_path):
    _require_tools((*IMAGE_TOOLS, "lame", "gw"))
    results = tmp_path / "new results é"

    completed = _invoke(tmp_path, package_smoke.SMOKE_ARGUMENT, results)

    report = json.loads((results / package_smoke.REPORT_FILENAME).read_text(encoding="utf-8"))
    assert completed.returncode == 0, completed.stderr + json.dumps(report, indent=2)
    assert report["status"] == "passed"
    assert set(report["cases"]) == set(package_smoke.CASE_NAMES)
    assert all(case["status"] == "passed" for case in report["cases"].values())
    assert report["frozen"] is False
    assert report["build_identity"]["commit"]
    assert report["executable_sha256"] == hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    assert Path(report["cases"]["ui"]["settings_file"]).is_relative_to(results / "settings")
    assert (results / "Package test é.img").stat().st_size == 737280
    assert (results / "Restored from HFE.img").stat().st_size == 737280
    assert report["cases"]["mp3"]["renderer"] == "built-in piano"
    assert report["cases"]["ui"]["smart_pianosoft_utility_hidden"] is True
    assert "smart_pianosoft" not in report["cases"]
    assert not (results / "Synthetic Smart PianoSoft source").exists()
    source_changes = report["cases"]["image_source_changes"]
    assert source_changes["stale_writes_rejected"] == [
        "commit_to_source", "export_to_source", "export_to_images_source",
    ]
    assert source_changes["external_source_bytes_preserved"] is True
    assert source_changes["external_file_preserved"] is True
    assert source_changes["pending_edits_recovered_to_new_image"] is True
    assert source_changes["reload_and_repeat_save_succeeded"] is True
    recovered = Path(source_changes["recovered_image"])
    assert recovered.is_relative_to(results)
    assert source_changes["recovered_image_sha256"] == hashlib.sha256(recovered.read_bytes()).hexdigest()


def test_explicit_developer_smoke_adds_retained_smart_pianosoft_case(tmp_path):
    _require_tools((*IMAGE_TOOLS, "lame", "gw"))
    results = tmp_path / "developer results"

    completed = _invoke(tmp_path, package_smoke.SMOKE_ARGUMENT, results,
                        package_smoke.SMART_PIANOSOFT_ARGUMENT)

    report = json.loads((results / package_smoke.REPORT_FILENAME).read_text(encoding="utf-8"))
    assert completed.returncode == 0, completed.stderr + json.dumps(report, indent=2)
    assert report["status"] == "passed"
    assert set(report["cases"]) == set(package_smoke.CASE_NAMES + package_smoke.OPTIONAL_CASE_NAMES)
    assert report["cases"]["ui"]["smart_pianosoft_utility_hidden"] is True
    smart = report["cases"]["smart_pianosoft"]
    assert smart["dialog_shown_and_closed"] is True
    assert smart["loaded_image_list_verified"] is True
    assert smart["automatic_scan_verified"] is True
    assert smart["source_reread_not_required"] is True
    assert smart["snapshot_workspace_cleaned"] is True
    assert smart["album_title"] == "Original package album"
    assert smart["catalog_track"] == 1
    assert smart["song_title"] == "Original package phrase"
    assert smart["song_filename"] == "01.MID"
    assert smart["source_image_sha256"] == hashlib.sha256(Path(smart["source_image"]).read_bytes()).hexdigest()
    original_folder = results / "Synthetic Smart PianoSoft source"
    assert set(smart["original_hashes"]) == {"01.MID", "PSONG.MNG", "PDISK.MNG"}
    for name, digest in smart["original_hashes"].items():
        assert digest == hashlib.sha256((original_folder / name).read_bytes()).hexdigest()
    assert smart["originals_preserved"] is True
    assert smart["synthetic_audio_only"] is True
    assert smart["confidence"] > .98
    assert smart["hardware_verified"] is False


def test_source_change_case_preserves_external_edits_and_recovers_pending_edits(tmp_path):
    _require_tools(IMAGE_TOOLS)
    state = {}
    package_smoke._check_img(tmp_path, state)

    details = package_smoke._check_image_source_changes(tmp_path, state)

    assert details["stale_writes_rejected"] == [
        "commit_to_source", "export_to_source", "export_to_images_source",
    ]
    assert details["external_source_bytes_preserved"] is True
    assert details["external_file_preserved"] is True
    assert details["pending_edits_recovered_to_new_image"] is True
    assert details["reload_and_repeat_save_succeeded"] is True
    assert details["mcopy"] == shutil.which("mcopy")


def test_source_change_case_detects_a_missing_guard(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app.floppy_image import FloppyImageSession

    _require_tools(IMAGE_TOOLS)
    state = {}
    package_smoke._check_img(tmp_path, state)
    monkeypatch.setattr(FloppyImageSession, "_assert_source_unchanged", lambda _self: None)

    with pytest.raises(RuntimeError, match="Stale image save was accepted by commit_to_source"):
        package_smoke._check_image_source_changes(tmp_path, state)

    # Demonstrate that bypassing the safeguard actually reproduces data loss,
    # so acceptance cannot silently pass when an old implementation is bundled.
    with package_smoke._opened_image(tmp_path / "Externally changed é.img") as overwritten:
        names = {entry.path for entry in overwritten.list_entries().entries}
    assert "OUTSIDE.MID" not in names
    assert "STAGED.MID" in names


def test_existing_results_are_never_reused_or_overwritten(tmp_path):
    results = tmp_path / "old results"
    results.mkdir()
    previous = results / package_smoke.REPORT_FILENAME
    previous.write_text("previous acceptance evidence")

    completed = _invoke(tmp_path, package_smoke.SMOKE_ARGUMENT, results)

    assert completed.returncode == 2
    assert list(results.iterdir()) == [previous]
    assert previous.read_text() == "previous acceptance evidence"


@pytest.mark.parametrize("failed_case", ("hfe", "image_source_changes", "smart_pianosoft"))
def test_a_failed_check_is_recorded_and_cli_exits_unsuccessfully(tmp_path, failed_case):
    results = tmp_path / "failed results"
    code = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from aps_midi_prep_tool_app import app, package_smoke
def missing_tool(*args):
    raise OSError('The bundled image converter could not start')
package_smoke._check_ui = lambda *args: {{}}
package_smoke._check_img = lambda *args: {{}}
package_smoke._check_image_source_changes = lambda *args: {{}}
package_smoke._check_hfe = lambda *args: {{}}
package_smoke._check_mp3 = lambda *args: {{}}
package_smoke._check_smart_pianosoft = lambda *args: {{}}
setattr(package_smoke, {"_check_" + failed_case!r}, missing_tool)
app.main()
"""

    optional = [package_smoke.SMART_PIANOSOFT_ARGUMENT] if failed_case == "smart_pianosoft" else []
    completed = _invoke(tmp_path, package_smoke.SMOKE_ARGUMENT, results, *optional, code=code)

    report = json.loads((results / package_smoke.REPORT_FILENAME).read_text(encoding="utf-8"))
    assert completed.returncode == 1
    assert report["status"] == "failed"
    assert report["cases"][failed_case]["status"] == "failed"
    assert "could not start" in report["cases"][failed_case]["error"]
    assert report["cases"]["mp3"]["status"] == "passed"


def test_dispatch_propagates_failure_before_regular_startup(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aps", package_smoke.SMOKE_ARGUMENT, "unused"])
    monkeypatch.setattr(package_smoke, "run_package_smoke", lambda _: 1)

    with pytest.raises(SystemExit) as exit_code:
        app.main()

    assert exit_code.value.code == 1


def test_invalid_command_arguments_fail_without_running_checks(monkeypatch):
    monkeypatch.setattr(package_smoke, "run_package_smoke", lambda _: pytest.fail("Checks must not run"))
    assert package_smoke.run_package_smoke_from_argv(["aps"]) is None
    assert package_smoke.run_package_smoke_from_argv(["aps", package_smoke.SMOKE_ARGUMENT]) == 2
    assert package_smoke.run_package_smoke_from_argv(["aps", package_smoke.SMOKE_ARGUMENT, "a", "b"]) == 2
    assert package_smoke.run_package_smoke_from_argv([
        "aps", package_smoke.SMOKE_ARGUMENT, "a", "--include-smart-pianosoft", "b",
    ]) == 2


def test_release_ui_smoke_rejects_exposing_deferred_utility(tmp_path, monkeypatch, qt_application):
    from aps_midi_prep_tool_app.main_window import MidiTitleWindow

    monkeypatch.setattr(MidiTitleWindow, "ENABLE_SMART_PIANOSOFT_UTILITY", True)
    with pytest.raises(RuntimeError, match="deferred Smart PianoSoft utility is exposed"):
        package_smoke._check_ui(tmp_path, {"application": qt_application})


def test_default_smoke_ignores_unavailable_experimental_dependencies(tmp_path):
    results = tmp_path / "release results"
    code = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from aps_midi_prep_tool_app import app, package_smoke
def missing_dependency(*args):
    raise ImportError('Experimental dependency unavailable')
for name in package_smoke.CASE_NAMES:
    setattr(package_smoke, '_check_' + name, lambda *args: {{}})
package_smoke._check_smart_pianosoft = missing_dependency
app.main()
"""

    completed = _invoke(tmp_path, package_smoke.SMOKE_ARGUMENT, results, code=code)

    report = json.loads((results / package_smoke.REPORT_FILENAME).read_text(encoding="utf-8"))
    assert completed.returncode == 0, completed.stderr
    assert report["status"] == "passed"
    assert "smart_pianosoft" not in report["cases"]


def test_mp3_case_rejects_non_audio_encoder_output(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app import main_window

    def corrupt_output(_wav, output, _format, **_kwargs):
        Path(output).write_bytes(b"not an MP3" * 100)

    monkeypatch.setattr(main_window, "_convert_wav_for_audio_export", corrupt_output)
    with pytest.raises(RuntimeError, match="MPEG audio stream"):
        package_smoke._check_mp3(tmp_path, {})


def test_smart_pianosoft_case_rejects_missing_control_channel(tmp_path, monkeypatch, qt_application):
    import wave
    import numpy as np
    from aps_midi_prep_tool_app import disklavier_audio

    _require_tools(IMAGE_TOOLS)
    encode = disklavier_audio.encode_disklavier_wav

    def corrupt_control(events, audio_path, destination, **kwargs):
        result = encode(events, audio_path, destination, **kwargs)
        with wave.open(str(destination), "rb") as incoming:
            parameters = incoming.getparams()
            frames = np.frombuffer(incoming.readframes(incoming.getnframes()), dtype="<i2").copy()
        frames[1::2] = 0
        with wave.open(str(destination), "wb") as outgoing:
            outgoing.setparams(parameters)
            outgoing.writeframes(frames.tobytes())
        return result

    monkeypatch.setattr(disklavier_audio, "encode_disklavier_wav", corrupt_control)
    with pytest.raises(RuntimeError, match="music/control channels"):
        package_smoke._check_smart_pianosoft(tmp_path, {"application": qt_application})
    assert (tmp_path / "Synthetic Smart PianoSoft.img").is_file()
    assert not (tmp_path / "Synthetic Smart PianoSoft temporarily offline.img").exists()


def test_smart_pianosoft_case_rejects_missing_current_list_integration(tmp_path, monkeypatch, qt_application):
    from aps_midi_prep_tool_app.main_window import MidiTitleWindow

    _require_tools(IMAGE_TOOLS)
    monkeypatch.setattr(MidiTitleWindow, "_smart_pianosoft_loaded_source", lambda _self: None)
    with pytest.raises(RuntimeError, match="main song list did not supply"):
        package_smoke._check_smart_pianosoft(tmp_path, {"application": qt_application})
    assert (tmp_path / "Synthetic Smart PianoSoft.img").is_file()
    assert not (tmp_path / "Synthetic Smart PianoSoft temporarily offline.img").exists()
