"""Publication requires complete packages accepted at the exact uploaded bytes.

All GitHub and git calls are replaced in these tests; no release is published.
"""

import hashlib
import json

import pytest

from scripts import release_assets
from scripts.release_gate import ReleaseGateError


SHA = "a" * 40
TAG = "v1.2.3"
REPOSITORY = "example/aps-midi-prep-tool"
ASSETS = ("APSMIDIPrepTool-1.2.3-Setup.exe", "APSMIDIPrepTool-1.2.3-windows-portable.zip",
          "APSMidiPrepTool-1.2.3-x86_64.AppImage", "windows-test-kit.zip")


@pytest.fixture
def accepted_release():
    metadata = {"version": "1.2.3", "required_assets": list(ASSETS)}
    release = {"tagName": TAG, "isDraft": True, "assets": []}
    acceptance = {"tag": TAG, "commit": SHA, "packages": []}
    for name in ASSETS:
        digest = hashlib.sha256(name.encode()).hexdigest()
        release["assets"].append({"name": name, "size": 1234, "state": "uploaded", "digest": f"sha256:{digest}"})
        if name == "windows-test-kit.zip":
            continue
        platforms = ("linux",) if name.endswith(".AppImage") else ("windows-10", "windows-11")
        for platform in platforms:
            acceptance["packages"].append({
                "asset": name, "platform": platform, "sha256": digest, "clean_machine": True,
                "tester": "Release tester", "evidence": f"acceptance/{platform}/{name}.txt",
                "checks": {check: "PASS" for check in release_assets.CHECKS},
            })
    return metadata, release, acceptance


def test_complete_draft_with_all_platforms_and_matching_hashes_is_accepted(accepted_release):
    release_assets.validate_assets(*accepted_release, SHA)


@pytest.mark.parametrize("name", ASSETS)
def test_every_intended_package_and_test_kit_must_be_present(accepted_release, name):
    metadata, release, acceptance = accepted_release
    release["assets"] = [asset for asset in release["assets"] if asset["name"] != name]
    with pytest.raises(ReleaseGateError, match="Missing or incomplete"):
        release_assets.validate_assets(metadata, release, acceptance, SHA)


@pytest.mark.parametrize("change", ({"state": "starter"}, {"state": "uploading"}, {"size": 0}, {"size": -1}))
def test_partially_uploaded_or_empty_package_cannot_be_published(accepted_release, change):
    metadata, release, acceptance = accepted_release
    release["assets"][0].update(change)
    with pytest.raises(ReleaseGateError, match="incomplete"):
        release_assets.validate_assets(metadata, release, acceptance, SHA)


@pytest.mark.parametrize("digest", (None, "", "sha256:1234", "sha1:" + "a" * 40, "sha256:" + "z" * 64))
def test_upload_requires_a_verifiable_sha256_digest(accepted_release, digest):
    metadata, release, acceptance = accepted_release
    release["assets"][0]["digest"] = digest
    with pytest.raises(ReleaseGateError, match="digest"):
        release_assets.validate_assets(metadata, release, acceptance, SHA)


@pytest.mark.parametrize("change", ({"tag": "v9.8.7"}, {"commit": "b" * 40}))
def test_acceptance_must_identify_the_exact_tag_and_commit(accepted_release, change):
    metadata, release, acceptance = accepted_release
    acceptance.update(change)
    with pytest.raises(ReleaseGateError, match="exact release tag and commit"):
        release_assets.validate_assets(metadata, release, acceptance, SHA)


@pytest.mark.parametrize("change", ({"isDraft": False}, {"isDraft": "true"}, {"tagName": "v9.8.7"}))
def test_assets_cannot_be_published_from_the_wrong_or_public_release(accepted_release, change):
    metadata, release, acceptance = accepted_release
    release.update(change)
    with pytest.raises(ReleaseGateError, match="matching draft"):
        release_assets.validate_assets(metadata, release, acceptance, SHA)


