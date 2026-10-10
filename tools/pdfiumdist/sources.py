"""Acquire, verify and export the pinned upstream sources (sources.lock.json).

Everything here is ordinary Git: a shallow fetch of an exact commit into a cache of bare
repositories, verification of the commit's tree id against the lockfile, and a `git archive`
export into the ignored `upstream/` directory. No gclient, no hooks, no network access once the
cache holds the pinned commits.

Layout produced by `prepare` (mirrors where gclient would put DEPS entries):

    upstream/pdfium/                      PDFium at the pinned commit (+ patches/series)
    upstream/pdfium/build/...             Chromium build headers (subset, from DEPS)
    upstream/pdfium/third_party/*/src/    header-only DEPS dependencies (subset)
    upstream/.prepared.json               stamp: lock + patch digests, content digest
"""

from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

LOCK_SCHEMA = 1
STAMP_NAME = ".prepared.json"


class SourcesError(RuntimeError):
    pass


# --- repository files ------------------------------------------------------------------------


@dataclass(frozen=True)
class Repo:
    root: Path

    @property
    def lock_path(self) -> Path:
        return self.root / "sources.lock.json"

    @property
    def policy_path(self) -> Path:
        return self.root / "upstream.toml"

    @property
    def upstream_dir(self) -> Path:
        return Path(os.environ.get("PDFIUM_UPSTREAM_DIR", self.root / "upstream"))

    @property
    def pdfium_dir(self) -> Path:
        return self.upstream_dir / "pdfium"

    @property
    def patches_dir(self) -> Path:
        return self.root / "patches"

    def policy(self) -> dict:
        with self.policy_path.open("rb") as handle:
            return tomllib.load(handle)

    def lock(self) -> dict:
        if not self.lock_path.exists():
            raise SourcesError(f"{self.lock_path.name} does not exist; run `tools/pdfium-dist update` first")
        data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        if data.get("schema") != LOCK_SCHEMA:
            raise SourcesError(f"{self.lock_path.name}: unsupported schema {data.get('schema')!r}")
        return data

    def write_lock(self, data: dict) -> None:
        text = json.dumps(data, indent=2, sort_keys=False) + "\n"
        tmp = self.lock_path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.lock_path)

    def patches(self) -> list[Path]:
        series = self.patches_dir / "series"
        if not series.exists():
            return []
        names = [line.split("#", 1)[0].strip() for line in series.read_text(encoding="utf-8").splitlines()]
        result = []
        for name in names:
            if not name:
                continue
            path = self.patches_dir / name
            if not path.is_file():
                raise SourcesError(f"patches/series lists {name}, which does not exist")
            result.append(path)
        return result


def find_repo(start: Path | None = None) -> Repo:
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "upstream.toml").is_file() and (candidate / "meson.build").is_file():
            return Repo(candidate)
    raise SourcesError("cannot find the repository root (upstream.toml + meson.build)")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lock_digest(lock: dict) -> str:
    return hashlib.sha256(json.dumps(lock, sort_keys=True).encode()).hexdigest()


# --- git cache -------------------------------------------------------------------------------


def cache_dir() -> Path:
    explicit = os.environ.get("PDFIUM_CACHE_DIR")
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "vaulthalla-pdfium"


def _git(*args: str, git_dir: Path | None = None, input: bytes | None = None, timeout: int = 600) -> bytes:
    command = ["git"]
    if git_dir is not None:
        command += ["--git-dir", str(git_dir)]
    command += list(args)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    result = subprocess.run(command, input=input, capture_output=True, timeout=timeout, env=env)
    if result.returncode != 0:
        raise SourcesError(f"git {' '.join(args[:3])} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def _mirror_path(url: str) -> Path:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", re.sub(r"^https?://", "", url).removesuffix(".git")).strip("_")
    return cache_dir() / "git" / f"{name}.git"


