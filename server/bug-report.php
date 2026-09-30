<?php
/**
 * APS Bug Report Receiver
 *
 * Save as something like /var/www/html/bug-report.php and post JSON to it.
 * Requires PHP 7.4+ and normal PHP mail() support. Optional GitHub forwarding
 * additionally needs cURL; see docs/bug-report-github.md.
 */

declare(strict_types=1);

/* ----------------------------- Configuration ----------------------------- */

// Set the existing compatible signing value in the Apache/PHP environment.
// This public client HMAC value is separate from the server's GitHub credential.
// Example Apache vhost line:
// SetEnv APS_BUG_REPORT_SECRET "existing-compatible-signing-value"
$secret_from_env = getenv('APS_BUG_REPORT_SECRET') ?: '';

defined('APS_BUG_REPORT_TO') || define('APS_BUG_REPORT_TO', 'service@alexanderpeppe.com');
defined('APS_BUG_REPORT_FROM') || define('APS_BUG_REPORT_FROM', 'bug-reports@alexanderpeppe.com');
defined('APS_BUG_REPORT_FROM_NAME') || define('APS_BUG_REPORT_FROM_NAME', 'APS Bug Reports');
defined('APS_BUG_REPORT_ENVELOPE_FROM') || define('APS_BUG_REPORT_ENVELOPE_FROM', APS_BUG_REPORT_FROM);
defined('APS_BUG_REPORT_SECRET') || define('APS_BUG_REPORT_SECRET', $secret_from_env ?: 'CHANGE_THIS_TO_A_LONG_RANDOM_SHARED_SECRET');

// Keep these true for a public endpoint.
defined('APS_BUG_REPORT_REQUIRE_HMAC') || define('APS_BUG_REPORT_REQUIRE_HMAC', true);
defined('APS_BUG_REPORT_REQUIRE_HTTPS') || define('APS_BUG_REPORT_REQUIRE_HTTPS', true);

// Comma-separated app names allowed to submit reports. Empty string allows any app name.
defined('APS_BUG_REPORT_ALLOWED_APP_NAMES') || define('APS_BUG_REPORT_ALLOWED_APP_NAMES', 'APS MIDI Prep Tool');

// Limits. Your sample log tail is 256 KiB, so the body limit leaves room for metadata.
defined('APS_BUG_REPORT_MAX_BODY_BYTES') || define('APS_BUG_REPORT_MAX_BODY_BYTES', 768 * 1024);
defined('APS_BUG_REPORT_MAX_SUMMARY_CHARS') || define('APS_BUG_REPORT_MAX_SUMMARY_CHARS', 300);
defined('APS_BUG_REPORT_MAX_DESCRIPTION_CHARS') || define('APS_BUG_REPORT_MAX_DESCRIPTION_CHARS', 12000);
defined('APS_BUG_REPORT_MAX_EMAIL_LOG_CHARS') || define('APS_BUG_REPORT_MAX_EMAIL_LOG_CHARS', 262144);

// Simple file-based protections. They use the system temp directory, not your web root.
defined('APS_BUG_REPORT_RATE_LIMIT_PER_HOUR_PER_IP') || define('APS_BUG_REPORT_RATE_LIMIT_PER_HOUR_PER_IP', 25);
defined('APS_BUG_REPORT_HMAC_MAX_SKEW_SECONDS') || define('APS_BUG_REPORT_HMAC_MAX_SKEW_SECONDS', 10 * 60);
defined('APS_BUG_REPORT_DEDUPE_TTL_SECONDS') || define('APS_BUG_REPORT_DEDUPE_TTL_SECONDS', 7 * 24 * 60 * 60);

/* ------------------------------ Entry point ------------------------------ */

if (!defined('APS_BUG_REPORT_LIBRARY_ONLY') || !APS_BUG_REPORT_LIBRARY_ONLY) {
    aps_bug_report_handle();
}

/* ------------------------------ Main logic ------------------------------- */

