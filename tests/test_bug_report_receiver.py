"""Exercise the PHP receiver offline, including validation and delivery retries."""

import hashlib
import hmac
import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
import shutil
import subprocess
import time

import pytest


PHP = shutil.which("php")
pytestmark = pytest.mark.skipif(PHP is None, reason="PHP CLI is not installed")
SERVER = Path(__file__).resolve().parents[1] / "server"
SECRET = "test-shared-secret-never-transmitted"
REPORT_ID = "1234567890abcdef1234567890abcdef"
HARNESS = r"""
$input = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
define('APS_BUG_REPORT_LIBRARY_ONLY', true);
require $argv[1];
require_once $argv[2];
$_SERVER = $input['server'];
$calls = [];
ob_start();
register_shutdown_function(static function () use (&$calls): void {
    $output = ob_get_clean();
    echo json_encode([
        'status' => http_response_code(),
        'payload' => json_decode($output, true),
        'calls' => $calls,
    ], JSON_THROW_ON_ERROR);
});
$send = static function ($to, $subject, $body, $reply_to) use (&$calls, $input): bool {
    $calls[] = [
        'transport' => 'email', 'to' => $to, 'subject' => $subject,
        'body' => $body, 'reply_to' => $reply_to,
    ];
    if ($input['mail_gate'] !== '') {
        file_put_contents($input['mail_gate'] . '.started', 'started');
        $deadline = microtime(true) + 5;
        while (!file_exists($input['mail_gate'] . '.release')) {
            if (microtime(true) >= $deadline) {
                throw new RuntimeException('Test mail gate timed out');
            }
            usleep(10000);
        }
    }
    return $input['mail_result'];
};
$forward = static function (array $data) use (&$calls, $input): array {
    if ($input['forward_exception']) {
        throw new RuntimeException('private forwarding diagnostic');
    }
    $post = static function ($repository, $token, $issue) use (&$calls, $input): array {
        $calls[] = ['transport' => 'github', 'repository' => $repository, 'issue' => $issue];
        if ($input['github_exception']) {
            throw new RuntimeException('private GitHub transport diagnostic');
        }
        return $input['github_response'];
    };
    return \APSMidiPrepReports\githubForwardBugReport($data, $post);
};
if ($input['request_started'] !== '') {
    file_put_contents($input['request_started'], 'started');
}
aps_bug_report_handle(
    $input['raw'], $send, $input['default_forward'] ? null : $forward
);
"""


def _report(**changes):
    return {
        "report_id": REPORT_ID,
        "created_at": "2026-09-29T12:00:00Z",
        "summary": "The song list stopped updating",
        "description": "The selected song did not appear after extraction.",
        "contact": "customer@example.invalid",
        "app": {"name": "APS MIDI Prep Tool", "version": "0.8.8"},
        "logs": {"text": "Opened the extraction dialog"},
        **changes,
    }


def _run(
    tmp_path, *, report=None, raw=None, server=None, secret=SECRET,
    mail_result=True, github_response=None, github_exception=False,
    forward_exception=False, enabled=True, default_forward=False,
    mail_gate="", request_started="", helper_path=None,
):
    if raw is None:
        raw = json.dumps(report if report is not None else _report(), separators=(",", ":"))
    timestamp = str(int(time.time()))
    signature = hmac.new(
        SECRET.encode(), (timestamp + "." + raw).encode(), hashlib.sha256,
    ).hexdigest()
    request_server = {
        "REQUEST_METHOD": "POST",
        "HTTPS": "on",
        "CONTENT_TYPE": "application/json; charset=UTF-8",
        "CONTENT_LENGTH": str(len(raw.encode())),
        "REMOTE_ADDR": "192.0.2.10",
        "HTTP_X_APS_TIMESTAMP": timestamp,
        "HTTP_X_APS_SIGNATURE": "sha256=" + signature,
    }
    request_server.update(server or {})
    private_temp = tmp_path / "php-temp"
    private_temp.mkdir(exist_ok=True)
    github_state = tmp_path / "github-state"
    github_state.mkdir(exist_ok=True)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("APS_BUG_REPORT_", "APS_REPORT_GITHUB_"))
    }
    if secret is not None:
        environment["APS_BUG_REPORT_SECRET"] = secret
    environment.update({
        "APS_REPORT_GITHUB_ENABLED": "1" if enabled else "0",
        "APS_REPORT_GITHUB_REPOSITORY": "example/private-reports",
        "APS_REPORT_GITHUB_TOKEN": "test_token_never_transmitted",
        "APS_REPORT_GITHUB_STATE_DIR": str(github_state),
    })
    if helper_path is not None:
        environment["APS_REPORT_GITHUB_HELPER_PATH"] = str(helper_path)
    request = {
        "raw": raw,
        "server": request_server,
        "mail_result": mail_result,
        "github_response": github_response or {"http_status": 201, "number": 17},
        "github_exception": github_exception,
        "forward_exception": forward_exception,
        "default_forward": default_forward,
        "mail_gate": mail_gate,
        "request_started": request_started,
    }
    # Even a broken injection seam cannot send mail or issue an HTTP request.
    process = subprocess.run(
        [
            PHP, "-d", "disable_functions=mail,curl_exec,curl_multi_exec",
            "-d", "allow_url_fopen=0", "-d", f"sys_temp_dir={private_temp}",
            "-r", HARNESS, str(SERVER / "bug-report.php"),
            str(SERVER / "github_bug_reports.php"),
        ],
        input=json.dumps(request), capture_output=True, text=True,
        env=environment, timeout=10, check=True,
    )
    assert "private forwarding diagnostic" not in process.stderr
    assert "private GitHub transport diagnostic" not in process.stderr
    return json.loads(process.stdout)


