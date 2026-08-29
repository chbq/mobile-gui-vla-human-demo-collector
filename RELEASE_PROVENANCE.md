# Release Provenance

## Version

- Package version: `0.1.0`
- Git tag: `v0.1.0`
- Release channel: public evaluation prerelease
- History policy: one sanitized root commit; private task history is excluded

## Exact sources

- Data Lab source commit:
  `0a35cb751e24785a06a5ebfc41892a8e40fc9b20`
- Data Lab main anchor:
  `a8032b1eb5fa5630e3b12b0b6a4cf32cb1696c7e`
- Data Lab workflow commit:
  `592dcfc8771effc6516f218847b9e46c7e78ea18`
- Data Lab workflow merge:
  `973e5ffef6c102a3ad263f15bd7be9b23bb38a73`
- Platform dependency commit:
  `4f8f08482391ff9da004742a5199af8936160ee0`
- Pinned Platform base:
  `0eb713a412ff97a66f282dbb36c09130b8b8897f`

The dependency commit is a direct child of the pinned Platform base and adds
the minimal TypeAction/HomeAction contracts, validation, and ADB dispatch
required for human collection.

## Export allowlist

Exact Git blobs exported from the Data Lab source commit:

- `src/mobile_gui_vla_data_lab/**`
- `tests/**`
- `configs/p0_reference_plans.json`

Exact Git blobs exported from the Platform dependency commit:

- `src/mobile_gui_vla_platform/**`

Release-layer files authored for this evaluation preview:

- `.gitignore`
- `pyproject.toml`
- `README.md`
- `PRIVACY.md`
- `RELEASE_PROVENANCE.md`
- `RELEASE_NOTES.md`
- `RELEASE_MANIFEST.json`
- `platform_tests/test_type_home.py`

The Platform focused release test is a sanitized, packaging-layer test. It
checks TypeAction/HomeAction validation and mocked ADB dispatch without
including a real endpoint.

## Source closure

Source Git object closure:

- Data Lab package tree: `bb95999539403cb77746a79da77ee0855e39a3c7`
- Data Lab tests tree: `69dde9522670f3e73d479b2e6bcf86f70a08aa48`
- P0 reference-plan blob: `4789bbb88ef2655d0d367f214975decfdd4e6824`
- Platform package tree: `1ce389a69d5ebe8cdebabbea85dfc985533e78f4`

Every file under both published `src/` packages is byte-compared with the blob
at its exact source commit before publication. `RELEASE_MANIFEST.json` records
the SHA-256, byte size, origin, source path, source commit, and source Git blob
OID for every exact export. It records release-layer files by SHA-256 and byte
size. The manifest excludes only itself, and verification requires the set of
manifest paths plus the manifest path to equal the tracked release tree.

No source file in either package is modified for packaging. The combined
`pyproject.toml` is outside both source packages.

## Excluded material

The release excludes private workflow/task history, supervisory records,
status/result/return capsules, raw or derived human data, screenshots, videos,
UI dumps, device identifiers, collector identities, host paths, runtime state,
credentials, model artifacts, and the original repositories' Git histories.

The repository is publicly visible, but no license file is included and no
license is asserted. Public visibility does not grant redistribution or reuse
rights; license selection remains a separate decision. The annotated `v0.1.0`
tag remains on the original sanitized root commit. The publication-transition
commit changes release-layer documentation and manifest metadata only.
