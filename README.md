# Mobile GUI-VLA Human Demo Collector

This repository is a **private evaluation preview** of the host-side Mobile
GUI-VLA human-demonstration collector. Version `0.1.0` is intended for a small,
trusted group to evaluate collection usability on benign disposable Android
tasks. It is not a public release, a training dataset, or a general GUI-agent
framework.

No license is granted or implied. Redistribution, public publication, and the
choice of an eventual license require a separate decision by the repository
owner.

## Included scope

- Local or explicitly trusted-LAN browser collector.
- Tap, Swipe, Type, Back, Home, and Wait actions.
- Strict browser-to-original-frame coordinate mapping and stale-frame guards.
- Platform-owned screenshots, action validation, and immutable trajectory
  recording.
- Prepare, Record, and Review workflow guidance.
- Data Lab annotations, intervention provenance, reload QA, manifests, and a
  deterministic model-neutral export.
- Per-device active-session locking and bounded stable-frame capture.

The exact Platform package needed by the collector is bundled under `src/`.
Cloning a separate private Platform repository is not required.

This preview does **not** include live Baseline inference, DaModel access, GPU
operations, natural model intervention, an Android companion application, a
device farm, authentication, or a completed physical-phone workflow.

## Requirements

- Python 3.10 or newer.
- Android SDK Platform Tools with a working `adb` executable.
- One benign, disposable Android emulator or sanitized test device that is
  already reachable through the selected ADB server.
- A Chromium- or Firefox-compatible desktop browser.

Do not use a personal phone or an account containing credentials, payment
information, private messages, personal photos, authentication codes, or other
sensitive data.

## Install from a fresh clone

```bash
git clone https://github.com/chbq/mobile-gui-vla-human-demo-collector.git
cd mobile-gui-vla-human-demo-collector
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
gui-vla-data-lab --help
```

The installation contains both `mobile_gui_vla_data_lab` and the pinned
`mobile_gui_vla_platform` package.

## Start a loopback collector

Choose a private artifact directory outside the clone. Replace placeholders;
never commit the artifact directory or a real ADB serial.

```bash
export COLLECTOR_DATA_ROOT=/private/path/outside/the/clone

gui-vla-data-lab serve \
  --artifact-root "$COLLECTOR_DATA_ROOT" \
  --device test-device=ADB_SERIAL \
  --adb /path/to/platform-tools/adb \
  --platform-base 0eb713a412ff97a66f282dbb36c09130b8b8897f \
  --platform-dependency 4f8f08482391ff9da004742a5199af8936160ee0 \
  --host 127.0.0.1 \
  --port 8765
```

Open `http://127.0.0.1:8765/` in a browser on the same computer. The alias on
the left side of `--device ALIAS=ADB_SERIAL` is what appears in collection
metadata; use a non-identifying alias.

When an existing ADB server must be selected explicitly, pass
`--adb-server-socket ADB_SERVER_SOCKET`. Do not run global ADB reset commands
on a shared host.

## Trusted-LAN access

The server has **no authentication or transport encryption**. Loopback is the
safe default. To serve a collector to a different computer on a trusted,
isolated LAN, explicitly replace `127.0.0.1` with `LAN_BIND_ADDRESS`, restrict
access with host/network controls, and stop the service after the bounded
session. Never expose it to the public internet, a guest network, or another
untrusted network.

## Collection workflow

1. **Prepare:** select the device, use a pseudonymous collector ID, define a
   precise benign task, and move the device to the intended start state. These
   preparation actions are not recorded.
2. **Record:** start the trajectory, perform only the actions required by the
   instruction, and stop as soon as the goal is reached.
3. **Review:** freeze input, inspect the final screen, choose an outcome, and
   mark any sensitive trajectory for quarantine.

Use disposable tasks and pseudonymous collector IDs. Do not enter real secrets
in the Type field. Store the mapping from pseudonyms to people outside this
repository, if a study requires such a mapping.

All P0 scripted fixtures are schema/UX checks and are forced to
`training_eligible=false`. This preview has only one accepted real-human P1
candidate; the planned 2–3 collectors and 20–30 accepted trajectories are not
complete. No training manifest is included or claimed.

## QA and model-neutral export

```bash
gui-vla-data-lab qa --artifact-root "$COLLECTOR_DATA_ROOT"

gui-vla-data-lab manifest \
  --artifact-root "$COLLECTOR_DATA_ROOT" \
  --dataset-version private-evaluation-v0.1

gui-vla-data-lab export \
  --manifest "$COLLECTOR_DATA_ROOT/manifests/private-evaluation-v0.1.json" \
  --output "$COLLECTOR_DATA_ROOT/exports/private-evaluation-v0.1.jsonl"
```

Keep every artifact outside Git. A default manifest selects only records that
pass the implemented eligibility gates. Do not treat structural QA as a
substitute for human semantic review.

## Development checks

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s platform_tests -v
python -m compileall -q src tests platform_tests
git diff --check
```

See [PRIVACY.md](PRIVACY.md) before collecting data and
[RELEASE_PROVENANCE.md](RELEASE_PROVENANCE.md) for the exact source closure.