def _transports(result):
    return [call["transport"] for call in result["calls"]]


def test_valid_signed_report_sends_email_before_creating_github_issue(tmp_path):
    result = _run(tmp_path)
    assert result["status"] == 202
    assert result["payload"] == {"ok": True}
    assert _transports(result) == ["email", "github"]
    email, github = result["calls"]
    assert email["reply_to"] == "customer@example.invalid"
    assert _report()["summary"] in email["subject"]
    assert REPORT_ID in email["body"]
    assert _report()["description"] in email["body"]
    assert REPORT_ID in github["issue"]["body"]
    assert "customer@example.invalid" not in json.dumps(github)
    assert _report()["description"] not in json.dumps(github)


def test_unconfigured_github_keeps_the_existing_email_only_endpoint(tmp_path):
    result = _run(tmp_path, enabled=False, default_forward=True)
    assert result["status"] == 202
    assert result["payload"] == {"ok": True}
    assert _transports(result) == ["email"]


def test_missing_optional_github_helper_preserves_valid_accepted_email_response(tmp_path):
    result = _run(
        tmp_path, enabled=True, default_forward=True,
        helper_path=tmp_path / "missing-helper.php",
    )
    assert result["status"] == 202
    assert result["payload"] == {"ok": True}
    assert _transports(result) == ["email"]


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_github_failure_keeps_accepted_email_and_does_not_trigger_retries(tmp_path, status):
    result = _run(tmp_path, github_response={"http_status": status})
    assert result["status"] == 202
    assert result["payload"] == {"ok": True}
    assert _transports(result) == ["email", "github"]
    retry = _run(tmp_path)
    assert retry == {"status": 202, "payload": {"ok": True, "duplicate": True}, "calls": []}


@pytest.mark.parametrize("failure", ["github_exception", "forward_exception"])
def test_github_exceptions_cannot_change_email_success(tmp_path, failure):
    result = _run(tmp_path, **{failure: True})
    assert result["status"] == 202
    assert result["payload"] == {"ok": True}
    expected = ["email", "github"] if failure == "github_exception" else ["email"]
    assert _transports(result) == expected
    assert _run(tmp_path)["calls"] == []


def test_failed_email_can_retry_the_same_report_id_then_suppress_duplicates(tmp_path):
    failed = _run(tmp_path, mail_result=False)
    assert failed["status"] == 500
    assert failed["payload"] == {"ok": False, "error": "Could not send email"}
    assert _transports(failed) == ["email"]
    retried = _run(tmp_path)
    assert retried["status"] == 202
    assert retried["payload"] == {"ok": True}
    assert _transports(retried) == ["email", "github"]
    duplicate = _run(tmp_path)
    assert duplicate == {"status": 202, "payload": {"ok": True, "duplicate": True}, "calls": []}


def test_successful_duplicate_is_suppressed_across_receiver_processes(tmp_path):
    assert _transports(_run(tmp_path)) == ["email", "github"]
    duplicate = _run(tmp_path)
    assert duplicate == {"status": 202, "payload": {"ok": True, "duplicate": True}, "calls": []}


def _wait_for_path(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        assert time.monotonic() < deadline, f"PHP request did not reach {path.name}"
        time.sleep(0.01)


def test_concurrent_requests_wait_for_the_first_email_and_send_only_once(tmp_path):
    gate = tmp_path / "mail-gate"
    second_started = tmp_path / "second-request-started"
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_run, tmp_path, mail_gate=str(gate))
        try:
            _wait_for_path(gate.with_suffix(".started"))
            second = executor.submit(_run, tmp_path, request_started=str(second_started))
            _wait_for_path(second_started)
            # The second process has entered the receiver while the first is
            # inside its mail transport, so it must wait for a definite result.
            with pytest.raises(FutureTimeout):
                second.result(timeout=0.1)
        finally:
            gate.with_suffix(".release").write_text("release")
        delivered = first.result(timeout=10)
        duplicate = second.result(timeout=10)
    assert delivered["status"] == 202
    assert _transports(delivered) == ["email", "github"]
    assert duplicate == {"status": 202, "payload": {"ok": True, "duplicate": True}, "calls": []}


