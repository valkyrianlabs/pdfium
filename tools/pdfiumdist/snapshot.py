"""Release source snapshot: a standard Debian source package plus a provenance record.

    libpdfium_<V>.orig.tar.xz            this repository's tracked files (as on disk, i.e. with the
                                         release docs `vlr prepare` promoted), without debian/
    libpdfium_<V>.orig-upstream.tar.xz   upstream/ as prepared: PDFium at the locked commit, the
                                         locked DEPS files, patches/series applied, the stamp
    libpdfium_<V>-<R>.debian.tar.xz      debian/
    libpdfium_<V>-<R>.dsc                (dpkg-source -b; unsigned)
    libpdfium_<V>-<R>.provenance.json    lock, patch and artifact digests, repository commit

Tarballs are deterministic (sorted members, fixed owner/mode, mtime = SOURCE_DATE_EPOCH or the
HEAD commit time). `dpkg-source -x libpdfium_<V>-<R>.dsc && dpkg-buildpackage -b` rebuilds the
release offline; `tools/pdfium-dist verify-snapshot DIR` checks a snapshot against the pinned
upstream trees.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import lzma
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from . import sources
from .sources import Repo, SourcesError, sha256_file


def _epoch(repo: Repo) -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if raw.isdigit():
        return int(raw)
    result = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=repo.root, capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip().isdigit():
        return int(result.stdout.strip())
    raise SourcesError("set SOURCE_DATE_EPOCH (no git history to take the release time from)")


def _tracked_files(repo: Repo) -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=repo.root, capture_output=True)
    if result.returncode != 0:
        raise SourcesError("snapshot needs a git work tree (git ls-files failed)")
    return sorted(path for path in result.stdout.decode().split("\0") if path)


def _add(archive: tarfile.TarFile, path: Path, name: str, mtime: int) -> None:
    info = tarfile.TarInfo(name)
    info.mtime = mtime
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    if path.is_symlink():
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(path)
        info.mode = 0o777
        archive.addfile(info)
    elif path.is_dir():
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        archive.addfile(info)
    else:
        info.size = path.stat().st_size
        info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
        with path.open("rb") as handle:
            archive.addfile(info, handle)


def write_tarball(destination: Path, root: Path, prefix: str, files: list[str], mtime: int) -> None:
    """A deterministic .tar.xz of `files` (relative to `root`) below `prefix/`."""
    directories = sorted({parent.as_posix() for f in files for parent in Path(f).parents if parent.as_posix() != "."})
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        _add(archive, root, prefix, mtime)
        for directory in directories:
            _add(archive, root / directory, f"{prefix}/{directory}", mtime)
        for relative in files:
            _add(archive, root / relative, f"{prefix}/{relative}", mtime)
    destination.write_bytes(lzma.compress(buffer.getvalue(), format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=6))


def _all_files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink())


def _debian_version(repo: Repo, version: str, revision: int) -> str:
    changelog = repo.root / "debian" / "changelog"
    top = changelog.read_text().split("\n", 1)[0] if changelog.exists() else ""
    expected = f"{version}-{revision}"
    if f"({expected})" not in top:
        raise SourcesError(f"debian/changelog does not start with {expected}; run `vlr prepare` first "
                           "(the source package carries the prepared release history)")
    return expected


def run_snapshot(repo: Repo, *, output: str, version: str | None, revision: int) -> int:
    version = version or (repo.root / "VERSION").read_text().strip()
    debian_version = _debian_version(repo, version, revision)
    problems = sources.verify_prepared(repo)
    if problems:
        raise SourcesError("; ".join(problems))
    mtime = _epoch(repo)
    out = (repo.root / output) if not Path(output).is_absolute() else Path(output)
    work = out / ".work"
    if out.exists():
        shutil.rmtree(out)
    work.mkdir(parents=True)

    base = f"libpdfium_{version}"
    tree = f"libpdfium-{version}"
    tracked = [f for f in _tracked_files(repo) if not f.startswith("debian/")]
    missing = [f for f in tracked if not (repo.root / f).exists() and not (repo.root / f).is_symlink()]
    if missing:
        raise SourcesError(f"tracked files missing from the work tree: {', '.join(missing[:5])}")
    orig = work / f"{base}.orig.tar.xz"
    write_tarball(orig, repo.root, tree, tracked, mtime)
    component = work / f"{base}.orig-upstream.tar.xz"
    write_tarball(component, repo.upstream_dir, "upstream", _all_files(repo.upstream_dir), mtime)
    print(f"snapshot: {orig.name} ({orig.stat().st_size // 1024} KiB), {component.name} ({component.stat().st_size // 1024} KiB)")

    # Assemble the unpacked source tree from the tarballs (+ debian/) and let dpkg-source build
    # the .dsc, so the source package is exactly what the tarballs contain.
    unpacked = work / tree
    with tarfile.open(orig) as archive:
        archive.extractall(work, filter="data")
    with tarfile.open(component) as archive:
        archive.extractall(unpacked, filter="data")
    shutil.copytree(repo.root / "debian", unpacked / "debian", symlinks=True,
                    ignore=shutil.ignore_patterns(".debhelper", "tmp", "files", "*.substvars", "*.log",
                                                  "debhelper-build-stamp", "libpdfium8059", "libpdfium-dev"))
    env = dict(os.environ, SOURCE_DATE_EPOCH=str(mtime))
    result = subprocess.run(["dpkg-source", "--build", tree], cwd=work, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise SourcesError(f"dpkg-source --build failed:\n{result.stdout}{result.stderr}")
    shutil.rmtree(unpacked)
    for path in sorted(work.iterdir()):
        shutil.move(str(path), out / path.name)
    work.rmdir()

    lock = repo.lock()
    stamp = sources.read_stamp(repo) or {}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo.root, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo.root,
                                capture_output=True, text=True).stdout.strip())
    provenance = {
        "schema": 1,
        "package": "libpdfium",
        "version": version,
        "debian_version": debian_version,
        "source_date_epoch": mtime,
        "created": dt.datetime.fromtimestamp(mtime, dt.timezone.utc).isoformat(),
        "repository": {"url": "https://github.com/valkyrianlabs/pdfium", "commit": head or None,
                       "modified": dirty},
        "upstream": lock,
        "upstream_content_sha256": stamp.get("content"),
        "patches": {path.name: sha256_file(path) for path in repo.patches()},
        "abi": {"soversion": (repo.root / "abi" / "soversion").read_text().strip()},
        "artifacts": {path.name: sha256_file(path) for path in sorted(out.iterdir())},
    }
    record = out / f"libpdfium_{debian_version}.provenance.json"
    record.write_text(json.dumps(provenance, indent=2) + "\n")
    for path in sorted(out.iterdir()):
        print(f"snapshot: {path.name}")
    return 0


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def run_verify_snapshot(repo: Repo, directory: str, *, offline: bool) -> int:
    """Check a snapshot's upstream tarball against the pinned commits, file by file."""
    folder = Path(directory)
    records = sorted(folder.glob("libpdfium_*.provenance.json"))
    if len(records) != 1:
        raise SourcesError(f"{folder}: expected exactly one provenance record")
    provenance = json.loads(records[0].read_text())
    failures = 0
    for name, digest in provenance["artifacts"].items():
        actual = sha256_file(folder / name) if (folder / name).exists() else "missing"
        if actual != digest:
            print(f"FAIL {name}: sha256 {actual} != recorded {digest}")
            failures += 1
    lock = provenance["upstream"]
    component = next(folder.glob("libpdfium_*.orig-upstream.tar.xz"))
    members: dict[str, bytes] = {}
    with tarfile.open(component) as archive:
        for member in archive.getmembers():
            if member.isfile():
                members[member.name.split("/", 1)[1]] = archive.extractfile(member).read()  # type: ignore[union-attr]
    exclude = lock["pdfium"].get("exclude", [])
    entries = [("pdfium", lock["pdfium"], None)] + [
        (f"pdfium/{dep['deps_path']}", dep, dep.get("files")) for dep in lock["dependencies"]]
    covered: set[str] = set()
    bad_excluded: list[str] = []
    for prefix, entry, subset in entries:
        mirror = sources.verify_lock_entry(entry, offline=offline)
        listing = sources._git("ls-tree", "-r", "-z", entry["commit"], git_dir=mirror).decode().split("\0")
        expected = {}
        for line in filter(None, listing):
            meta, path = line.split("\t", 1)
            mode, kind, sha = meta.split()
            if kind != "blob" or (subset is not None and path not in subset):
                continue
            if prefix == "pdfium" and sources.excluded(path, exclude):
                if f"pdfium/{path}" in members:
                    bad_excluded.append(f"pdfium/{path}")
                continue
            expected[f"{prefix}/{path}"] = sha
        bad = [path for path, sha in expected.items() if path not in members or _git_blob_sha1(members[path]) != sha]
        covered |= set(expected)
        status = "ok  " if not bad else "FAIL"
        print(f"{status} {prefix}: {len(expected) - len(bad)}/{len(expected)} files identical to "
              f"{entry['git']}@{entry['commit'][:12]}")
        failures += bool(bad)
        for path in bad[:10]:
            print(f"       differs or missing: {path}")
    if bad_excluded:
        print(f"FAIL {len(bad_excluded)} excluded file(s) present: {', '.join(bad_excluded[:5])}")
        failures += 1
    elif exclude:
        print(f"ok   excluded upstream files are absent ({', '.join(exclude)})")
    patched = set(members) - covered - {sources.STAMP_NAME}
    if patched:
        print(f"note {len(patched)} file(s) not from a pinned tree: {', '.join(sorted(patched)[:10])}")
        if not provenance["patches"]:
            failures += 1
    return 1 if failures else 0
