"""Command-line entrypoints for collection, QA, manifest, and export."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from mobile_gui_vla_platform import ADBDeviceAdapter

from .collector import CollectionService
from .export import build_manifest, export_model_neutral
from .qa import validate_all
from .web import serve


def _device_map(values: list[str], adb_path: str):
    factories = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--device must be ALIAS=ADB_SERIAL")
        alias, serial = value.split("=", 1)
        if not alias or not serial:
            raise ValueError("--device must contain non-empty alias and serial")
        if alias in factories:
            raise ValueError(f"duplicate device alias: {alias}")
        factories[alias] = lambda serial=serial, alias=alias: ADBDeviceAdapter(
            serial,
            alias=alias,
            adb_path=adb_path,
        )
    return factories


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="run the browser collector")
    serve_parser.add_argument("--artifact-root", type=Path, required=True)
    serve_parser.add_argument("--device", action="append", default=[], metavar="ALIAS=SERIAL")
    serve_parser.add_argument("--adb", default=os.environ.get("ADB", "adb"))
    serve_parser.add_argument("--adb-server-socket", default=None)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--platform-base", required=True)
    serve_parser.add_argument("--platform-dependency", required=True)

    qa_parser = subparsers.add_parser("qa", help="reload and validate all records")
    qa_parser.add_argument("--artifact-root", type=Path, required=True)

    manifest_parser = subparsers.add_parser("manifest", help="build an accepted manifest")
    manifest_parser.add_argument("--artifact-root", type=Path, required=True)
    manifest_parser.add_argument("--dataset-version", required=True)
    manifest_parser.add_argument("--parent-manifest", default=None)
    manifest_parser.add_argument("--seed", type=int, default=0)
    manifest_parser.add_argument("--include-non-training", action="store_true")
    manifest_parser.add_argument("--manifest-role", default="training")

    export_parser = subparsers.add_parser("export", help="write deterministic JSONL")
    export_parser.add_argument("--manifest", type=Path, required=True)
    export_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "serve":
        if not args.device:
            parser.error("serve requires at least one explicit --device alias=serial")
        if args.adb_server_socket:
            os.environ["ADB_SERVER_SOCKET"] = args.adb_server_socket
        service = CollectionService(
            artifact_root=args.artifact_root,
            device_factories=_device_map(args.device, args.adb),
            platform_dependency={
                "base_commit": args.platform_base,
                "dependency_commit": args.platform_dependency,
            },
            allow_natural_model=False,
        )
        serve(service, host=args.host, port=args.port)
        return 0
    if args.command == "qa":
        print(json.dumps(validate_all(args.artifact_root), indent=2, sort_keys=True))
        return 0
    if args.command == "manifest":
        path = build_manifest(
            args.artifact_root,
            dataset_version=args.dataset_version,
            parent_manifest=args.parent_manifest,
            seed=args.seed,
            include_non_training=args.include_non_training,
            manifest_role=args.manifest_role,
        )
        print(path)
        return 0
    if args.command == "export":
        print(
            json.dumps(
                export_model_neutral(args.manifest, output_path=args.output),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