def test_unavailable_dedupe_storage_does_not_send_or_claim_a_duplicate(tmp_path):
    directory = tmp_path / "php-temp" / "aps-bug-report-dedupe"
    record = directory / ("seen-" + hashlib.sha256(REPORT_ID.encode()).hexdigest() + ".txt")
    # A directory cannot be opened as the record file, regardless of which user
    # runs the tests. A permissions-only simulation can be bypassed by root.
    record.mkdir(parents=True)
    result = _run(tmp_path)
    assert result == {"status": 500, "payload": {"ok": False, "error": "Server error"}, "calls": []}


@pytest.mark.parametrize("marker", ["legacy", "sent", "sending", "damaged", "partial", "expired"])
def test_existing_delivery_records_survive_receiver_restart(tmp_path, marker):
    directory = tmp_path / "php-temp" / "aps-bug-report-dedupe"
    directory.mkdir(parents=True)
    timestamp = int(time.time())
    state = {
        "legacy": str(timestamp),
        "sent": "sent:" + str(timestamp),
        "sending": "sending",
        "damaged": "unreadable delivery record",
        "partial": "sent:",
        "expired": str(timestamp - 8 * 24 * 3600),
    }[marker]
    path = directory / ("seen-" + hashlib.sha256(REPORT_ID.encode()).hexdigest() + ".txt")
    path.write_text(state)
    result = _run(tmp_path)
    if marker == "expired":
        assert result["status"] == 202
        assert result["payload"] == {"ok": True}
        assert _transports(result) == ["email", "github"]
    elif marker in {"legacy", "sent"}:
        assert result == {"status": 202, "payload": {"ok": True, "duplicate": True}, "calls": []}
    else:
        # An interrupted send needs reconciliation; repeating it could send the
        # same accepted email twice and falsely report a new successful send.
        assert result["status"] == 500
        assert result["payload"]["ok"] is False
        assert result["calls"] == []


@pytest.mark.parametrize("server,status", [
    ({"REQUEST_METHOD": "GET"}, 405),
    ({"HTTPS": "off"}, 403),
    ({"CONTENT_TYPE": "text/plain"}, 415),
    ({"HTTP_X_APS_SIGNATURE": ""}, 401),
    ({"HTTP_X_APS_SIGNATURE": "sha256=" + "0" * 64}, 401),
    ({"HTTP_X_APS_TIMESTAMP": "0"}, 401),
    ({"CONTENT_LENGTH": str(768 * 1024 + 1)}, 413),
])
def test_invalid_request_never_reaches_either_transport(tmp_path, server, status):
    result = _run(tmp_path, server=server)
    assert result["status"] == status
    assert result["payload"]["ok"] is False
    assert result["calls"] == []


@pytest.mark.parametrize("raw,status", [
    ("", 400),
    ("invalid JSON", 400),
    ("null", 400),
    ("x" * (768 * 1024 + 1), 413),
], ids=["empty", "malformed-json", "null", "oversized"])
def test_empty_malformed_and_oversized_bodies_are_rejected(tmp_path, raw, status):
    # Understating Content-Length must not bypass the actual body-size limit.
    result = _run(tmp_path, raw=raw, server={"CONTENT_LENGTH": "1"})
    assert result["status"] == status
    assert result["payload"]["ok"] is False
    assert result["calls"] == []


@pytest.mark.parametrize("changes,status", [
    ({"report_id": ""}, 422),
    ({"created_at": "not a timestamp"}, 422),
    ({"summary": ""}, 422),
    ({"summary": "x" * 301}, 422),
    ({"description": "x" * 12001}, 422),
    ({"app": "invalid"}, 422),
    ({"app": {"name": "Another app", "version": "1.0.0"}}, 403),
    ({"logs": "invalid"}, 422),
])
def test_invalid_report_never_reaches_either_transport(tmp_path, changes, status):
    result = _run(tmp_path, report=_report(**changes))
    assert result["status"] == status
    assert result["payload"]["ok"] is False
    assert result["calls"] == []


@pytest.mark.parametrize("secret", [None, "", "CHANGE_THIS_TO_A_LONG_RANDOM_SHARED_SECRET"])
def test_missing_shared_secret_fails_closed_before_delivery(tmp_path, secret):
    result = _run(tmp_path, secret=secret)
    assert result["status"] == 500
    assert result["payload"] == {"ok": False, "error": "Server not configured"}
    assert result["calls"] == []
