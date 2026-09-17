"""Build redistributable, self-created fixtures on a development/CI machine.

Run with python -m scripts.build_windows_test_kit --output dist/windows-test-kit.
The resulting folder needs no Python, Git, or mtools on the recipient's PC.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil

from aps_midi_prep_tool_app.app_info import APP_VERSION
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
)


def _vlq(number):
    encoded = [number & 127]
    while number >> 7:
        number >>= 7
        encoded.insert(0, (number & 127) | 128)
    return bytes(encoded)


def midi_bytes(title, padding=0):
    title = title.encode("ascii", errors="replace")
    track = b"\x00\xff\x03" + _vlq(len(title)) + title
    if padding:
        track += b"\x00\xff\x01" + _vlq(padding) + b"x" * padding
    track += b"\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk" + len(track).to_bytes(4, "big") + track


CASES = [
    "Startup", "Create 720K IMG", "Modify and reopen IMG", "Preserve unrelated files",
    "Clean E-SEQ export", "Create HFE", "Inspect HFE", "Modify and reopen HFE",
    "IMG to HFE", "HFE to IMG", "Capacity boundary", "Filename handling",
    "Damaged image recovery", "Cancel operation", "Close during disk activity",
    "Destination failure and retry", "Physical floppy recovery", "Final clean-machine verification",
    "Render and play MP3",
]


def build_kit(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixtures = output / "fixtures"
    for name in ("songs", "filename-cases", "capacity", "mixed-source", "images"):
        (fixtures / name).mkdir(parents=True)
    for n in range(1, 4):
        (fixtures / "songs" / f"0{n} Test Song.mid").write_bytes(midi_bytes(f"Test Song {n}"))
    for name in ("Long Song Name Number One.mid", "Test Song.mid", "NOEXTENSION",
                 "SONG.MID.bak", "SONG.FIL.old", "SONG~", ".tmp.mid", ".hidden.mid"):
        (fixtures / "filename-cases" / name).write_bytes(midi_bytes(name))
    # Case aliases and DOS device names cannot be separate normal files on Windows.
    (fixtures / "capacity/NEARFULL.MID").write_bytes(midi_bytes("Near full", 700000))
    (fixtures / "capacity/TOOBIG.MID").write_bytes(midi_bytes("Too large", 800000))
    files = {"SONG1.FIL": convert_midi_bytes_to_eseq_bytes(midi_bytes("Test Song 1")),
             "SONG2.FIL": convert_midi_bytes_to_eseq_bytes(midi_bytes("Test Song 2")),
             "NOTES.TXT": b"Keep these original notes.\r\n", "EXTRA.DAT": bytes(range(256)),
             "PSONG.MNG": b"Opaque preservation test data", "PDISK.MNG": b"Opaque disk sidecar"}
    specs = []
    for name, data in files.items():
        host = fixtures / "mixed-source" / name
        host.write_bytes(data)
        specs.append({"host_path": str(host), "image_path": name})
    mixed = fixtures / "images/mixed720.img"
    create_floppy_images_from_files(specs, str(mixed), "img", DISK_FORMAT_BY_KEY["ibm.720"])
    session = FloppyImageSession.load(str(mixed))
    try:
        session.commit_to_source(generate_pianodir=True, eseq_variant="disklavier")
        # Creating HFE fixtures is required: a kit missing HFE support must fail.
        session.export_to(str(fixtures / "images/DSKA0000.HFE"), "hfe")
    finally:
        session.cleanup()
    damaged = bytearray(mixed.read_bytes())
    damaged[:512] = bytes(512)  # Intact FAT, catalog, and songs; damaged boot sector.
    (fixtures / "images/damaged-boot.img").write_bytes(damaged)
    (fixtures / "images/truncated.img").write_bytes(mixed.read_bytes()[:4096])
    root = Path(__file__).resolve().parents[1]
    shutil.copy2(root / "docs/windows-test-plan.md", output / "TEST-PLAN.md")
    shutil.copy2(root / "scripts/start_windows_manual_test.ps1", output / "Start-ManualTest.ps1")
    with (output / "results-template.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Test", "Result", "Notes"])
        writer.writerows((case, "NOT RUN", "") for case in CASES)
    manifest = {"app_version": APP_VERSION, "fixtures": {}}
    for path in sorted(fixtures.rglob("*")):
        if path.is_file():
            manifest["fixtures"][path.relative_to(output).as_posix()] = {
                "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing directories are refused")
    args = parser.parse_args()
    tool_dirs = [os.environ[name] for name in ("APS_WINDOWS_MTOOLS_DIR", "APS_WINDOWS_GW_DIR")
                 if os.environ.get(name)]
    os.environ["PATH"] = os.pathsep.join([*tool_dirs, os.environ.get("PATH", "")])
    print(build_kit(args.output))