function aps_bug_report_handle(
    ?string $request_body = null,
    ?callable $send_email = null,
    ?callable $forward = null
): void
{
    try {
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
            aps_json_response(405, ['ok' => false, 'error' => 'POST required']);
        }

        if (APS_BUG_REPORT_REQUIRE_HTTPS && !aps_request_is_https()) {
            aps_json_response(403, ['ok' => false, 'error' => 'HTTPS required']);
        }

        aps_check_rate_limit(aps_remote_ip());

        $content_type = strtolower(trim((string)($_SERVER['CONTENT_TYPE'] ?? '')));
        $content_type = explode(';', $content_type, 2)[0];

        if ($content_type !== 'application/json') {
            aps_json_response(415, ['ok' => false, 'error' => 'Content-Type must be application/json']);
        }

        $raw = aps_read_limited_body((int)APS_BUG_REPORT_MAX_BODY_BYTES, $request_body);

        if (APS_BUG_REPORT_REQUIRE_HMAC) {
            aps_verify_hmac($raw);
        }

        $data = json_decode($raw, true);

        if (!is_array($data) || json_last_error() !== JSON_ERROR_NONE) {
            aps_json_response(400, ['ok' => false, 'error' => 'Invalid JSON']);
        }

        aps_validate_report($data);

        $result = aps_deliver_report($data, $send_email, $forward);
        aps_json_response($result['status'], $result['payload']);
    } catch (Throwable $e) {
        error_log('Bug report receiver error: ' . $e->getMessage());
        aps_json_response(500, ['ok' => false, 'error' => 'Server error']);
    }
}

/** Deliver a validated report. Optional transports allow offline regression tests. */
function aps_deliver_report(array $data, ?callable $send_email = null, ?callable $forward = null): array
{
    $reservation = aps_reserve_dedupe(aps_scalar_string($data['report_id'] ?? ''));
    $lock = $reservation['handle'];
    try {
        if ($reservation['duplicate']) {
            return ['status' => 202, 'payload' => ['ok' => true, 'duplicate' => true]];
        }

        $subject = aps_build_subject($data);
        $body = aps_build_email_body($data);
        // Reserve state before mail. An interrupted/uncertain send must
        // be reconciled, not silently repeated after the process releases flock.
        aps_write_dedupe($lock, 'sending');
        $send_email = $send_email ?? 'aps_send_email';
        if (!$send_email(APS_BUG_REPORT_TO, $subject, $body, aps_contact_reply_to($data))) {
            aps_write_dedupe($lock, '');
            return ['status' => 500, 'payload' => ['ok' => false, 'error' => 'Could not send email']];
        }
        aps_write_dedupe($lock, 'sent:' . time());
    } finally {
        if (is_resource($lock)) {
            flock($lock, LOCK_UN);
            fclose($lock);
        }
    }

    // Optional GitHub failure must never change the accepted-email response.
    try {
        $forward = $forward ?? 'aps_forward_report_to_github';
        $forward($data);
    } catch (Throwable $error) {
        error_log('APS optional GitHub forwarding unavailable');
    }
    return ['status' => 202, 'payload' => ['ok' => true]];
}

function aps_forward_report_to_github(array $data): void
{
    if (getenv('APS_REPORT_GITHUB_ENABLED') !== '1') {
        return;
    }
    $helper = getenv('APS_REPORT_GITHUB_HELPER_PATH') ?: __DIR__ . '/github_bug_reports.php';
    @require_once $helper;
    $github = \APSMidiPrepReports\githubForwardBugReport($data);
    if (!in_array($github['status'], ['disabled', 'skipped', 'created', 'already_attempted'], true)) {
        error_log('APS optional GitHub forwarding: ' . $github['status']);
    }
}

/* ----------------------------- Validation -------------------------------- */

function aps_validate_report(array $data): void
{
    $report_id = aps_scalar_string($data['report_id'] ?? '');
    if ($report_id === '' || strlen($report_id) > 128) {
        aps_json_response(422, ['ok' => false, 'error' => 'Invalid report_id']);
    }

    $created_at = aps_scalar_string($data['created_at'] ?? '');
    if ($created_at === '' || strtotime($created_at) === false) {
        aps_json_response(422, ['ok' => false, 'error' => 'Invalid created_at']);
    }

    $summary = aps_scalar_string($data['summary'] ?? '');
    if ($summary === '' || strlen($summary) > (int)APS_BUG_REPORT_MAX_SUMMARY_CHARS) {
        aps_json_response(422, ['ok' => false, 'error' => 'Invalid summary']);
    }

    $description = aps_scalar_string($data['description'] ?? '');
    if (strlen($description) > (int)APS_BUG_REPORT_MAX_DESCRIPTION_CHARS) {
        aps_json_response(422, ['ok' => false, 'error' => 'Description too long']);
    }

    if (!isset($data['app']) || !is_array($data['app'])) {
        aps_json_response(422, ['ok' => false, 'error' => 'Invalid app block']);
    }

    $app_name = aps_scalar_string($data['app']['name'] ?? '');
    $app_version = aps_scalar_string($data['app']['version'] ?? '');

    if ($app_name === '' || strlen($app_name) > 100 || strlen($app_version) > 50) {
        aps_json_response(422, ['ok' => false, 'error' => 'Invalid app name/version']);
    }

    $allowed = array_values(array_filter(array_map('trim', explode(',', (string)APS_BUG_REPORT_ALLOWED_APP_NAMES))));

    if ($allowed && !in_array($app_name, $allowed, true)) {
        aps_json_response(403, ['ok' => false, 'error' => 'App not allowed']);
    }

    if (isset($data['logs']) && !is_array($data['logs'])) {
        aps_json_response(422, ['ok' => false, 'error' => 'Invalid logs block']);
    }
}

