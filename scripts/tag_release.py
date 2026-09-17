"""Create a local release tag only after its commit has passed GitHub CI."""

import argparse
from pathlib import Path
import subprocess

try:
    from .release_gate import ReleaseGateError, require_successful_ci
    from .release_metadata import ReleaseMetadataError, validate_release_metadata
except ImportError:  # Direct invocation: python scripts/tag_release.py ...
    from release_gate import ReleaseGateError, require_successful_ci
    from release_metadata import ReleaseMetadataError, validate_release_metadata


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def tag_release(tag, repository):
    git("check-ref-format", f"refs/tags/{tag}")
    if git("status", "--porcelain"):
        raise ReleaseGateError("Commit or remove working-tree changes before tagging.")
    validate_release_metadata(ROOT, tag=tag, require_ready=True)
    sha = git("rev-parse", "HEAD")
    url = require_successful_ci(repository, sha)
    # Do not accidentally tag a different HEAD or new uncommitted changes
    # introduced while waiting for the network response.
    if git("rev-parse", "HEAD") != sha or git("status", "--porcelain"):
        raise ReleaseGateError("The checkout changed during validation. Run the command again.")
    git("tag", "-a", "--message", f"Release {tag}\n\nValidated by {url}", "--", tag, sha)
    return sha, url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Release tag, for example v0.8.3")
    parser.add_argument("--repo", required=True, help="GitHub owner/repository that will publish this release")
    args = parser.parse_args()
    try:
        sha, url = tag_release(args.tag, args.repo)
    except (ReleaseGateError, ReleaseMetadataError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Tag not created: {exc}\n")
    print(f"Created local tag {args.tag} at {sha}. CI: {url}")
    print("Push this tag, prepare a draft, and validate all packages before publication.")


if __name__ == "__main__":
    main()