@pytest.mark.parametrize("platform", ("windows-10", "windows-11", "linux"))
def test_each_supported_platform_requires_clean_machine_acceptance(accepted_release, platform):
    metadata, release, acceptance = accepted_release
    acceptance["packages"] = [record for record in acceptance["packages"] if record["platform"] != platform]
    with pytest.raises(ReleaseGateError, match="clean-machine acceptance"):
        release_assets.validate_assets(metadata, release, acceptance, SHA)


def test_acceptance_of_an_earlier_upload_cannot_validate_replacement_bytes(accepted_release):
    metadata, release, acceptance = accepted_release
    release["assets"][0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ReleaseGateError, match="uploaded hash"):
        release_assets.validate_assets(metadata, release, acceptance, SHA)


@pytest.mark.parametrize("field,value", (
    ("sha256", "b" * 64), ("clean_machine", False), ("clean_machine", "true"),
    ("tester", ""), ("evidence", "   "), ("tester", None), ("evidence", None),
    ("tester", 42), ("evidence", {"missing": "report"}),
))
def test_incomplete_or_wrong_hash_acceptance_is_rejected(accepted_release, field, value):
    metadata, release, acceptance = accepted_release
    acceptance["packages"][0][field] = value
    with pytest.raises(ReleaseGateError, match="clean-machine acceptance"):
        release_assets.validate_assets(metadata, release, acceptance, SHA)


@pytest.mark.parametrize("check", sorted(release_assets.CHECKS))
@pytest.mark.parametrize("result", ("FAIL", "SKIP", None))
def test_all_packaged_image_and_audio_checks_must_pass(accepted_release, check, result):
    metadata, release, acceptance = accepted_release
    acceptance["packages"][0]["checks"][check] = result
    with pytest.raises(ReleaseGateError, match="clean-machine acceptance"):
        release_assets.validate_assets(metadata, release, acceptance, SHA)


def test_unlisted_additional_binary_also_requires_acceptance(accepted_release):
    metadata, release, acceptance = accepted_release
    release["assets"].append({"name": "APSMIDIPrepTool.exe", "state": "uploaded", "size": 1234,
                              "digest": "sha256:" + "c" * 64})
    with pytest.raises(ReleaseGateError, match="APSMIDIPrepTool.exe"):
        release_assets.validate_assets(metadata, release, acceptance, SHA)


@pytest.fixture
def fake_publication(tmp_path, monkeypatch, accepted_release):
    metadata, release, acceptance = accepted_release
    calls = []
    state = {"dirty": False, "releases": [{"tagName": TAG, "isDraft": True}], "release": release,
             "metadata": metadata, "sha": SHA}
    evidence = tmp_path / "acceptance.json"
    evidence.write_text(json.dumps(acceptance), encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        "## [1.2.3] - 2020-01-02\n\nCurrent release changes.\n\n"
        "## [1.2.2] - 2019-12-01\n\nPrevious release changes.\n", encoding="utf-8")

    def check_output(command, **_kwargs):
        assert command[0] == "git", "Tests must not invoke external release tools"
        if command[1:] == ["rev-parse", "HEAD"]:
            return state["sha"] + "\n"
        if command[1:] == ["status", "--porcelain"]:
            return " M file.py\n" if state["dirty"] else ""
        raise AssertionError(command)

    def gh(*args):
        calls.append(args)
        if args[:2] == ("release", "list"):
            return json.dumps(state["releases"])
        if args[:2] == ("release", "view"):
            return json.dumps(state["release"])
        if args[:2] in (("release", "create"), ("release", "upload"), ("release", "edit")):
            return ""
        raise AssertionError(args)

    def metadata_for_tag(*, tag, require_ready):
        assert tag == TAG and require_ready is True
        return state["metadata"]

    def ci(repository, sha):
        assert repository == REPOSITORY and sha == SHA

    def no_network(*args, **_kwargs):
        raise AssertionError(f"Unexpected unmocked GitHub request: {args}")

    monkeypatch.setattr(release_assets.subprocess, "check_output", check_output)
    monkeypatch.setattr(release_assets, "ROOT", tmp_path)
    monkeypatch.setattr(release_assets, "gh", gh)
    monkeypatch.setattr(release_assets, "validate_release_metadata", metadata_for_tag)
    monkeypatch.setattr(release_assets, "require_successful_ci", ci)
    monkeypatch.setattr(release_assets, "github_get", no_network)
    monkeypatch.setattr(release_assets, "remote_tag_commit", lambda *_args: SHA, raising=False)
    return state, calls, evidence


