"""Release tags require both platforms to pass on the exact published commit."""

import subprocess

import pytest

from scripts import release_gate, tag_release


SHA = "a" * 40
REPOSITORY = "example/aps-midi-prep-tool"


def successful_run():
    return {
        "id": 123, "head_sha": SHA, "event": "push", "status": "completed",
        "conclusion": "success", "html_url": f"https://github.com/{REPOSITORY}/actions/runs/123",
    }


def successful_jobs():
    return [
        {"name": name, "head_sha": SHA, "status": "completed", "conclusion": "success"}
        for name in sorted(release_gate.REQUIRED_JOBS)
    ]


def api(runs, jobs):
    def get(path, params):
        if path.endswith("/ci.yml/runs"):
            assert params["head_sha"] == SHA
            assert params["event"] == "push"
            return {"workflow_runs": runs}
        assert path.endswith("/123/jobs")
        assert params["filter"] == "latest"
        start = (params["page"] - 1) * params["per_page"]
        return {"jobs": jobs[start:start + params["per_page"]]}
    return get


def test_gate_accepts_both_platforms_for_exact_commit_and_paginates_jobs():
    jobs = [{"name": f"other job {index}"} for index in range(100)] + successful_jobs()
    assert release_gate.require_successful_ci(REPOSITORY, SHA, get=api([successful_run()], jobs)).endswith("/123")


@pytest.mark.parametrize("change", [
    {"head_sha": "b" * 40}, {"event": "pull_request"},
    {"status": "in_progress", "conclusion": None},
    {"conclusion": "failure"}, {"conclusion": "cancelled"}, {"conclusion": "skipped"},
])
def test_gate_rejects_wrong_commit_event_and_unsuccessful_runs(change):
    run = {**successful_run(), **change}
    with pytest.raises(release_gate.ReleaseGateError):
        release_gate.require_successful_ci(REPOSITORY, SHA, get=api([run], successful_jobs()))


def test_gate_rejects_zero_runs():
    with pytest.raises(release_gate.ReleaseGateError, match="No push CI run"):
        release_gate.require_successful_ci(REPOSITORY, SHA, get=api([], []))


def test_old_success_cannot_hide_new_failure():
    older = {**successful_run(), "id": 100}
    latest = {**successful_run(), "conclusion": "failure"}
    with pytest.raises(release_gate.ReleaseGateError, match="latest push CI run"):
        release_gate.require_successful_ci(REPOSITORY, SHA, get=api([older, latest], successful_jobs()))


@pytest.mark.parametrize("platform", sorted(release_gate.REQUIRED_JOBS))
@pytest.mark.parametrize("failure", ["missing", "failure", "skipped", "cancelled", "wrong_commit"])
def test_gate_requires_real_success_from_each_platform(platform, failure):
    jobs = successful_jobs()
    if failure == "missing":
        jobs = [job for job in jobs if job["name"] != platform]
    else:
        for job in jobs:
            if job["name"] == platform:
                job["head_sha" if failure == "wrong_commit" else "conclusion"] = (
                    "b" * 40 if failure == "wrong_commit" else failure
                )
    with pytest.raises(release_gate.ReleaseGateError, match="Required CI job"):
        release_gate.require_successful_ci(REPOSITORY, SHA, get=api([successful_run()], jobs))


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(tag_release, "ROOT", tmp_path)
    tag_release.git("init", "--quiet")
    (tmp_path / "file.txt").write_text("Committed source", encoding="utf-8")
    tag_release.git("add", "file.txt")
    tag_release.git("-c", "user.name=APS Tests", "-c", "user.email=tests@example.invalid", "commit", "-qm", "Initial")
    # An annotated tag needs an identity; configure only this temporary repo.
    tag_release.git("config", "user.name", "APS Tests")
    tag_release.git("config", "user.email", "tests@example.invalid")
    return tmp_path


def test_tag_is_not_created_when_ci_cannot_be_verified(checkout, monkeypatch):
    def reject(_repo, _sha):
        raise release_gate.ReleaseGateError("No recorded CI success")
    monkeypatch.setattr(tag_release, "require_successful_ci", reject)
    with pytest.raises(release_gate.ReleaseGateError):
        tag_release.tag_release("v1.2.3", REPOSITORY)
    assert tag_release.git("tag", "--list") == ""


def test_tag_records_the_validated_commit_and_run(checkout, monkeypatch):
    sha = tag_release.git("rev-parse", "HEAD")
    url = successful_run()["html_url"]
    def passed(repo, candidate):
        assert (repo, candidate) == (REPOSITORY, sha)
        return url
    monkeypatch.setattr(tag_release, "require_successful_ci", passed)
    assert tag_release.tag_release("v1.2.3", REPOSITORY) == (sha, url)
    assert tag_release.git("rev-parse", "v1.2.3^{commit}") == sha
    assert tag_release.git("cat-file", "-t", "v1.2.3") == "tag"
    assert url in tag_release.git("cat-file", "-p", "v1.2.3")
    with pytest.raises(subprocess.CalledProcessError):
        tag_release.tag_release("v1.2.3", REPOSITORY)


@pytest.mark.parametrize("during_validation", [False, True])
def test_dirty_worktree_cannot_be_tagged(checkout, monkeypatch, during_validation):
    def passed(_repo, _sha):
        (checkout / "file.txt").write_text("Uncommitted change", encoding="utf-8")
        return successful_run()["html_url"]
    monkeypatch.setattr(tag_release, "require_successful_ci", passed)
    if not during_validation:
        passed(None, None)
    with pytest.raises(release_gate.ReleaseGateError):
        tag_release.tag_release("v1.2.3", REPOSITORY)
    assert tag_release.git("tag", "--list") == ""
