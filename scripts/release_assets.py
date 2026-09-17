"""Stage draft assets, or publish only a complete, accepted release.

Publication is an explicit command; packaging never publishes a release.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess

try:
    from .release_gate import ReleaseGateError, github_get, require_successful_ci
    from .release_metadata import ReleaseMetadataError, ROOT, validate_release_metadata
except ImportError:
    from release_gate import ReleaseGateError, github_get, require_successful_ci
    from release_metadata import ReleaseMetadataError, ROOT, validate_release_metadata


CHECKS = {"launch", "img_create_reopen", "hfe_roundtrip", "mp3_render"}


def gh(*args):
    return subprocess.check_output(["gh", *args], cwd=ROOT, text=True)


def require_draft(release, tag):
    if release.get("tagName") != tag or release.get("isDraft") is not True:
        raise ReleaseGateError("Assets may only be staged or published from the matching draft release.")


def remote_tag_commit(repository, tag, *, get=github_get):
    ref = get(f"repos/{repository}/git/ref/tags/{tag}")
    if ref.get("ref") != f"refs/tags/{tag}":
        raise ReleaseGateError("The exact remote release tag is missing.")
    target = ref.get("object", {})
    for _ in range(8):
        sha = target.get("sha", "")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            break
        if target.get("type") == "commit":
            return sha
        if target.get("type") != "tag":
            break
        target = get(f"repos/{repository}/git/tags/{sha}").get("object", {})
    raise ReleaseGateError("The remote release tag does not resolve to a commit.")


def validate_assets(metadata, release, acceptance, sha):
    tag = f"v{metadata['version']}"
    require_draft(release, tag)
    if acceptance.get("tag") != tag or acceptance.get("commit") != sha:
        raise ReleaseGateError("Acceptance evidence must identify the exact release tag and commit.")
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    # Undeclared package uploads must also have acceptance evidence.
    packages = set(metadata["required_assets"]) | {
        name for name in assets if name.lower().endswith((".exe", ".zip", ".appimage"))
    }
    for name in sorted(packages):
        asset = assets.get(name, {})
        if asset.get("state") != "uploaded" or asset.get("size", 0) <= 0:
            raise ReleaseGateError(f"Missing or incomplete release asset: {name}")
        if name == "windows-test-kit.zip":
            continue
        digest = asset.get("digest") or ""
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise ReleaseGateError(f"Release asset has no verifiable SHA-256 digest: {name}")
        platforms = {"linux"} if name.lower().endswith(".appimage") else {"windows-10", "windows-11"}
        for platform in platforms:
            records = [record for record in acceptance.get("packages", [])
                       if record.get("asset") == name and record.get("platform") == platform
                       and record.get("sha256", "").lower() == digest[7:].lower()
                       and record.get("clean_machine") is True
                       and isinstance(record.get("tester"), str) and record["tester"].strip()
                       and isinstance(record.get("evidence"), str) and record["evidence"].strip()
                       and all(record.get("checks", {}).get(check) == "PASS" for check in CHECKS)]
            if not records:
                raise ReleaseGateError(f"Missing clean-machine acceptance for {name} on {platform} at its uploaded hash.")


def release_assets(mode, tag, repository, *, files=(), acceptance_path=None):
    metadata = validate_release_metadata(tag=tag, require_ready=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise ReleaseGateError("Use a clean checkout of the release commit.")
    require_successful_ci(repository, sha)
    if remote_tag_commit(repository, tag) != sha:
        raise ReleaseGateError("The remote tag must point to this exact validated commit.")
    # Listing drafts avoids treating network/authentication errors as 'not found'.
    releases = json.loads(gh("release", "list", "--repo", repository, "--limit", "1000", "--json", "tagName,isDraft"))
    matching = next((release for release in releases if release.get("tagName") == tag), None)
    if matching is None:
        if mode != "stage":
            raise ReleaseGateError("Prepare a draft before publication.")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        notes = changelog.split(f"## [{metadata['version']}] - ", 1)[1].split("\n## [", 1)[0]
        notes = notes.split("\n", 1)[1].strip()
        gh("release", "create", tag, "--repo", repository, "--draft", "--verify-tag", "--title", tag,
           "--notes", notes)
    else:
        require_draft(matching, tag)
    if mode == "stage":
        if not files or any(not Path(path).is_file() or Path(path).stat().st_size == 0 for path in files):
            raise ReleaseGateError("Supply existing, nonempty package files to stage.")
        gh("release", "upload", tag, "--repo", repository, *map(str, files))
        return
    acceptance = json.loads(Path(acceptance_path).read_text(encoding="utf-8-sig"))
    release = json.loads(gh("release", "view", tag, "--repo", repository, "--json", "tagName,isDraft,assets"))
    validate_assets(metadata, release, acceptance, sha)
    gh("release", "edit", tag, "--repo", repository, "--draft=false")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("stage", "publish"))
    parser.add_argument("tag")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--files", nargs="+")
    parser.add_argument("--acceptance", type=Path)
    args = parser.parse_args()
    if args.mode == "publish" and args.acceptance is None:
        parser.error("publish requires --acceptance with completed clean-machine evidence")
    try:
        release_assets(args.mode, args.tag, args.repo, files=args.files or (), acceptance_path=args.acceptance)
    except (ReleaseGateError, ReleaseMetadataError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Release blocked: {exc}\n")


if __name__ == "__main__":
    main()
