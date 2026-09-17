"""Require recorded cross-platform CI success for the exact release commit."""

import argparse
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from .release_metadata import ReleaseMetadataError, validate_release_metadata
except ImportError:
    from release_metadata import ReleaseMetadataError, validate_release_metadata


WORKFLOW = "ci.yml"
REQUIRED_JOBS = frozenset({"Tests (ubuntu-latest)", "Tests (windows-latest)"})


class ReleaseGateError(RuntimeError):
    pass


def github_get(path, params=None):
    url = "https://api.github.com/" + path
    if params:
        url += "?" + urlencode(params)
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "APS-MIDI-Prep-Tool-release-gate",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            return json.load(response)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise ReleaseGateError(f"Could not verify GitHub CI: {exc}") from exc


def require_successful_ci(repository, sha, *, get=github_get):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReleaseGateError("Use an owner/repository GitHub repository name.")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise ReleaseGateError("Use the full 40-character commit SHA.")
    sha = sha.lower()
    prefix = f"repos/{repository}/actions"
    # A PR merge result or another commit's green checks cannot validate a tag.
    data = get(f"{prefix}/workflows/{WORKFLOW}/runs", {"head_sha": sha, "event": "push", "per_page": 100})
    runs = [
        run for run in data.get("workflow_runs", [])
        if run.get("head_sha") == sha and run.get("event") == "push"
    ]
    if not runs:
        raise ReleaseGateError(f"No push CI run for {sha}. Push the commit and wait for both CI jobs before tagging.")
    run = max(runs, key=lambda item: item["id"])
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ReleaseGateError(f"The latest push CI run for {sha} has not succeeded: {run.get('html_url', '')}")
    jobs = []
    page = 1
    while True:
        data = get(f"{prefix}/runs/{run['id']}/jobs", {"filter": "latest", "per_page": 100, "page": page})
        batch = data.get("jobs", [])
        jobs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    for name in sorted(REQUIRED_JOBS):
        matching = [job for job in jobs if job.get("name") == name]
        if not matching or any(
            job.get("status") != "completed" or job.get("conclusion") != "success"
            or job.get("head_sha") != sha
            for job in matching
        ):
            raise ReleaseGateError(f"Required CI job did not pass for {sha}: {name}")
    return run.get("html_url", f"https://github.com/{repository}/actions/runs/{run['id']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub owner/repository")
    parser.add_argument("--sha", required=True, help="Full commit SHA to release")
    parser.add_argument("--tag", help="Requested release tag; also requires publication-ready metadata")
    args = parser.parse_args()
    try:
        validate_release_metadata(tag=args.tag, require_ready=args.tag is not None)
        url = require_successful_ci(args.repo, args.sha)
    except (ReleaseGateError, ReleaseMetadataError) as exc:
        parser.exit(1, f"Release blocked: {exc}\n")
    print(f"Cross-platform CI passed: {url}")


if __name__ == "__main__":
    main()
