"""Elevatable USB formatting helper.

This module is intentionally small: it reads a JSON job, asks the shared
formatting backend to validate and run it, then writes a JSON result. On
Windows it can be packaged as its own UAC/admin executable; in source-tree
development it also supports --dry-run on any platform.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from aps_midi_prep_tool_app.formatting.usb_format_core import (
    UsbFormatCancelled,
    UsbFormatError,
    read_usb_format_job,
    run_usb_format_job,
    usb_format_result_to_dict,
    write_usb_format_result,
)


def _result_payload(*, ok, error="", cancelled=False, dry_run=False, result=None):
    payload = usb_format_result_to_dict(result or {})
    payload["ok"] = bool(ok)
    payload["cancelled"] = bool(cancelled)
    payload["dry_run"] = bool(dry_run or payload.get("dry_run", False))
    if error:
        payload["error"] = str(error)
    return payload


def _write_or_print_result(payload, result_path):
    if result_path:
        write_usb_format_result(payload, result_path)
        return
    json.dump(usb_format_result_to_dict(payload), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run an APS MIDI Prep Tool USB formatting job.")
    parser.add_argument("--job", required=True, help="Path to the JSON formatting job.")
    parser.add_argument("--result", help="Path where the JSON result should be written.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and describe the job without writing.")
    parser.add_argument(
        "--traceback",
        action="store_true",
        help="Include a Python traceback in the result JSON for development diagnostics.",
    )
    args = parser.parse_args(argv)

    try:
        job = read_usb_format_job(args.job)
        result = run_usb_format_job(job, dry_run=args.dry_run)
        payload = _result_payload(ok=True, dry_run=args.dry_run, result=result)
        _write_or_print_result(payload, args.result)
        return 0
    except UsbFormatCancelled as exc:
        payload = _result_payload(ok=False, error=str(exc) or "Operation cancelled.", cancelled=True, dry_run=args.dry_run)
    except (UsbFormatError, OSError) as exc:
        payload = _result_payload(ok=False, error=str(exc), dry_run=args.dry_run)
    except Exception as exc:
        payload = _result_payload(ok=False, error=str(exc), dry_run=args.dry_run)
        if args.traceback:
            payload["traceback"] = traceback.format_exc()

    _write_or_print_result(payload, args.result)
    return 3 if payload.get("cancelled") else 2


if __name__ == "__main__":
    raise SystemExit(main())
