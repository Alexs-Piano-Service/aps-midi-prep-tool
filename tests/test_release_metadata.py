"""Release identity must agree before tagging or packaging can proceed."""

from datetime import date, timedelta
import json

import pytest

from scripts.release_metadata import ReleaseMetadataError, validate_release_metadata


VERSION = "1.2.3"
RELEASE_DATE = "2020-01-02"
ASSETS = ["APSMIDIPrepTool-1.2.3-Setup.exe", "APSMIDIPrepTool-1.2.3-windows-portable.zip",
          "APSMidiPrepTool-1.2.3-x86_64.AppImage", "windows-test-kit.zip"]
APPSTREAM = "packaging/com.alexpianoservice.APSMidiPrepTool.metainfo.xml"


def _write_release(root, *, ready=True):
    (root / "aps_midi_prep_tool_app").mkdir(exist_ok=True)
    (root / "packaging").mkdir(exist_ok=True)
    metadata = {"version": VERSION, "status": "ready" if ready else "unreleased",
                "date": RELEASE_DATE if ready else None, "development_date": RELEASE_DATE,
                "required_assets": ASSETS}
    (root / "packaging/release.json").write_text(json.dumps(metadata), encoding="utf-8")
    (root / "aps_midi_prep_tool_app/app_info.py").write_text(
        f'APP_VERSION = "{VERSION}"\nraise RuntimeError("Validation must not execute app_info")\n',
        encoding="utf-8")
    label = "Current" if ready else "Development"
    (root / "README.md").write_text(f"{label} version: `{VERSION}`\n", encoding="utf-8")
    heading_date = RELEASE_DATE if ready else "Unreleased"
    (root / "CHANGELOG.md").write_text(f"## [{VERSION}] - {heading_date}\n\nRelease changes.\n", encoding="utf-8")
    release_type = "stable" if ready else "development"
    (root / APPSTREAM).write_text(
        f'<component><releases><release version="{VERSION}" date="{RELEASE_DATE}" '
        f'type="{release_type}"/></releases></component>', encoding="utf-8")
    return root


@pytest.fixture
def ready_release(tmp_path):
    return _write_release(tmp_path)


def _change_metadata(root, **changes):
    path = root / "packaging/release.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata.update(changes)
    path.write_text(json.dumps(metadata), encoding="utf-8")


def _replace(root, filename, old, new):
    path = root / filename
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def test_matching_ready_release_accepts_its_tag_without_executing_application(ready_release):
    metadata = validate_release_metadata(ready_release, tag=f"v{VERSION}", require_ready=True)
    assert metadata["version"] == VERSION
    assert metadata["required_assets"] == ASSETS


def test_development_metadata_is_valid_for_builds_but_refuses_release_tagging(tmp_path):
    root = _write_release(tmp_path, ready=False)
    assert validate_release_metadata(root)["date"] is None
    with pytest.raises(ReleaseMetadataError, match="unreleased"):
        validate_release_metadata(root, tag=f"v{VERSION}", require_ready=True)


@pytest.mark.parametrize("tag", ("v1.2.4", "v9.8.7", "1.2.3", "v1.2.3-rc1"))
def test_requested_tag_must_match_application_version(ready_release, tag):
    with pytest.raises(ReleaseMetadataError, match="tag"):
        validate_release_metadata(ready_release, tag=tag, require_ready=True)


@pytest.mark.parametrize("filename", (
    "aps_midi_prep_tool_app/app_info.py", "README.md", "CHANGELOG.md", APPSTREAM,
))
def test_each_version_document_must_agree(ready_release, filename):
    _replace(ready_release, filename, VERSION, "9.8.7")
    with pytest.raises(ReleaseMetadataError):
        validate_release_metadata(ready_release)


def test_metadata_version_cannot_disagree_with_application(ready_release):
    _change_metadata(ready_release, version="9.8.7")
    with pytest.raises(ReleaseMetadataError, match="versions"):
        validate_release_metadata(ready_release)


@pytest.mark.parametrize("filename", ("CHANGELOG.md", APPSTREAM))
def test_each_release_date_must_agree(ready_release, filename):
    _replace(ready_release, filename, RELEASE_DATE, "2020-01-03")
    with pytest.raises(ReleaseMetadataError):
        validate_release_metadata(ready_release, require_ready=True)


@pytest.mark.parametrize("release_date", (None, "not-a-date", "2020-02-30"))
def test_ready_release_requires_a_valid_date(ready_release, release_date):
    _change_metadata(ready_release, date=release_date)
    with pytest.raises(ReleaseMetadataError):
        validate_release_metadata(ready_release, require_ready=True)


def test_future_publication_date_is_refused_even_if_documents_match(ready_release):
    future = (date.today() + timedelta(days=1)).isoformat()
    _change_metadata(ready_release, date=future)
    for filename in ("CHANGELOG.md", APPSTREAM):
        _replace(ready_release, filename, RELEASE_DATE, future)
    with pytest.raises(ReleaseMetadataError, match="future"):
        validate_release_metadata(ready_release, require_ready=True)


def test_unreleased_version_cannot_claim_a_publication_date(tmp_path):
    root = _write_release(tmp_path, ready=False)
    _change_metadata(root, date=RELEASE_DATE)
    with pytest.raises(ReleaseMetadataError, match="publication date"):
        validate_release_metadata(root)


def test_development_appstream_date_must_match_metadata(tmp_path):
    root = _write_release(tmp_path, ready=False)
    _change_metadata(root, development_date="2020-01-03")
    with pytest.raises(ReleaseMetadataError):
        validate_release_metadata(root)


@pytest.mark.parametrize("heading", ("## [Unreleased]\n\n", f"## [{VERSION}] - {RELEASE_DATE}\n\n"))
def test_current_release_cannot_be_split_across_changelog_sections(ready_release, heading):
    path = ready_release / "CHANGELOG.md"
    path.write_text(heading + path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ReleaseMetadataError, match="changelog"):
        validate_release_metadata(ready_release)


@pytest.mark.parametrize("filename,old,new", (
    ("README.md", "Current version", "Development version"),
    (APPSTREAM, 'type="stable"', 'type="development"'),
))
def test_publication_status_must_agree_with_readme_and_appstream(ready_release, filename, old, new):
    _replace(ready_release, filename, old, new)
    with pytest.raises(ReleaseMetadataError):
        validate_release_metadata(ready_release, require_ready=True)


def test_released_readme_cannot_keep_planned_changes_wording(ready_release):
    path = ready_release / "README.md"
    path.write_text(path.read_text(encoding="utf-8")
                    + f"\nThis checkout includes changes planned for {VERSION}.\n",
                    encoding="utf-8")
    with pytest.raises(ReleaseMetadataError, match="development wording"):
        validate_release_metadata(ready_release, require_ready=True)


@pytest.mark.parametrize("assets", ([], ["app.exe", "app.exe"], ["*.exe"], ["../app.exe"],
                                   [r"folder\app.exe"], [None]))
def test_required_assets_are_exact_unique_filenames(ready_release, assets):
    _change_metadata(ready_release, required_assets=assets)
    with pytest.raises(ReleaseMetadataError, match="asset"):
        validate_release_metadata(ready_release)
