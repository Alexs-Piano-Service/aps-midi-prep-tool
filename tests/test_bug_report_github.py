"""Exercise the optional PHP hook without network access or credentials."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


PHP = shutil.which("php")
pytestmark = pytest.mark.skipif(PHP is None, reason="PHP CLI is not installed")
HELPER = Path(__file__).resolve().parents[1] / "server" / "github_bug_reports.php"
REPORT_ID = "1234567890abcdef1234567890abcdef"
HARNESS = r"""
require $argv[1];
$input = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
foreach (['ENABLED', 'REPOSITORY', 'TOKEN', 'STATE_DIR'] as $name) {
    putenv('APS_REPORT_GITHUB_' . $name);
}
foreach ($input['config'] as $name => $value) {
    putenv('APS_REPORT_GITHUB_' . $name . '=' . $value);
}
$requests = [];
$post = function ($repository, $token, $issue) use (&$requests, $input) {
    $requests[] = ['repository' => $repository, 'issue' => $issue];
    if (isset($input['exception'])) {
        throw new RuntimeException($input['exception']);
    }
    return $input['response'];
};
$results = [];
foreach ($input['reports'] as $report) {
    $results[] = \APSMidiPrepReports\githubForwardBugReport($report, $post);
}
$journals = [];
foreach (glob($input['state_dir'] . '/*.json') as $path) {
    $journals[] = json_decode(file_get_contents($path), true);
}
echo json_encode(['results' => $results, 'requests' => $requests, 'journals' => $journals], JSON_THROW_ON_ERROR);
"""


def _report(**changes):
    return {
        "report_id": REPORT_ID,
        "app": {"name": "APS MIDI Prep Tool", "version": "0.8.8"},
        **changes,
    }


def _run(tmp_path, *, reports=None, config=None, response=None, exception=None):
    settings = {
        "ENABLED": "1",
        "REPOSITORY": "example/private-reports",
        "TOKEN": "test_token_never_transmitted",
        "STATE_DIR": str(tmp_path),
    }
    settings.update(config or {})
    request = {
        "config": settings,
        "reports": reports or [_report()],
        "response": response or {"http_status": 201, "number": 17},
        "state_dir": str(tmp_path),
    }
    if exception:
        request["exception"] = exception
    process = subprocess.run(
        [PHP, "-r", HARNESS, str(HELPER)],
        input=json.dumps(request), capture_output=True, text=True, timeout=10,
        check=True,
    )
    assert not process.stderr
    return json.loads(process.stdout)


def test_issue_is_a_private_email_reference_without_report_details(tmp_path):
    report = _report(
        summary="private summary", description="private description",
        email="customer@example.invalid", contact="private phone",
        sender={"email": "private sender"},
        context={"regular_context": "/home/customer/music"},
        environment={"cwd": "/private/cwd"},
        logs={"text": "private log contents"},
    )
    result = _run(tmp_path, reports=[report])
    assert result["results"] == [{"status": "created", "number": 17}]
    issue = result["requests"][0]["issue"]
    assert set(issue) == {"title", "body"}
    assert REPORT_ID in issue["title"] and REPORT_ID in issue["body"]
    assert "0.8.8" in issue["body"] and "support email" in issue["body"]
    public = json.dumps(issue)
    for private in (
        "private summary", "private description", "customer@example.invalid",
        "private phone", "private sender", "/home/customer/music",
        "/private/cwd", "private log contents", "test_token",
    ):
        assert private not in public
    assert result["journals"] == [{"status": "created", "number": 17}]


@pytest.mark.parametrize("enabled", ["", "0", "true", "yes"])
def test_forwarding_is_disabled_unless_explicitly_enabled(tmp_path, enabled):
    result = _run(tmp_path, config={"ENABLED": enabled})
    assert result == {"results": [{"status": "disabled"}], "requests": [], "journals": []}


@pytest.mark.parametrize("report", [
    _report(kind="feedback"), _report(kind="unrecognized"),
    _report(report_id="../../private"), _report(report_id=""),
    _report(report_id=[REPORT_ID]), {},
])
def test_feedback_and_malformed_reports_are_not_forwarded(tmp_path, report):
    result = _run(tmp_path, reports=[report])
    assert result["results"] == [{"status": "skipped"}]
    assert not result["requests"] and not result["journals"]


@pytest.mark.parametrize("app", [
    {"version": "customer@example.invalid"}, {"version": "0.8.8\nprivate"},
    {"version": {"private": "value"}}, "private name", {}, None,
])
def test_malformed_app_metadata_is_not_copied_to_issue(tmp_path, app):
    result = _run(tmp_path, reports=[_report(app=app)])
    body = result["requests"][0]["issue"]["body"]
    assert "App version: `unknown`" in body
    assert "private" not in body.replace("private support email", "")
    assert "customer" not in body


@pytest.mark.parametrize("config", [
    {"REPOSITORY": ""}, {"REPOSITORY": "example/repo/../../other"},
    {"REPOSITORY": "https://example.invalid/repo"}, {"TOKEN": ""},
    {"TOKEN": "test\nAuthorization: invalid"}, {"STATE_DIR": ""},
])
def test_bad_configuration_returns_a_status_without_sending(tmp_path, config):
    result = _run(tmp_path, config=config)
    assert result["results"] == [{"status": "configuration_error"}]
    assert not result["requests"] and not result["journals"]


def test_duplicate_report_reservation_survives_separate_processes(tmp_path):
    first = _run(tmp_path)
    second = _run(tmp_path)
    assert first["results"][0]["status"] == "created"
    assert second["results"] == [{"status": "already_attempted"}]
    assert not second["requests"]
    assert second["journals"] == first["journals"]


@pytest.mark.parametrize("status", [301, 400, 401, 403, 410, 422, 429, 500, 503])
def test_github_errors_return_failure_without_throwing_or_retrying(tmp_path, status):
    result = _run(
        tmp_path, reports=[_report(), _report()],
        response={"http_status": status, "number": None, "body": "private response"},
    )
    assert result["results"] == [{"status": "failed"}, {"status": "already_attempted"}]
    assert len(result["requests"]) == 1
    assert result["journals"] == [{"status": "failed"}]
    assert "private response" not in json.dumps(result)


def test_transport_exception_keeps_email_caller_running_and_hides_secrets(tmp_path):
    result = _run(tmp_path, exception="timeout with secret test_token and private payload")
    assert result["results"] == [{"status": "failed"}]
    assert "secret" not in json.dumps(result)
    assert result["journals"] == [{"status": "failed"}]
    retry = _run(tmp_path)
    assert retry["results"] == [{"status": "already_attempted"}]
    assert not retry["requests"]


def test_created_response_without_issue_number_is_not_reported_as_success(tmp_path):
    result = _run(tmp_path, response={"http_status": 201})
    assert result["results"] == [{"status": "failed"}]


def test_new_report_id_creates_its_own_issue(tmp_path):
    result = _run(tmp_path, reports=[_report(), _report(report_id="f" * 32)])
    assert [item["status"] for item in result["results"]] == ["created", "created"]
    assert len(result["requests"]) == 2
