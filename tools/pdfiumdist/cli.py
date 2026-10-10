"""`tools/pdfium-dist`: upstream acquisition, drift audit, ABI check and source snapshots."""

from __future__ import annotations

import argparse
import json
import sys

from . import sources
from .sources import SourcesError, find_repo

EXIT_UPDATE_AVAILABLE = 10


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2))


def cmd_status(args: argparse.Namespace) -> int:
    repo = find_repo()
    lock = repo.lock()
    pdfium = lock["pdfium"]
    print(f"pdfium      {pdfium['commit']}  {pdfium.get('branch')}  Chromium {pdfium.get('chromium_version')}")
    print(f"            {pdfium['committed']}  {pdfium['subject']}")
    for dep in lock["dependencies"]:
        print(f"dependency  {dep['commit']}  {dep['deps_path']}")
    for patch in repo.patches():
        print(f"patch       {patch.name}")
    problems = sources.verify_prepared(repo) if repo.upstream_dir.exists() else ["upstream/ is not prepared"]
    print("upstream/   " + ("prepared, unmodified" if not problems else "; ".join(problems)))
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from .upstream import discover

    repo = find_repo()
    found = discover(repo)
    current = repo.lock()["pdfium"] if repo.lock_path.exists() else None
    found["locked_commit"] = current["commit"] if current else None
    found["update_available"] = not current or current["commit"] != found["commit"]
    if args.json:
        _print_json(found)
    else:
        print(f"{found['channel']}/{found['platform']}: Chromium {found['chromium_version']} "
              f"(M{found['milestone']}), {found['branch']} pdfium {found['commit']}")
        if current:
            print(f"locked:     Chromium {current.get('chromium_version')} pdfium {current['commit']}")
        print("update available" if found["update_available"] else "up to date")
    return EXIT_UPDATE_AVAILABLE if found["update_available"] and args.exit_code else 0


def cmd_update(args: argparse.Namespace) -> int:
    from .upstream import discover, resolve

    repo = find_repo()
    discovery = None
    if args.commit:
        commit, branch, version, milestone = args.commit, args.branch, args.chromium_version, args.milestone
        if not branch:
            raise SourcesError("--commit needs --branch (e.g. chromium/8059)")
    else:
        discovery = discover(repo)
        commit, branch = discovery["commit"], discovery["branch"]
        version, milestone = discovery["chromium_version"], discovery["milestone"]
    old = repo.lock() if repo.lock_path.exists() else None
    lock = resolve(repo, commit=commit, branch=branch, chromium_version=version, milestone=milestone, discovery=discovery)
    if args.dry_run:
        _print_json(lock)
        return 0
    repo.write_lock(lock)
    print(f"sources.lock.json → pdfium {commit[:12]} ({branch}, Chromium {version})")
    if old and old["pdfium"]["commit"] != commit:
        print(f"  was {old['pdfium']['commit'][:12]} ({old['pdfium'].get('branch')}, Chromium {old['pdfium'].get('chromium_version')})")
    for dep in lock["dependencies"]:
        before = next((d for d in (old or {}).get("dependencies", []) if d["deps_path"] == dep["deps_path"]), None)
        change = "" if before is None or before["commit"] == dep["commit"] else f" (was {before['commit'][:12]})"
        print(f"  {dep['deps_path']} {dep['commit'][:12]}{change}")
    print("Next: tools/pdfium-dist prepare && tools/pdfium-dist audit (docs/MAINTENANCE.md)")
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    repo = find_repo()
    sources.prepare(repo, offline=args.offline, force=args.force, pristine=args.pristine)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    repo = find_repo()
    lock = repo.lock()
    for entry in [lock["pdfium"], *lock["dependencies"]]:
        sources.verify_lock_entry(entry, offline=args.offline)
        print(f"ok  {entry['git']}@{entry['commit'][:12]} tree {entry['tree'][:12]}")
    problems = sources.verify_prepared(repo)
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print("ok  upstream/ matches the lockfile and patch set")
    return 1 if problems else 0


