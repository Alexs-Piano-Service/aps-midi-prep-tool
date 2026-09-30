# Security Policy

APS MIDI Prep Tool is a desktop utility for working with MIDI files, Yamaha
E-SEQ files, floppy images, and physical floppy disks. Please report security
issues, suspicious behavior, malware false positives, or destructive data-loss
bugs as soon as possible.

Third-party names in this project identify compatibility targets only. APS MIDI
Prep Tool is independent and is not affiliated with, sponsored by, or endorsed
by Yamaha, Disklavier, PianoSoft, Electone, Clavinova, PianoDisc, Nalbantov,
Greaseweazle, Akai, MPC, or other companies and products mentioned.

## How To Report

Use either of these channels:

- Open a GitHub issue in the project repository.
- Email Alex's Piano Service LLC at service@alexanderpeppe.com.

Please include:

- App version and operating system.
- Whether you were using local files, an image, a floppy drive, or Greaseweazle.
- Steps to reproduce the issue.
- Any relevant error text or screenshots.
- Whether a security product flagged the app, and the vendor/name of that product.

Do not upload copyrighted disk images, commercial MIDI libraries, proprietary
firmware, private customer data, or other sensitive files to a public issue.
If a sample is necessary, describe it first and coordinate privately.

## Scope

Security reports may include:

- Malware or antivirus false positives.
- Unintended modification or deletion of files.
- Unsafe handling of floppy devices, disk images, or temporary files.
- Vulnerabilities in bundled helper tools or release packaging.
- Crashes or parsing bugs caused by malformed MIDI, E-SEQ, or disk-image data.

## Bug-report and feedback endpoint

The reporting protocol's bundled `BUG_REPORT_PUBLIC_TOKEN` (formerly named
`BUG_REPORT_SECRET`) is public. Its timestamped HMAC is retained for compatibility
with the existing endpoint; it does not authenticate legitimate installations.
Replacing it with another distributed constant would not create a credential.
The legacy environment-variable names remain supported for compatibility.

The supplied [bug-report receiver](server/bug-report.php) is included with its
HMAC signing value read from `APS_BUG_REPORT_SECRET`; the live deployment and
feedback endpoint have not been audited here. The receiver retains request-size
and rate limits, validation, and the legacy signature checks. Missing signing
configuration fails explicitly. Client signatures alone do not authorize
privileged actions or replace server abuse controls. Do not use the bundled
token to protect any other service; retire that trust if it exists.

Optional [GitHub forwarding](docs/bug-report-github.md) runs after successful
email acceptance. It publishes only a report ID and app version while full
reports remain in private email. It is disabled by default and uses a separate
server-held GitHub token. GitHub failures do not change successful email
responses. Concurrent report retries share a lock; only accepted emails receive
a sent marker, and uncertain delivery state requires server-side reconciliation.

## Legal And Safety Notes

Use test copies when possible. Do not use this project to distribute copyrighted
music, commercial player-piano disks, proprietary software, or other material
you do not have the legal right to share. Trademarks and product names belong
to their respective owners.
