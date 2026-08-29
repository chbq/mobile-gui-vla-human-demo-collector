# Release Notes — v0.1.0

`v0.1.0` is a private evaluation prerelease of the Mobile GUI-VLA human-demo
collector.

## Highlights

- Desktop browser workspace with bilingual guidance and explicit Prepare,
  Record, and Review phases.
- Exact displayed-preview binding and strict mapping into original Frame
  pixels for Tap and Swipe.
- Type, Back, Home, and Wait controls.
- Responsive asynchronous action feedback while stable raw evidence closes.
- Per-device active-session lock, intervention provenance, privacy annotation,
  reload QA, deterministic manifest selection, and model-neutral export.
- Bundled pinned Platform dependency; no separate private dependency checkout
  is needed by evaluators.

## Candidate validation

The release is published only after the following candidate checks pass:

- fresh isolated installation/build;
- 29 Data Lab tests;
- focused bundled-Platform Type/Home and mocked ADB dispatch tests;
- Python compile, embedded JavaScript syntax, and Git whitespace checks;
- CLI help and loopback fixture browser/API smoke without a real device;
- exact source-byte comparison and release-manifest closure;
- tracked-tree, prohibited-content, privacy, and secret-regex scans.

`gitleaks` is not available in the release environment, so a Gitleaks result is
explicitly `NOT_AVAILABLE`; this is not represented as a pass.

## Known limits

- The server has no authentication or TLS. Trusted-LAN use must be temporary
  and access-controlled.
- Full-resolution ADB screenshot throughput is not a video stream.
- Only one accepted real-human P1 candidate exists at release time. The target
  of 2–3 collectors and 20–30 accepted trajectories is not complete.
- No training manifest is included.
- Natural model intervention, live Baseline inference, DaModel/GPU operations,
  physical-phone completion, public release, redistribution terms,
  collaborator access, and data sharing are not part of this release.
- Evaluators must provide their own sanitized Android endpoint and private
  artifact storage.