def cmd_cache_prune(args: argparse.Namespace) -> int:
    repo = find_repo()
    sources.prune_cache(repo.lock())
    print(f"cache: {sources.cache_dir()}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    from .audit import run_audit

    return run_audit(find_repo(), builddir=args.builddir, json_out=args.json, update_baseline=args.update_baseline,
                     cpu=args.cpu)


def cmd_abi(args: argparse.Namespace) -> int:
    from .abi import run_abi

    return run_abi(find_repo(), library=args.library, update=args.update)


def cmd_soname_bump(args: argparse.Namespace) -> int:
    from .abi import soname_bump

    for line in soname_bump(find_repo(), args.soversion):
        print(f"  {line}")
    print("Next: rebuild, then `tools/pdfium-dist abi --library build/.../libpdfium.so --update`")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    from .snapshot import run_snapshot

    return run_snapshot(find_repo(), output=args.output, version=args.version, revision=args.revision)


def cmd_verify_snapshot(args: argparse.Namespace) -> int:
    from .snapshot import run_verify_snapshot

    return run_verify_snapshot(find_repo(), args.directory, offline=args.offline)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdfium-dist", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="show the pinned sources and the state of upstream/")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("discover", help="find the PDFium revision Chrome Stable ships (no changes)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--exit-code", action="store_true", help=f"exit {EXIT_UPDATE_AVAILABLE} when an update is available")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("update", help="pin a new upstream revision in sources.lock.json")
    p.add_argument("--commit", help="pin this PDFium commit instead of discovering one")
    p.add_argument("--branch", help="its release branch, e.g. chromium/8059 (with --commit)")
    p.add_argument("--chromium-version", help="the Chromium version shipping it (with --commit)")
    p.add_argument("--milestone", type=int, help="the Chromium milestone (with --commit)")
    p.add_argument("--dry-run", action="store_true", help="print the new lock without writing it")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("prepare", help="export the locked sources into upstream/ and apply patches")
    p.add_argument("--offline", action="store_true", help="use only the local git cache")
    p.add_argument("--force", action="store_true", help="re-export even if upstream/ is current")
    p.add_argument("--pristine", action="store_true", help="do not apply patches/series")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("verify", help="verify pinned trees and that upstream/ is unmodified")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("cache-prune", help="drop cached upstream commits the lockfile no longer pins")
    p.set_defaults(func=cmd_cache_prune)

    p = sub.add_parser("audit", help="compare upstream BUILD.gn with the Meson build (static, no GN)")
    p.add_argument("--builddir", help="a configured Meson build directory to introspect (default: parse meson/)")
    p.add_argument("--json", help="also write the full report to this file")
    p.add_argument("--update-baseline", action="store_true", help="record the reviewed upstream facts in audit/baseline.json")
    p.add_argument("--cpu", help="evaluate BUILD.gn for this target CPU instead of x64 (e.g. arm64)")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("abi", help="check the public API/ABI against abi/ (SONAME policy)")
    p.add_argument("--library", help="built libpdfium.so to read exported symbols from")
    p.add_argument("--update", action="store_true", help="record the current API as the baseline")
    p.set_defaults(func=cmd_abi)

    p = sub.add_parser("soname-bump", help="move the packaging to a new SONAME after an ABI break")
    p.add_argument("soversion", help="the new soversion: the Chromium branch number of the new upstream")
    p.set_defaults(func=cmd_soname_bump)

    p = sub.add_parser("snapshot", help="write the release source snapshot (Debian source package inputs)")
    p.add_argument("--output", default="release", help="output directory (default: release)")
    p.add_argument("--version", help="package version (default: VERSION)")
    p.add_argument("--revision", type=int, default=1, help="Debian revision (default: 1)")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("verify-snapshot", help="check a release snapshot against the pinned upstream trees")
    p.add_argument("directory", help="directory with the snapshot artifacts and provenance record")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(func=cmd_verify_snapshot)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SourcesError as error:
        print(f"pdfium-dist: {error}", file=sys.stderr)
        return 2
