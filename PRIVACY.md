# Privacy and Data Handling

This private preview controls an Android endpoint and can capture every visible
pixel after an action. Treat screenshots, trajectory steps, annotations, QA
records, manifests, exports, and lock metadata as private study data.

## Before a session

- Use a disposable emulator or a sanitized test device.
- Use only benign tasks whose state can be safely reset.
- Disable or remove personal accounts, notifications, messages, photos,
  authentication applications, payment applications, and unrelated data.
- Choose a pseudonymous collector ID. Keep any identity mapping in a separate,
  access-controlled location.
- Choose an artifact root outside the repository and restrict its filesystem
  permissions.
- Confirm that the collector binds to loopback unless a bounded trusted-LAN
  session is explicitly required.

## Prohibited collection

Do not intentionally collect credentials, authentication codes, payment data,
private messages, personal photos, health records, precise personal location,
unauthorized personal data, destructive account changes, permission grants, or
irreversible settings changes. Do not type real secrets into the collector.

If sensitive content appears, stop the session, mark the trajectory for
quarantine, restrict access to its artifact directory, and exclude it from
manifests. Do not rewrite or silently delete immutable raw evidence as a way to
hide a privacy incident; follow the study's retention and incident procedure.

## Network boundary

The collector HTTP server has no authentication and no TLS. Its default
loopback bind is deliberate. A trusted-LAN bind must be temporary, explicitly
chosen, and protected by host/network access controls. Never bind this preview
to an untrusted network or expose it through a public tunnel.

## Repository boundary

This repository must contain code, controlled task fixtures, documentation,
and release integrity metadata only. Never commit or attach raw frames,
trajectories, annotations, QA outputs, manifests derived from human sessions,
exports, lock files, device identifiers, collector identity mappings, logs, UI
dumps, recordings, credentials, or model artifacts.

P0 scripted fixtures are never training eligible. Real-human records require
both structural QA and semantic review before any later training-manifest
decision.