/* ------------------------------- Security -------------------------------- */

function aps_verify_hmac(string $raw): void
{
    if (trim((string)APS_BUG_REPORT_SECRET) === ''
        || trim((string)APS_BUG_REPORT_SECRET) === 'CHANGE_THIS_TO_A_LONG_RANDOM_SHARED_SECRET') {
        aps_json_response(500, ['ok' => false, 'error' => 'Server not configured']);
    }

    $timestamp = aps_header('X-APS-Timestamp');
    $signature = aps_header('X-APS-Signature');

    if ($timestamp === '' || $signature === '') {
        aps_json_response(401, ['ok' => false, 'error' => 'Missing signature']);
    }

    if (!ctype_digit($timestamp)) {
        aps_json_response(401, ['ok' => false, 'error' => 'Invalid signature timestamp']);
    }

    $ts = (int)$timestamp;

    if (abs(time() - $ts) > (int)APS_BUG_REPORT_HMAC_MAX_SKEW_SECONDS) {
        aps_json_response(401, ['ok' => false, 'error' => 'Signature timestamp expired']);
    }

    $signature = trim($signature);

    if (stripos($signature, 'sha256=') === 0) {
        $signature = substr($signature, 7);
    }

    if (!preg_match('/^[a-f0-9]{64}$/i', $signature)) {
        aps_json_response(401, ['ok' => false, 'error' => 'Invalid signature format']);
    }

    $expected = hash_hmac('sha256', $timestamp . '.' . $raw, (string)APS_BUG_REPORT_SECRET);

    if (!hash_equals(strtolower($expected), strtolower($signature))) {
        aps_json_response(401, ['ok' => false, 'error' => 'Invalid signature']);
    }
}

function aps_check_rate_limit(string $ip): void
{
    $limit = (int)APS_BUG_REPORT_RATE_LIMIT_PER_HOUR_PER_IP;

    if ($limit <= 0) {
        return;
    }

    $dir = aps_private_temp_dir('aps-bug-report-rate-limit');
    aps_cleanup_old_files($dir, 2 * 24 * 60 * 60);

    $window = (string)floor(time() / 3600);
    $file = $dir . '/rl-' . hash('sha256', $ip . '|' . $window) . '.txt';

    $fp = @fopen($file, 'c+');

    if (!$fp) {
        aps_json_response(500, ['ok' => false, 'error' => 'Rate limit unavailable']);
    }

    try {
        if (!flock($fp, LOCK_EX)) {
            aps_json_response(500, ['ok' => false, 'error' => 'Rate limit unavailable']);
        }

        rewind($fp);
        $current = trim(stream_get_contents($fp));
        $count = ctype_digit($current) ? (int)$current : 0;

        if ($count >= $limit) {
            flock($fp, LOCK_UN);
            fclose($fp);
            aps_json_response(429, ['ok' => false, 'error' => 'Rate limit exceeded']);
        }

        ftruncate($fp, 0);
        rewind($fp);
        fwrite($fp, (string)($count + 1));
        fflush($fp);
        flock($fp, LOCK_UN);
    } finally {
        if (is_resource($fp)) {
            fclose($fp);
        }
    }
}