def test_publication_occurs_only_after_all_uploaded_packages_pass_validation(fake_publication):
    _state, calls, evidence = fake_publication
    release_assets.release_assets("publish", TAG, REPOSITORY, acceptance_path=evidence)
    assert calls[-1] == ("release", "edit", TAG, "--repo", REPOSITORY, "--draft=false")
    assert [call[1] for call in calls] == ["list", "view", "edit"]


def test_incomplete_upload_never_reaches_the_publish_command(fake_publication):
    state, calls, evidence = fake_publication
    state["release"]["assets"].pop()
    with pytest.raises(ReleaseGateError, match="incomplete"):
        release_assets.release_assets("publish", TAG, REPOSITORY, acceptance_path=evidence)
    assert all(call[:2] != ("release", "edit") for call in calls)


def test_dirty_checkout_stops_before_any_release_mutation(fake_publication):
    state, calls, evidence = fake_publication
    state["dirty"] = True
    with pytest.raises(ReleaseGateError, match="clean checkout"):
        release_assets.release_assets("publish", TAG, REPOSITORY, acceptance_path=evidence)
    assert calls == []


def test_staging_only_creates_a_draft_and_uploads_files(fake_publication, tmp_path):
    state, calls, _evidence = fake_publication
    state["releases"] = []
    package = tmp_path / ASSETS[0]
    package.write_bytes(b"Packaged application")
    release_assets.release_assets("stage", TAG, REPOSITORY, files=[package])
    assert [call[1] for call in calls] == ["list", "create", "upload"]
    assert "--draft" in calls[1]
    assert "--verify-tag" in calls[1]
    assert calls[1][calls[1].index("--notes") + 1] == "Current release changes."
    assert all("--draft=false" not in call for call in calls)


def test_remote_tag_mismatch_prevents_staging_and_publication(fake_publication, monkeypatch):
    _state, calls, evidence = fake_publication
    monkeypatch.setattr(release_assets, "remote_tag_commit", lambda *_args: "b" * 40)
    with pytest.raises(ReleaseGateError, match="remote tag"):
        release_assets.release_assets("publish", TAG, REPOSITORY, acceptance_path=evidence)
    assert calls == []


def test_remote_branch_with_release_name_is_not_a_release_tag():
    paths = []

    def get(path):
        paths.append(path)
        if path == f"repos/{REPOSITORY}/commits/{TAG}":
            # A same-named branch resolves, but the tag does not exist.
            return {"sha": SHA}
        assert path == f"repos/{REPOSITORY}/git/ref/tags/{TAG}"
        raise ReleaseGateError("Tag not found")

    with pytest.raises(ReleaseGateError, match="Tag not found"):
        release_assets.remote_tag_commit(REPOSITORY, TAG, get=get)
    assert paths == [f"repos/{REPOSITORY}/git/ref/tags/{TAG}"]


@pytest.mark.parametrize("annotated", (False, True))
def test_remote_tag_resolves_the_exact_commit_including_annotated_tags(annotated):
    tag_sha = "d" * 40

    def get(path):
        if path == f"repos/{REPOSITORY}/git/ref/tags/{TAG}":
            return {"ref": f"refs/tags/{TAG}",
                    "object": {"type": "tag" if annotated else "commit", "sha": tag_sha if annotated else SHA}}
        assert annotated and path == f"repos/{REPOSITORY}/git/tags/{tag_sha}"
        return {"object": {"type": "commit", "sha": SHA}}

    assert release_assets.remote_tag_commit(REPOSITORY, TAG, get=get) == SHA


def test_malformed_tag_chain_cannot_loop_indefinitely():
    calls = []

    def get(path):
        calls.append(path)
        return {"ref": f"refs/tags/{TAG}", "object": {"type": "tag", "sha": "d" * 40}}

    with pytest.raises(ReleaseGateError):
        release_assets.remote_tag_commit(REPOSITORY, TAG, get=get)
    assert len(calls) <= 10
