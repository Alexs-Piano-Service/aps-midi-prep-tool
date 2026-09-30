<?php
declare(strict_types=1);

namespace APSMidiPrepReports;

/** Optional hook for bug-report.php; never a standalone endpoint. */
function githubIssueStub(array $report): ?array
{
    if (isset($report['kind']) && $report['kind'] !== 'bug') {
        return null;
    }
    $id = $report['report_id'] ?? null;
    if (!is_string($id) || !preg_match('/\A[a-f0-9]{32}\z/D', $id)) {
        return null;
    }
    $app = $report['app'] ?? [];
    $version = is_array($app) ? ($app['version'] ?? null) : null;
    if (!is_string($version) || !preg_match('/\A\d{1,4}\.\d{1,4}\.\d{1,4}\z/D', $version)) {
        $version = 'unknown';
    }
    // An allowlist, not redaction: no user prose, contact, paths or logs.
    return [
        'title' => 'App bug report ' . $id,
        'body' => "A bug report was received by private support email.\n\n"
            . "Report ID: `$id`\nApp version: `$version`\n\n"
            . "The report details remain in the support email with this report ID. "
            . "Review that email before adding any information to this issue.",
    ];
}

/** Send only the allowlisted stub. No redirects, raw response logging or retries. */
function githubPostIssue(string $repository, string $token, array $issue): array
{
    $handle = curl_init('https://api.github.com/repos/' . $repository . '/issues');
    if ($handle === false) {
        throw new \RuntimeException('GitHub transport unavailable');
    }
    $body = '';
    try {
        if (!curl_setopt_array($handle, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => json_encode($issue, JSON_THROW_ON_ERROR),
            CURLOPT_HTTPHEADER => [
                'Accept: application/vnd.github+json',
                'Content-Type: application/json',
                'Authorization: Bearer ' . $token,
                'X-GitHub-Api-Version: 2026-03-10',
            ],
            CURLOPT_USERAGENT => 'APS-MIDI-Prep-Tool-Report-Server',
            CURLOPT_CONNECTTIMEOUT_MS => 1500,
            CURLOPT_TIMEOUT_MS => 4000,
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_PROTOCOLS => CURLPROTO_HTTPS,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
            CURLOPT_WRITEFUNCTION => static function ($unused, string $chunk) use (&$body): int {
                if (strlen($body) + strlen($chunk) > 65536) {
                    return 0;
                }
                $body .= $chunk;
                return strlen($chunk);
            },
        ])) {
            throw new \RuntimeException('GitHub transport configuration failed');
        }
        if (curl_exec($handle) === false) {
            throw new \RuntimeException('GitHub transport failed');
        }
        $data = json_decode($body, true);
        return [
            'http_status' => (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE),
            'number' => is_array($data) ? ($data['number'] ?? null) : null,
        ];
    } finally {
        curl_close($handle);
    }
}

/**
 * Call only AFTER the existing endpoint successfully queues/sends its email.
 * The return value is server-side bookkeeping; it must not change email success.
 * $post exists for offline tests. Production should omit it.
 */
function githubForwardBugReport(array $report, ?callable $post = null): array
{
    if (getenv('APS_REPORT_GITHUB_ENABLED') !== '1') {
        return ['status' => 'disabled'];
    }
    $journal = null;
    $reserved = false;
    try {
        $issue = githubIssueStub($report);
        if ($issue === null) {
            return ['status' => 'skipped'];
        }
        $repository = trim((string) getenv('APS_REPORT_GITHUB_REPOSITORY'));
        $token = trim((string) getenv('APS_REPORT_GITHUB_TOKEN'));
        $directory = (string) getenv('APS_REPORT_GITHUB_STATE_DIR');
        if (!preg_match('/\A[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}\z/D', $repository)
            || !preg_match('/\A[A-Za-z0-9_]+\z/D', $token)
            || $directory === '' || !is_dir($directory) || !is_writable($directory)
            || ($post === null && !function_exists('curl_init'))) {
            return ['status' => 'configuration_error'];
        }
        $journal = rtrim($directory, '/\\') . DIRECTORY_SEPARATOR
            . hash('sha256', strtolower($repository) . ':' . $report['report_id']) . '.json';
        // Exclusive creation reserves one attempt per report, including timeouts.
        // This avoids duplicate issues when GitHub accepted a POST but its reply
        // was lost. Operators can inspect the journal and reconcile with email.
        $file = @fopen($journal, 'x');
        if ($file === false) {
            return ['status' => file_exists($journal) ? 'already_attempted' : 'state_error'];
        }
        $reserved = true;
        try {
            $reservation = '{"status":"attempting"}';
            if (!@chmod($journal, 0600)
                || fwrite($file, $reservation) !== strlen($reservation)
                || !fflush($file)) {
                return ['status' => 'state_error'];
            }
        } finally {
            fclose($file);
        }
        $post = $post ?? __NAMESPACE__ . '\\githubPostIssue';
        $response = $post($repository, $token, $issue);
        $status = $response['http_status'] ?? 0;
        $number = $response['number'] ?? null;
        $result = $status === 201 && is_int($number) && $number > 0
            ? ['status' => 'created', 'number' => $number]
            : ['status' => 'failed'];
        @file_put_contents($journal, json_encode($result, JSON_THROW_ON_ERROR), LOCK_EX);
        return $result;
    } catch (\Throwable $error) {
        // Never expose request bodies, tokens, exception text or GitHub responses.
        if ($reserved) {
            @file_put_contents($journal, '{"status":"failed"}', LOCK_EX);
        }
        return ['status' => 'failed'];
    }
}