/** Return a locked reservation; callers must release its handle in finally. */
function aps_reserve_dedupe(string $report_id): array
{
    if (APS_BUG_REPORT_DEDUPE_TTL_SECONDS <= 0) {
        return ['handle' => null, 'duplicate' => false];
    }

    $dir = aps_private_temp_dir('aps-bug-report-dedupe');
    $file = $dir . '/seen-' . hash('sha256', $report_id) . '.txt';
    // Never unlink these lock files during request handling: deleting a locked
    // inode would allow another request to create a separate lock for this ID.
    $fp = @fopen($file, 'c+');
    if (!$fp) {
        throw new RuntimeException('Report deduplication storage unavailable');
    }
    try {
        if (!flock($fp, LOCK_EX) || !rewind($fp)) {
            throw new RuntimeException('Report deduplication lock unavailable');
        }
        $state = stream_get_contents($fp);
        if ($state === false) {
            throw new RuntimeException('Report deduplication state unavailable');
        }
        $state = trim($state);
        if (strpos($state, 'sent:') === 0) {
            $state = substr($state, 5);
            if ($state === '') {
                throw new RuntimeException('Report delivery status requires reconciliation');
            }
        }
        // Numeric markers from the original receiver are also treated as sent.
        if ($state !== '' && !ctype_digit($state)) {
            throw new RuntimeException('Report delivery status requires reconciliation');
        }
        $duplicate = $state !== '' && (time() - (int)$state) < (int)APS_BUG_REPORT_DEDUPE_TTL_SECONDS;
        return ['handle' => $fp, 'duplicate' => $duplicate];
    } catch (Throwable $error) {
        flock($fp, LOCK_UN);
        fclose($fp);
        throw $error;
    }
}

/** Update the locked state, reporting storage failures instead of duplicates. */
function aps_write_dedupe($fp, string $state): void
{
    if ($fp === null) {
        return; // Explicitly disabled through the original TTL setting.
    }
    if (!rewind($fp)
        || ($state !== '' && fwrite($fp, $state) !== strlen($state))
        || !ftruncate($fp, strlen($state)) || !fflush($fp)) {
        throw new RuntimeException('Could not store report delivery status');
    }
}

/* ------------------------------- Email ----------------------------------- */

function aps_build_subject(array $data): string
{
    $app_name = aps_scalar_string($data['app']['name'] ?? 'App');
    $version = aps_scalar_string($data['app']['version'] ?? '');
    $summary = aps_scalar_string($data['summary'] ?? 'Bug report');
    $report_id = aps_scalar_string($data['report_id'] ?? '');

    $prefix = '[Bug Report] ' . $app_name;

    if ($version !== '') {
        $prefix .= ' ' . $version;
    }

    $subject = $prefix . ': ' . $summary;

    if ($report_id !== '') {
        $subject .= ' #' . substr($report_id, 0, 8);
    }

    return aps_header_value(aps_truncate($subject, 180));
}

function aps_build_email_body(array $data): string
{
    $logs = isset($data['logs']) && is_array($data['logs']) ? $data['logs'] : [];

    $log_text = aps_scalar_string($logs['text'] ?? '');
    $log_text = aps_redact_text($log_text);
    $log_text = aps_tail($log_text, (int)APS_BUG_REPORT_MAX_EMAIL_LOG_CHARS);

    $body = [];

    $body[] = 'APS bug report received';
    $body[] = 'Received at: ' . gmdate('c') . ' UTC';
    $body[] = 'Remote IP: ' . aps_remote_ip();
    $body[] = '';

    $body[] = 'Report ID: ' . aps_clean_text(aps_scalar_string($data['report_id'] ?? ''));
    $body[] = 'Created at: ' . aps_clean_text(aps_scalar_string($data['created_at'] ?? ''));
    $body[] = 'Contact: ' . aps_clean_text(aps_scalar_string($data['contact'] ?? ''));
    $body[] = '';

    $body[] = 'Summary';
    $body[] = '-------';
    $body[] = aps_clean_text(aps_scalar_string($data['summary'] ?? ''));
    $body[] = '';

    $body[] = 'Description';
    $body[] = '-----------';
    $body[] = aps_redact_text(aps_clean_text(aps_scalar_string($data['description'] ?? '')));
    $body[] = '';

    $body[] = 'App';
    $body[] = '---';
    $body[] = aps_pretty_json($data['app'] ?? []);
    $body[] = '';

    $body[] = 'Environment';
    $body[] = '-----------';
    $body[] = aps_pretty_json($data['environment'] ?? []);
    $body[] = '';

    $body[] = 'Context';
    $body[] = '-------';
    $body[] = aps_pretty_json($data['context'] ?? []);
    $body[] = '';

    $body[] = 'Log metadata';
    $body[] = '------------';
    $body[] = aps_pretty_json(array_diff_key($logs, ['text' => true]));
    $body[] = '';

    $body[] = 'Recent logs';
    $body[] = '-----------';
    $body[] = $log_text !== '' ? $log_text : '[No log text included]';
    $body[] = '';

    $body[] = 'Sanitized raw report, excluding full log text';
    $body[] = '-------------------------------------------';

    $raw_copy = $data;

    if (isset($raw_copy['logs']['text'])) {
        $raw_copy['logs']['text'] = '[log text omitted here; see Recent logs section]';
    }

    $body[] = aps_pretty_json($raw_copy);

    return implode("\n", $body) . "\n";
}

