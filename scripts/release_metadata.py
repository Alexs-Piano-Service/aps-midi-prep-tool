"""Validate release identity without importing the GUI or executing app_info."""

import argparse
import ast
from datetime import date
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataError(ValueError):
    pass


def validate_release_metadata(root=ROOT, *, tag=None, require_ready=False):
    root = Path(root)
    try:
        tree = ast.parse((root / "aps_midi_prep_tool_app/app_info.py").read_text(encoding="utf-8"))
        versions = [ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "APP_VERSION"
                            for target in node.targets)]
        metadata = json.loads((root / "packaging/release.json").read_text(encoding="utf-8"))
        readme = (root / "README.md").read_text(encoding="utf-8")
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        release = ET.parse(root / "packaging/com.alexpianoservice.APSMidiPrepTool.metainfo.xml").find("releases/release")
        version = metadata["version"]
        if versions != [version] or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ReleaseMetadataError("Application and release metadata versions must agree.")
        if tag is not None and tag != f"v{version}":
            raise ReleaseMetadataError(f"Release tag must be v{version}; received {tag!r}.")
        ready = metadata["status"] == "ready"
        if metadata["status"] not in {"unreleased", "ready"}:
            raise ReleaseMetadataError("Release status must be unreleased or ready.")
        release_date = metadata.get("date")
        if ready:
            if date.fromisoformat(release_date) > date.today():
                raise ReleaseMetadataError("Release date cannot be in the future.")
        elif release_date is not None:
            raise ReleaseMetadataError("An unreleased version must not claim a publication date.")
        appstream_date = release_date if ready else metadata["development_date"]
        if date.fromisoformat(appstream_date) > date.today():
            raise ReleaseMetadataError("AppStream build date cannot be in the future.")
        label = "Current" if ready else "Development"
        if not re.search(rf"^{label} version: `{re.escape(version)}`$", readme, re.MULTILINE):
            raise ReleaseMetadataError("README version/status does not match release metadata.")
        if ready and re.search(
            rf"Development version:|changes planned for {re.escape(version)}(?!\d|\.\d)",
            readme,
        ):
            raise ReleaseMetadataError("Remove development wording from the released README.")
        headings = re.findall(r"^## \[([^]]+)\](?: - (.+))?$", changelog, re.MULTILINE)
        expected = (version, release_date if ready else "Unreleased")
        if not headings or headings[0] != expected or sum(v == version for v, _ in headings) != 1:
            raise ReleaseMetadataError("Consolidate the current changelog under its matching version and date/status.")
        if release is None or release.get("version") != version:
            raise ReleaseMetadataError("AppStream version does not match the application.")
        if release.get("date") != appstream_date or release.get("type", "stable") != ("stable" if ready else "development"):
            raise ReleaseMetadataError("AppStream date/status does not match release metadata.")
        assets = metadata["required_assets"]
        if (not isinstance(assets, list) or not assets or len(set(assets)) != len(assets)
                or any(not isinstance(name, str) or not name or Path(name).name != name or "\\" in name
                       or any(character in name for character in "*?[]") for name in assets)):
            raise ReleaseMetadataError("List unique, exact required release asset filenames.")
        if require_ready and not ready:
            raise ReleaseMetadataError("Release is still unreleased. Set its actual date and reconcile metadata before tagging.")
        return metadata
    except (OSError, SyntaxError, ValueError, TypeError, KeyError, ET.ParseError) as exc:
        if isinstance(exc, ReleaseMetadataError):
            raise
        raise ReleaseMetadataError(f"Invalid release metadata: {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    try:
        metadata = validate_release_metadata(tag=args.tag, require_ready=args.require_ready)
    except ReleaseMetadataError as exc:
        parser.exit(1, f"Release blocked: {exc}\n")
    print(f"Release metadata agrees: {metadata['version']} ({metadata['status']})")


if __name__ == "__main__":
    main()
