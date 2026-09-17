"""Package acceptance runs the real app entry point and binds reports to the EXE."""

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


def _invoke(tmp_path, *args, code=None):
    working = tmp_path / "fresh working directory"
    working.mkdir()
    command = ([sys.executable, "-c", code] if code else
               [sys.executable, str(ROOT / "aps_midi_prep_tool.py")])
    return subprocess.run(
        [*command, *map(str, args)], cwd=working, capture_output=True, text=True,
        timeout=45, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )


def test_real_app_smoke_exercises_ui_img_hfe_and_mp3(tmp_path):
    missing = [name for name in ("mformat", "mcopy", "mdir", "lame", "gw") if not shutil.which(name)]
    if missing:
        pytest.skip("Native image/audio tools unavailable: " + ", ".join(missing))
    results = tmp_path / "new results é"

    completed = _invoke(tmp_path, package_smoke.SMOKE_ARGUMENT, results)

    report = json.loads((results / package_smoke.REPORT_FILENAME).read_text(encoding="utf-8"))
    assert completed.returncode == 0, completed.stderr + json.dumps(report, indent=2)
    assert report["status"] == "passed"
    assert set(report["cases"]) == set(package_smoke.CASE_NAMES)
    assert all(case["status"] == "passed" for case in report["cases"].values())
    assert report["frozen"] is False
    assert report["executable_sha256"] == hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    assert Path(report["cases"]["ui"]["settings_file"]).is_relative_to(results / "settings")
    assert (results / "Package test é.img").stat().st_size == 737280
    assert (results / "Restored from HFE.img").stat().st_size == 737280
    assert report["cases"]["mp3"]["renderer"] == "built-in piano"


def test_existing_results_are_never_reused_or_overwritten(tmp_path):
    results = tmp_path / "old results"
    results.mkdir()
    previous = results / package_smoke.REPORT_FILENAME
    previous.write_text("previous acceptance evidence")

    completed = _invoke(tmp_path, package_smoke.SMOKE_ARGUMENT, results)

    assert completed.returncode == 2
    assert list(results.iterdir()) == [previous]
    assert previous.read_text() == "previous acceptance evidence"


def test_a_failed_check_is_recorded_and_cli_exits_unsuccessfully(tmp_path):
    results = tmp_path / "failed results"
    code = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from aps_midi_prep_tool_app import app, package_smoke
def missing_tool(*args):
    raise OSError('The bundled image converter could not start')
package_smoke._check_ui = lambda *args: {{}}
package_smoke._check_img = lambda *args: {{}}
package_smoke._check_hfe = missing_tool
package_smoke._check_mp3 = lambda *args: {{}}
app.main()
"""

    completed = _invoke(tmp_path, package_smoke.SMOKE_ARGUMENT, results, code=code)

    report = json.loads((results / package_smoke.REPORT_FILENAME).read_text(encoding="utf-8"))
    assert completed.returncode == 1
    assert report["status"] == "failed"
    assert report["cases"]["hfe"]["status"] == "failed"
    assert "could not start" in report["cases"]["hfe"]["error"]
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


def test_mp3_case_rejects_non_audio_encoder_output(tmp_path, monkeypatch):
    from aps_midi_prep_tool_app import main_window

    def corrupt_output(_wav, output, _format, **_kwargs):
        Path(output).write_bytes(b"not an MP3" * 100)

    monkeypatch.setattr(main_window, "_convert_wav_for_audio_export", corrupt_output)
    with pytest.raises(RuntimeError, match="MPEG audio stream"):
        package_smoke._check_mp3(tmp_path, {})
