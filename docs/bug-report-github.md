# Forward bug reports to GitHub and email

The [report receiver](../server/bug-report.php) can call an optional
[PHP helper](../server/github_bug_reports.php) to create a GitHub issue stub
alongside the existing support email. Each stub contains the report ID and app
version, which identify the full report in email. It never copies the summary,
description, contact details, environment, paths, diagnostics, or logs. This
applies even when the destination repository is private. Publishing report text
would require a separate, explicit submitter choice and is not implemented.

**The receiver integration is implemented; server deployment and configuration
are still required.** The supplied receiver source is included, with its signing
value read from the server environment. No live deployment or delivery has been
verified here. The desktop endpoint and reporting UI are unchanged.

## Server setup

The receiver needs PHP 7.4 or newer and the host's existing `mail()` setup. GitHub
forwarding additionally needs the cURL extension and outbound HTTPS access to
`api.github.com`. Configure these variables in the PHP service environment, never
in the distributed app, repository, or public PHP response:

| Variable | Value |
| --- | --- |
| `APS_BUG_REPORT_SECRET` | The receiver's existing compatible HMAC signing value; required with HMAC enabled |
| `APS_REPORT_GITHUB_ENABLED` | `1` to enable; any other value disables forwarding |
| `APS_REPORT_GITHUB_REPOSITORY` | The intended `OWNER/REPOSITORY` |
| `APS_REPORT_GITHUB_TOKEN` | A server-held fine-grained token restricted to that repository |
| `APS_REPORT_GITHUB_STATE_DIR` | An existing writable directory outside the public document root |
| `APS_REPORT_GITHUB_HELPER_PATH` | Optional absolute helper path; defaults to `github_bug_reports.php` beside the receiver |

Deploy the receiver to the existing report URL. Deploy the helper beside it, or
set `APS_REPORT_GITHUB_HELPER_PATH` to its private installation path. The helper
alone does not accept requests. Keep credentials in server configuration.
An absent, empty, or placeholder HMAC value produces `Server not configured`;
set `APS_BUG_REPORT_SECRET` before switching the receiver into service. Preserve
the value compatible with existing clients. This public client compatibility
value is not a GitHub credential or proof of an authorized installation.

Enable issues on the repository and give the token **Issues: write** permission.
The helper uses GitHub's `POST /repos/{owner}/{repo}/issues` API and accepts `201`
as creation success. The API version is pinned to `2026-03-10`.
See [GitHub's create-issue documentation](https://docs.github.com/en/rest/issues/issues#create-an-issue).

Create the GitHub state directory with owner-only access for the PHP service
account (for example, mode `0700`). Files contain only forwarding status and, on
success, the issue number. Their names hash the repository and report ID. Keep
this directory across deployments; deleting it removes duplicate-attempt
protection. All PHP instances handling a repository must share a filesystem with
reliable exclusive creation and locking. Separate state directories do not
coordinate duplicate protection.

## Email and GitHub delivery

The receiver retains the supplied request validation, request-size limits,
per-IP rate limit, HMAC checks, email recipient, and JSON responses. Its existing
private email includes the full report ID. The GitHub hook runs only after
`mail()` accepts the email and the receiver records that success. A failed email
returns the original error and does not invoke GitHub. Email acceptance means
the local mail system accepted it; final inbox delivery depends on that system.

A GitHub timeout, authorization failure, rate limit, disabled issue tracker,
missing configuration/helper, or unavailable cURL leaves the successful email
response unchanged: HTTP 202 with `{"ok":true}`. The helper skips feedback
(`kind: feedback`) and malformed report IDs. Unknown or malformed app versions
become `unknown`. Server logs contain only a generic optional-forwarding status.

The client's bundled HMAC token is public and cannot authorize issue creation.
Retain the receiver's request/rate limits and existing server abuse controls.
Forwarded HTTPS headers are trustworthy only when set by a trusted proxy; keep
the existing host's proxy configuration when deploying this source.

The optional HTTP operation has a four-second total timeout and does not follow
redirects. The receiver calls it inline; ensure the endpoint's email operation
plus this timeout stays within the desktop client's 20-second request timeout.
This integration does not introduce a worker or durable email queue.

## Email retries and duplicate protection

The receiver locks each report's existing `seen-<SHA256(report_id)>.txt` file in
the `aps-bug-report-dedupe` directory under PHP's system temporary directory.
It writes a `sending` reservation, attempts email, then writes `sent:<timestamp>`
only when `mail()` succeeds. An explicit email failure clears the reservation,
allowing the same report to be retried. Concurrent requests for the same ID
wait for this lock; a completed send returns the original HTTP 202 duplicate
response without sending another email or issue.

Storage/locking failures produce an explicit server error. A leftover `sending`
or malformed marker also produces an error: a crash or uncertain send could have
accepted email without recording success. Reconcile that report ID with mail
logs/inbox before clearing or correcting its marker while no request holds it.
The receiver does not silently resend an uncertain delivery.

Existing plain numeric markers remain recognized as successful deliveries for
their original seven-day TTL. The old receiver recorded these before sending,
so older failed deliveries cannot be identified from those markers alone; check
mail logs before correcting one. TTL checks now happen while the per-report
lock is held. Lock files remain in place to avoid racing another request on a
new inode. Clean up expired files only while receiver workers are stopped.
As before, a nonpositive `APS_BUG_REPORT_DEDUPE_TTL_SECONDS` disables deduplication.

## GitHub failures and retries

Before posting an issue, the helper exclusively reserves a separate journal
file for the report ID and repository. Repeated helper calls with that ID return
`already_attempted`. A newly submitted desktop report gets a new ID. A crash,
timeout, or error keeps the reservation: GitHub may have created an issue even
when its reply was lost. There are no automatic retries.

Check the support email and search destination issues for the full report ID
before a manual retry. Remove only that report's GitHub journal file once you
have confirmed no issue exists, then invoke the GitHub helper on the accepted
report through server administration. Reposting to the public receiver returns
the successful-email duplicate response and does not retry GitHub. If an issue
exists, use it. A process interrupted after recording email success but before
calling the helper likewise requires manual forwarding. This policy avoids
duplicate notifications; it does not guarantee every email has an issue.

To disable forwarding, unset `APS_REPORT_GITHUB_ENABLED` or change it from `1`.
Email reporting continues through the receiver.

## Offline checks

```bash
php -l server/bug-report.php
php -l server/github_bug_reports.php
python -m pytest -q tests/test_bug_report_receiver.py tests/test_bug_report_github.py
```

Tests inject email and HTTP transports and use temporary state directories.
They verify request validation, delivery ordering, privacy, retries, duplicate
suppression, and failure handling without credentials or network requests. They
skip if PHP CLI is unavailable. `APS_BUG_REPORT_LIBRARY_ONLY` suppresses receiver
autorun for tests; it must not be enabled on the public endpoint. Deployment and
real mail/GitHub delivery remain separate checks against the actual host.