@contextlib.contextmanager
def _cache_lock():
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def ensure_commit(url: str, commit: str, *, offline: bool = False) -> Path:
    """Make `commit` available in the cache mirror for `url` and return the mirror's git dir."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SourcesError(f"not a full commit id: {commit!r}")
    mirror = _mirror_path(url)
    with _cache_lock():
        if not mirror.exists():
            mirror.parent.mkdir(parents=True, exist_ok=True)
            _git("init", "--quiet", "--bare", str(mirror))
        try:
            _git("cat-file", "-e", f"{commit}^{{commit}}", git_dir=mirror)
            return mirror
        except SourcesError:
            pass
        if offline:
            raise SourcesError(f"{commit[:12]} from {url} is not cached and --offline was given")
        last: SourcesError | None = None
        for _ in range(3):
            try:
                _git("fetch", "--quiet", "--depth", "1", "--no-tags", url, commit, git_dir=mirror, timeout=900)
                _git("cat-file", "-e", f"{commit}^{{commit}}", git_dir=mirror)
                _git("update-ref", f"refs/pins/{commit}", commit, git_dir=mirror)
                return mirror
            except SourcesError as error:
                last = error
        raise SourcesError(f"cannot fetch {commit[:12]} from {url}: {last}")


def prune_cache(lock: dict, *, log=print) -> None:
    """Drop cached commits the lockfile no longer pins, then garbage-collect each mirror."""
    root = cache_dir() / "git"
    if not root.exists():
        return
    wanted: dict[Path, set[str]] = {}
    for entry in [lock["pdfium"], *lock["dependencies"]]:
        wanted.setdefault(_mirror_path(entry["git"]), set()).add(entry["commit"])
    with _cache_lock():
        for mirror in sorted(root.glob("*.git")):
            keep = wanted.get(mirror, set())
            if not keep:
                shutil.rmtree(mirror)
                log(f"removed {mirror.name}")
                continue
            refs = _git("for-each-ref", "--format=%(refname)", "refs/pins/", git_dir=mirror).decode().split()
            for ref in refs:
                if ref.rsplit("/", 1)[-1] not in keep:
                    _git("update-ref", "-d", ref, git_dir=mirror)
            for commit in keep:  # pin commits fetched before pins existed
                try:
                    _git("update-ref", f"refs/pins/{commit}", commit, git_dir=mirror)
                except SourcesError:
                    pass
            _git("reflog", "expire", "--expire=now", "--all", git_dir=mirror)
            _git("gc", "--quiet", "--prune=now", git_dir=mirror, timeout=1800)
            log(f"pruned {mirror.name} (keeping {len(keep)} commit(s))")


def commit_tree(mirror: Path, commit: str) -> str:
    return _git("rev-parse", f"{commit}^{{tree}}", git_dir=mirror).decode().strip()


def commit_info(mirror: Path, commit: str) -> dict:
    raw = _git("show", "-s", "--format=%cI%x00%s", commit, git_dir=mirror).decode()
    committed, subject = raw.rstrip("\n").split("\x00", 1)
    return {"committed": committed, "subject": subject}


def read_blob(mirror: Path, commit: str, path: str) -> bytes:
    return _git("show", f"{commit}:{path}", git_dir=mirror)


def list_files(mirror: Path, commit: str) -> list[str]:
    return _git("ls-tree", "-r", "--name-only", "-z", commit, git_dir=mirror).decode().split("\x00")[:-1]


def excluded(path: str, patterns: list[str]) -> bool:
    """`dir/**` matches everything below dir; other patterns are fnmatch globs."""
    for pattern in patterns:
        if pattern.endswith("/**") and path.startswith(pattern[:-2]):
            return True
        if fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def select_files(all_files: list[str], patterns: list[str]) -> list[str]:
    selected = sorted({path for path in all_files for pattern in patterns if fnmatch.fnmatchcase(path, pattern)})
    for pattern in patterns:
        if not any(fnmatch.fnmatchcase(path, pattern) for path in all_files):
            raise SourcesError(f"file pattern {pattern!r} matches nothing")
    return selected


def export(mirror: Path, commit: str, destination: Path, files: list[str] | None = None) -> None:
    """Extract `commit` (or a subset of its files) into `destination` with `git archive`."""
    args = ["archive", "--format=tar", commit]
    if files is not None:
        args += ["--", *files]
    data = _git(*args, git_dir=mirror, timeout=900)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        archive.extractall(destination, filter="data")


# --- verification ----------------------------------------------------------------------------


def verify_lock_entry(entry: dict, *, offline: bool) -> Path:
    mirror = ensure_commit(entry["git"], entry["commit"], offline=offline)
    tree = commit_tree(mirror, entry["commit"])
    if tree != entry["tree"]:
        raise SourcesError(f"{entry['git']}@{entry['commit'][:12]}: tree {tree} != locked {entry['tree']}")
    return mirror


def content_digest(root: Path, exclude: tuple[str, ...] = (STAMP_NAME,)) -> str:
    """sha256 over every file below `root` (sorted relative paths, modes and contents)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink()):
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        digest.update(relative.encode() + b"\0")
        if path.is_symlink():
            digest.update(b"L" + os.readlink(path).encode())
        else:
            digest.update(b"X" if os.access(path, os.X_OK) else b"F")
            digest.update(sha256_file(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


# --- prepare ---------------------------------------------------------------------------------


def _stamp_inputs(repo: Repo, lock: dict) -> dict:
    return {
        "lock": lock_digest(lock),
        "patches": {path.name: sha256_file(path) for path in repo.patches()},
    }


def read_stamp(repo: Repo) -> dict | None:
    path = repo.upstream_dir / STAMP_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def is_prepared(repo: Repo, lock: dict | None = None) -> bool:
    stamp = read_stamp(repo)
    lock = lock if lock is not None else repo.lock()
    return bool(stamp) and {k: stamp.get(k) for k in ("lock", "patches")} == _stamp_inputs(repo, lock)


def prepare(repo: Repo, *, offline: bool = False, force: bool = False, pristine: bool = False, log=print) -> Path:
    """Export the locked sources into upstream/ and apply patches/series (unless `pristine`)."""
    lock = repo.lock()
    if not force and not pristine and is_prepared(repo, lock):
        log(f"upstream/ already prepared for pdfium {lock['pdfium']['commit'][:12]}")
        return repo.upstream_dir
    target = repo.upstream_dir
    staging = target.with_name(target.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    pdfium = lock["pdfium"]
    log(f"pdfium {pdfium['commit'][:12]} ({pdfium.get('branch', '?')}, Chromium {pdfium.get('chromium_version', '?')})")
    mirror = verify_lock_entry(pdfium, offline=offline)
    exclude = pdfium.get("exclude", [])
    files = None
    if exclude:
        files = [f for f in list_files(mirror, pdfium["commit"]) if not excluded(f, exclude)]
    export(mirror, pdfium["commit"], staging / "pdfium", files)
    for dep in lock["dependencies"]:
        log(f"  {dep['deps_path']} {dep['commit'][:12]}")
        dep_mirror = verify_lock_entry(dep, offline=offline)
        destination = staging / "pdfium" / dep["deps_path"]
        if destination.exists() and any(destination.iterdir()):
            raise SourcesError(f"{dep['deps_path']} already exists in the PDFium tree")
        files = sorted(dep["files"]) if dep.get("files") else None
        export(dep_mirror, dep["commit"], destination, files)
        for relative, expected in (dep.get("files") or {}).items():
            actual = sha256_file(destination / relative)
            if actual != expected:
                raise SourcesError(f"{dep['deps_path']}/{relative}: sha256 {actual} != locked {expected}")
    if not pristine:
        apply_patches(repo, staging / "pdfium", log=log)
    stamp = dict(_stamp_inputs(repo, lock), pristine=pristine, pdfium=pdfium["commit"])
    stamp["content"] = content_digest(staging)
    (staging / STAMP_NAME).write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    log(f"prepared {target}")
    return target


def apply_patches(repo: Repo, pdfium_dir: Path, *, log=print) -> None:
    for patch in repo.patches():
        log(f"  patch {patch.name}")
        result = subprocess.run(
            ["patch", "-p1", "--forward", "--batch", "--no-backup-if-mismatch", "-d", str(pdfium_dir), "-i", str(patch)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SourcesError(f"{patch.name} does not apply:\n{result.stdout}{result.stderr}")


def verify_prepared(repo: Repo) -> list[str]:
    """Problems with upstream/ relative to the lockfile (empty when it is exactly as prepared)."""
    stamp = read_stamp(repo)
    if stamp is None:
        return ["upstream/ is not prepared (no stamp); run `tools/pdfium-dist prepare`"]
    problems = []
    lock = repo.lock()
    if {k: stamp.get(k) for k in ("lock", "patches")} != _stamp_inputs(repo, lock):
        problems.append("upstream/ was prepared from a different sources.lock.json or patch set")
    if content_digest(repo.upstream_dir) != stamp.get("content"):
        problems.append("upstream/ was modified after it was prepared")
    return problems
