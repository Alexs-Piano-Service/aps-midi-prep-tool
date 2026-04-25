#!/usr/bin/env python3
"""Extract tempo information from Yamaha/Disklavier-style ESEQ .FIL/.ESQ files
as used by the 1998 ESEQ2MID.EXE converter bundled by the user.

Reverse-engineered rules:
- Base BPM is stored in header byte 0x33 as (bpm - 29).
- Event stream starts at offset 0x77.
- 0xF3 xx adds xx ticks to the running absolute time.
- 0xF4 lo hi adds ((hi << 7) | (lo & 0x7F)) ticks.
- 0xFB lo hi emits a tempo change. The decoded 15-bit value is a multiplier
  in thousandths relative to the base BPM:
      effective_bpm = base_bpm * decoded_value / 1000
  and the converter writes MIDI MPQN as floor(60000000 / effective_bpm).
- 0xF2 ends the stream.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List, Tuple


def decode_15(lo: int, hi: int) -> int:
    return (hi << 7) | (lo & 0x7F)


def base_bpm(data: bytes) -> int:
    if len(data) <= 0x33:
        raise ValueError("file too short")
    return data[0x33] + 29


def tempo_events(data: bytes) -> List[Tuple[int, int, float, int]]:
    if data[7:15] != b"COM-ESEQ":
        raise ValueError("not a COM-ESEQ file")
    bpm0 = base_bpm(data)
    out: List[Tuple[int, int, float, int]] = []
    tick = 0
    i = 0x77
    while i < len(data):
        b = data[i]
        i += 1
        if b < 0x80:
            continue
        if b == 0xF1:
            continue
        if b == 0xF3:
            if i >= len(data):
                break
            tick += data[i]
            i += 1
            continue
        if b == 0xF4:
            if i + 1 >= len(data):
                break
            tick += decode_15(data[i], data[i + 1])
            i += 2
            continue
        if b == 0xFB:
            if i + 1 >= len(data):
                break
            factor = decode_15(data[i], data[i + 1])
            i += 2
            eff_bpm = bpm0 * factor / 1000.0
            mpqn = int(60_000_000 // eff_bpm)
            out.append((tick, factor, eff_bpm, mpqn))
            continue
        if b == 0xF2:
            break
        # MIDI-like message bodies embedded directly in the stream.
        hi = b & 0xF0
        if hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
            i += 2
        elif hi in (0xC0, 0xD0):
            i += 1
        elif b == 0xFF:
            i += 1  # channel prefix meta payload byte in this format
        elif b == 0xF0:
            while i < len(data) and data[i] != 0xF7:
                # 0xF3/0xF4 can appear before F7 in this format; skip raw body here.
                if data[i] == 0xF3:
                    i += 2
                elif data[i] == 0xF4:
                    i += 3
                else:
                    i += 1
            if i < len(data):
                i += 1  # consume F7
        else:
            # unknown/reserved marker; ignore
            continue
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("paths", nargs="+", help=".FIL/.ESQ files to inspect")
    args = p.parse_args()

    for path_s in args.paths:
        path = Path(path_s)
        data = path.read_bytes()
        bpm0 = base_bpm(data)
        mpqn0 = 60_000_000 // bpm0
        print(f"{path.name}: base_bpm={bpm0} initial_mpqn={mpqn0}")
        events = tempo_events(data)
        if not events:
            print("  no FB tempo-change events found")
        else:
            for tick, factor, eff_bpm, mpqn in events:
                print(
                    f"  tick={tick:>6} factor={factor}/1000 -> bpm={eff_bpm:.3f} mpqn={mpqn}"
                )


if __name__ == "__main__":
    main()
