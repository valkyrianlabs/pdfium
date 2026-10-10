"""Discover suitable upstream PDFium revisions and resolve them into sources.lock.json."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.request

from . import sources
from .deps import parse_deps
from .sources import Repo, SourcesError, _git, ensure_commit


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "vaulthalla-pdfium/1"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (fixed https URL)
        return json.load(response)


def branch_head(url: str, branch: str) -> str | None:
    out = _git("ls-remote", url, f"refs/heads/{branch}", timeout=120).decode().split()
    return out[0] if out else None


def discover(repo: Repo) -> dict:
    """The PDFium revision Chrome ships on the tracked channel, verified against googlesource."""
    policy = repo.policy()
    tracking = policy["tracking"]
    url = (
        f"{tracking['dashboard'].rstrip('/')}/fetch_releases"
        f"?channel={tracking['channel']}&platform={tracking['platform']}&num=1"
    )
    releases = _fetch_json(url)
    if not isinstance(releases, list) or not releases:
        raise SourcesError(f"no {tracking['channel']}/{tracking['platform']} release from {url}")
    release = releases[0]
    version = release["version"]
    commit = release["hashes"]["pdfium"]
    build = version.split(".")[2]
    branch = policy["upstream"]["branch_pattern"].format(branch=build)
    git_url = policy["upstream"]["git"]
    head = branch_head(git_url, branch)
    if head is None:
        raise SourcesError(f"{branch} does not exist on {git_url}")
    on_branch = head == commit or _is_ancestor(git_url, commit, branch)
    if not on_branch:
        raise SourcesError(f"dashboard pdfium {commit[:12]} is not on {branch} (head {head[:12]})")
    return {
        "commit": commit,
        "branch": branch,
        "branch_head": head,
        "chromium_version": version,
        "milestone": int(release["milestone"]),
        "channel": tracking["channel"],
        "platform": tracking["platform"],
        "source": url,
    }


def _is_ancestor(url: str, commit: str, branch: str) -> bool:
    mirror = sources._mirror_path(url)
    ensure_commit(url, commit)
    _git("fetch", "--quiet", "--depth", "200", "--no-tags", url, f"refs/heads/{branch}", git_dir=mirror, timeout=900)
    try:
        _git("merge-base", "--is-ancestor", commit, "FETCH_HEAD", git_dir=mirror)
        return True
    except SourcesError:
        return False


def resolve(repo: Repo, *, commit: str, branch: str | None, chromium_version: str | None,
            milestone: int | None, discovery: dict | None) -> dict:
    """Build the lock document for `commit`: dependency pins come from that revision's DEPS."""
    policy = repo.policy()
    git_url = policy["upstream"]["git"]
    mirror = ensure_commit(git_url, commit)
    info = sources.commit_info(mirror, commit)
    exclude = policy["upstream"].get("export_exclude", [])
    all_files = sources.list_files(mirror, commit)
    for pattern in exclude:
        if not any(sources.excluded(f, [pattern]) for f in all_files):
            raise SourcesError(f"upstream.toml export_exclude {pattern!r} matches nothing in {commit[:12]}")
    deps = parse_deps(sources.read_blob(mirror, commit, "DEPS").decode())
    dependencies = []
    for wanted in policy.get("dependency", []):
        path = wanted["deps_path"]
        if path not in deps:
            raise SourcesError(f"DEPS of {commit[:12]} has no entry {path!r}")
        entry = deps[path]
        dep_mirror = ensure_commit(entry.url, entry.revision)
        selected = sources.select_files(sources.list_files(dep_mirror, entry.revision), wanted["files"]) if wanted.get("files") else None
        files = None
        if selected is not None:
            files = {name: hashlib.sha256(sources.read_blob(dep_mirror, entry.revision, name)).hexdigest() for name in selected}
        dependencies.append({
            "deps_path": path,
            "git": entry.url,
            "commit": entry.revision,
            "tree": sources.commit_tree(dep_mirror, entry.revision),
            "license": wanted.get("license"),
            **({"files": files} if files is not None else {}),
        })
    lock = {
        "schema": sources.LOCK_SCHEMA,
        "pdfium": {
            "git": git_url,
            "commit": commit,
            "tree": sources.commit_tree(mirror, commit),
            "committed": info["committed"],
            "subject": info["subject"],
            "branch": branch,
            "chromium_version": chromium_version,
            "milestone": milestone,
            "exclude": exclude,
            "excluded_files": sum(1 for f in all_files if sources.excluded(f, exclude)),
        },
        "dependencies": dependencies,
    }
    if discovery is not None:
        lock["discovery"] = {
            "source": discovery["source"],
            "channel": discovery["channel"],
            "platform": discovery["platform"],
            "checked": dt.date.today().isoformat(),
        }
    return lock