function aps_send_email(string $to, string $subject, string $body, string $reply_to = ''): bool
{
    if (!filter_var($to, FILTER_VALIDATE_EMAIL) || !filter_var(APS_BUG_REPORT_FROM, FILTER_VALIDATE_EMAIL)) {
        error_log('Bug report receiver has invalid email configuration.');
        return false;
    }

    $from = aps_mailbox_header((string)APS_BUG_REPORT_FROM_NAME, (string)APS_BUG_REPORT_FROM);

    $headers = [];
    $headers[] = 'From: ' . $from;

    if ($reply_to !== '') {
        $headers[] = 'Reply-To: ' . aps_header_value($reply_to);
    }

    $headers[] = 'MIME-Version: 1.0';
    $headers[] = 'Content-Type: text/plain; charset=UTF-8';
    $headers[] = 'Content-Transfer-Encoding: 8bit';
    $headers[] = 'X-Auto-Response-Suppress: All';
    $headers[] = 'Auto-Submitted: auto-generated';

    $encoded_subject = function_exists('mb_encode_mimeheader')
        ? mb_encode_mimeheader($subject, 'UTF-8', 'B', "\r\n")
        : $subject;

    $headers_string = implode("\r\n", $headers);

    if (defined('APS_BUG_REPORT_ENVELOPE_FROM') && APS_BUG_REPORT_ENVELOPE_FROM !== '') {
        if (!filter_var(APS_BUG_REPORT_ENVELOPE_FROM, FILTER_VALIDATE_EMAIL)) {
            error_log('Bug report receiver has invalid envelope sender.');
            return false;
        }

        return @mail(
            $to,
            $encoded_subject,
            $body,
            $headers_string,
            '-f' . escapeshellarg((string)APS_BUG_REPORT_ENVELOPE_FROM)
        );
    }

    return @mail($to, $encoded_subject, $body, $headers_string);
}

function aps_contact_reply_to(array $data): string
{
    $contact = trim(aps_scalar_string($data['contact'] ?? ''));

    return filter_var($contact, FILTER_VALIDATE_EMAIL) ? $contact : '';
}

/* ------------------------------- Helpers --------------------------------- */

function aps_request_is_https(): bool
{
    // Direct Apache/mod_php or normal HTTPS environment.
    if (!empty($_SERVER['HTTPS']) && strtolower((string)$_SERVER['HTTPS']) !== 'off') {
        return true;
    }

    // Common CGI/FastCGI variables.
    if (strtolower((string)($_SERVER['REQUEST_SCHEME'] ?? '')) === 'https') {
        return true;
    }

    if (strtolower((string)($_SERVER['SERVER_PROTOCOL'] ?? '')) === 'https') {
        return true;
    }

    if ((string)($_SERVER['SERVER_PORT'] ?? '') === '443') {
        return true;
    }

    // Apache/proxy-set headers. These are safe only if your Apache/Cloudflare setup
    // controls them. Do not rely on these on an untrusted plain HTTP endpoint.
    if (strtolower((string)($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '')) === 'https') {
        return true;
    }

    if (strtolower((string)($_SERVER['HTTP_X_FORWARDED_SSL'] ?? '')) === 'on') {
        return true;
    }

    if (strtolower((string)($_SERVER['HTTP_FRONT_END_HTTPS'] ?? '')) === 'on') {
        return true;
    }

    // Cloudflare sends CF-Visitor: {"scheme":"https"}
    $cf_visitor = (string)($_SERVER['HTTP_CF_VISITOR'] ?? '');
    if ($cf_visitor !== '') {
        $decoded = json_decode($cf_visitor, true);
        if (is_array($decoded) && strtolower((string)($decoded['scheme'] ?? '')) === 'https') {
            return true;
        }
    }

    return false;
}

function aps_header(string $name): string
{
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));

    return trim((string)($_SERVER[$key] ?? ''));
}

function aps_remote_ip(): string
{
    // Do not trust X-Forwarded-For on a direct public endpoint.
    return preg_replace('/[^a-fA-F0-9:.]/', '', (string)($_SERVER['REMOTE_ADDR'] ?? 'unknown')) ?: 'unknown';
}

function aps_read_limited_body(int $max_bytes, ?string $request_body = null): string
{
    $content_length = $_SERVER['CONTENT_LENGTH'] ?? null;

    if ($content_length !== null && ctype_digit((string)$content_length) && (int)$content_length > $max_bytes) {
        aps_json_response(413, ['ok' => false, 'error' => 'Request too large']);
    }

    $raw = $request_body ?? file_get_contents('php://input', false, null, 0, $max_bytes + 1);

    if ($raw === false || $raw === '') {
        aps_json_response(400, ['ok' => false, 'error' => 'Empty request body']);
    }

    if (strlen($raw) > $max_bytes) {
        aps_json_response(413, ['ok' => false, 'error' => 'Request too large']);
    }

    return $raw;
}

function aps_private_temp_dir(string $name): string
{
    $dir = rtrim(sys_get_temp_dir(), DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . $name;

    if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) {
        aps_json_response(500, ['ok' => false, 'error' => 'Temporary storage unavailable']);
    }

    return $dir;
}

function aps_cleanup_old_files(string $dir, int $ttl_seconds): void
{
    if ($ttl_seconds <= 0 || mt_rand(1, 100) !== 1) {
        return;
    }

    $cutoff = time() - $ttl_seconds;

    foreach (glob($dir . '/*.txt') ?: [] as $file) {
        if (is_file($file) && filemtime($file) !== false && filemtime($file) < $cutoff) {
            @unlink($file);
        }
    }
}

function aps_pretty_json($value): string
{
    $json = json_encode($value, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);

    if ($json === false) {
        return '[Could not encode JSON]';
    }

    return aps_redact_text($json);
}

function aps_scalar_string($value): string
{
    if (is_string($value)) {
        return $value;
    }

    if (is_int($value) || is_float($value) || is_bool($value)) {
        return (string)$value;
    }

    return '';
}

function aps_clean_text(string $text): string
{
    // Keep tabs/newlines, remove other ASCII control characters.
    return preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/', '', $text) ?? '';
}

function aps_redact_text(string $text): string
{
    $text = aps_clean_text($text);

    $patterns = [
        '/(Authorization\s*:\s*)(Bearer|Basic)\s+[^\s]+/i' => '$1[redacted]',
        '/((?:api[_-]?key|token|secret|password|passwd|pwd|auth[_-]?token)\s*[=:]\s*)[^\s&"\']+/i' => '$1[redacted]',
        '/(https?:\/\/)([^\/\s:@]+):([^\/\s@]+)@/i' => '$1[redacted]@',
        '/\b(sk_live_|sk_test_|rk_live_|rk_test_)[A-Za-z0-9_\-]+\b/' => '$1[redacted]',
        '/\bAC[a-f0-9]{32}\b/i' => '[twilio_sid_redacted]',
    ];

    foreach ($patterns as $pattern => $replacement) {
        $text = preg_replace($pattern, $replacement, $text) ?? $text;
    }

    return $text;
}

function aps_tail(string $text, int $max_chars): string
{
    if ($max_chars <= 0 || strlen($text) <= $max_chars) {
        return $text;
    }

    return "[Earlier log text truncated by server; showing last {$max_chars} bytes]\n" . substr($text, -$max_chars);
}

function aps_truncate(string $text, int $max_bytes): string
{
    if ($max_bytes <= 0 || strlen($text) <= $max_bytes) {
        return $text;
    }

    return substr($text, 0, $max_bytes - 3) . '...';
}

function aps_header_value(string $value): string
{
    return trim(str_replace(["\r", "\n"], ' ', $value));
}

function aps_mailbox_header(string $name, string $email): string
{
    $name = aps_header_value($name);
    $email = aps_header_value($email);

    if ($name === '') {
        return $email;
    }

    if (function_exists('mb_encode_mimeheader')) {
        $name = mb_encode_mimeheader($name, 'UTF-8', 'B', "\r\n");
    } else {
        $name = addcslashes($name, '"\\');
        $name = '"' . $name . '"';
    }

    return $name . ' <' . $email . '>';
}

function aps_json_response(int $status, array $payload): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=UTF-8');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($payload, JSON_UNESCAPED_SLASHES) ?: '{"ok":false}';
    exit;
}
