"""Upstream compatibility audit: upstream BUILD.gn (read statically) versus the Meson build.

Hard findings (exit status 1) mean the Meson build no longer mirrors upstream for the supported
configuration and must be reconciled before a release:

  sources      a mapped GN source set compiles files the Meson target does not, or vice versa
  targets      the //:pdfium graph reaches an in-tree target with sources that has no Meson mapping
  externals    the graph reaches an external dependency that upstream.toml does not account for
  defines      a define GN applies to a target (own, configs, deps' public configs) is missing
  includes     a compiled source includes third_party/... or build/... that resolves nowhere
  headers      the installed public headers differ from upstream's public headers
  undecided    a BUILD.gn condition the static reader cannot evaluate (and not known-irrelevant)

Review items (reported, not fatal) compare upstream facts with audit/baseline.json, the facts a
maintainer last reviewed: new pdfium.gni build arguments, changed DEPS pins of libraries we take
from the system, changed per-target defines/external deps. `--update-baseline` records the
current facts once they have been reviewed.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import gn
from .deps import parse_deps
from .sources import Repo, SourcesError

COMPILED = (".c", ".cc", ".cpp")
# DEPS entries for libraries that come from Debian/Ubuntu packages (or are not used): their pins
# are not ours to follow, but a change is worth a look (API/behavior expectations upstream).
SYSTEM_DEPS = (
    "third_party/abseil-cpp",
    "third_party/freetype/src",
    "third_party/harfbuzz/src",
    "third_party/icu",
    "third_party/libjpeg_turbo",
    "third_party/zlib",
)
INCLUDE = re.compile(rb'^\s*#\s*include\s+"((?:third_party|build)/[^"]+)"', re.M)


@dataclass
class Report:
    findings: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    facts: dict = field(default_factory=dict)

    def hard(self, kind: str, message: str, **data) -> None:
        self.findings.append({"kind": kind, "message": message, **data})

    def note(self, kind: str, message: str, **data) -> None:
        self.review.append({"kind": kind, "message": message, **data})


class GnGraph:
    def __init__(self, root: Path, args: dict):
        self.root = root
        self.evaluator = gn.Evaluator(root, args)
        self.files: dict[str, gn.GnFile] = {}

    def file(self, label: str) -> gn.GnFile | None:
        path = gn.label_file(label)
        if not (self.root / path).is_file():
            return None
        if path not in self.files:
            self.files[path] = self.evaluator.read(path)
        return self.files[path]

    def target(self, label: str) -> gn.Target | None:
        found = self.file(label)
        return found.targets.get(label) if found else None

    def config(self, label: str) -> gn.Target | None:
        found = self.file(label)
        return found.configs.get(label) if found else None

    def config_defines(self, label: str, seen: set[str] | None = None) -> set[str]:
        seen = seen if seen is not None else set()
        if label in seen:
            return set()
        seen.add(label)
        config = self.config(label)
        if config is None:
            return set()
        result = set(config.values.get("defines", []))
        for nested in config.values.get("configs", []):
            result |= self.config_defines(nested, seen)
        return result

    def public_defines(self, label: str, seen: set[str] | None = None) -> set[str]:
        """Defines a dependency on `label` brings along (public/all-dependent configs, public_deps)."""
        seen = seen if seen is not None else set()
        if label in seen:
            return set()
        seen.add(label)
        target = self.target(label)
        if target is None:
            return set()
        result: set[str] = set()
        for config in target.values.get("public_configs", []) + target.values.get("all_dependent_configs", []):
            result |= self.config_defines(config)
        for dep in target.values.get("public_deps", []):
            result |= self.public_defines(dep, seen)
        return result

    def target_defines(self, target: gn.Target) -> set[str]:
        result = set(target.values.get("defines", []))
        for config in target.values.get("configs", []):
            result |= self.config_defines(config)
        for dep in target.values.get("deps", []) + target.values.get("public_deps", []):
            result |= self.public_defines(dep)
        return result


_COMPONENT = re.compile(r"pdfium_components \+= \[\{(.*?)\}\]", re.S)


def _static_meson(repo: Repo) -> tuple[dict[str, dict], list[str]]:
    """Components and installed public headers read from meson/**/meson.build (pure data files)."""
    components: dict[str, dict] = {}
    for path in sorted((repo.root / "meson").rglob("meson.build")):
        for body in _COMPONENT.findall(path.read_text()):
            name = re.search(r"'name':\s*'([^']+)'", body).group(1)
            directory = re.search(r"'dir':\s*'([^']+)'", body).group(1)
            listed = re.search(r"'sources':\s*\[(.*?)\]", body, re.S).group(1)
            sources = [f"{directory}/{item}" for item in re.findall(r"'([^']+)'", listed)]
            components[name] = {"sources": sources, "file": str(path.relative_to(repo.root))}
    public = (repo.root / "meson" / "public" / "meson.build").read_text()
    headers = []
    for variable in ("pdfium_public_headers", "pdfium_public_cpp_headers"):
        block = re.search(variable + r"\s*=\s*\[(.*?)\]", public, re.S)
        headers += re.findall(r"'([^']+)'", block.group(1)) if block else []
    return components, headers


def _meson_targets(builddir: Path) -> tuple[dict[str, dict], Path, list[str]]:
    info = builddir / "meson-info"
    if not (info / "intro-targets.json").exists():
        raise SourcesError(f"{builddir} is not a configured Meson build directory")
    source = Path(json.loads((info / "meson-info.json").read_text())["directories"]["source"])
    targets = {t["name"]: t for t in json.loads((info / "intro-targets.json").read_text())}
    installed = list(json.loads((info / "intro-installed.json").read_text()).values())
    return targets, source, installed


def _meson_view(target: dict, pdfium_root: Path) -> tuple[set[str], set[str]]:
    sources: set[str] = set()
    defines: set[str] = set()
    for group in target.get("target_sources", []):
        for source in group.get("sources", []):
            try:
                sources.add(Path(source).resolve().relative_to(pdfium_root).as_posix())
            except ValueError:
                sources.add(source)
        defines |= {p[2:] for p in group.get("parameters", []) if p.startswith("-D")}
    return sources, defines


def _configure(repo: Repo, builddir: Path) -> None:
    if (builddir / "meson-info" / "intro-targets.json").exists():
        return
    command = ["meson", "setup", str(builddir), "-Dtests=false", "--wrap-mode=nodownload"]
    result = subprocess.run(command, cwd=repo.root, capture_output=True, text=True)
    if result.returncode != 0:
        raise SourcesError(f"meson setup {builddir} failed (run inside ci/run-ci?):\n{result.stdout[-2000:]}{result.stderr[-2000:]}")


def collect(repo: Repo, builddir: Path | None, report: Report, cpu: str | None = None) -> None:
    policy = repo.policy()
    audit = policy["audit"]
    mapping: dict[str, str] = audit["targets"]
    externals: dict[str, str] = audit["external"]
    allowed_extra = set(audit.get("extra_defines", {}))
    ignored = audit.get("ignore_undecided", {})
    pdfium_root = repo.pdfium_dir.resolve()
    if cpu:  # evaluate BUILD.gn for another target CPU (e.g. arm64)
        audit["args"] = dict(audit["args"], current_cpu=cpu, target_cpu=cpu)
    graph = GnGraph(pdfium_root, audit["args"])

    static_components, static_headers = _static_meson(repo)
    meson_targets: dict[str, dict] = {}
    installed: list[str] = []
    meson_pdfium_root = pdfium_root
    if builddir is not None:
        meson_targets, meson_source, installed = _meson_targets(builddir)
        meson_pdfium_root = (meson_source / Path(repo.pdfium_dir).resolve().relative_to(repo.root.resolve())) \
            if repo.pdfium_dir.resolve().is_relative_to(repo.root.resolve()) else pdfium_root
    else:
        report.note("mode", "static audit of meson/ (no build directory): compile defines and installed files "
                    "are checked only with --builddir")

    # Walk //:pdfium through deps and public_deps.
    order: list[str] = []
    pending = [audit["root"]]
    while pending:
        label = pending.pop()
        if label in order:
            continue
        order.append(label)
        target = graph.target(label)
        if target is None:
            continue
        pending += target.values.get("deps", []) + target.values.get("public_deps", [])

    facts_targets = {}
    compiled_files: set[str] = set()
    for label in sorted(order):
        target = graph.target(label)
        if target is None:
            if label not in externals:
                report.hard("externals", f"{label} is a new external dependency of {audit['root']}; decide how to "
                            "satisfy it and record it in upstream.toml [audit.external]")
            continue
        if label in externals and not target.values.get("sources"):
            continue
        sources = {s for s in target.values.get("sources", []) if s.endswith(COMPILED)}
        defines = sorted(graph.target_defines(target))
        external_deps = sorted(d for d in target.values.get("deps", []) + target.values.get("public_deps", [])
                               if graph.target(d) is None or d in externals)
        facts_targets[label] = {"defines": defines, "external_deps": external_deps, "sources": len(sources)}
        meson_name = mapping.get(label)
        if meson_name is None:
            if sources:
                report.hard("targets", f"{label} ({target.file}) compiles {len(sources)} files but has no Meson "
                            "component; add one under meson/ and map it in upstream.toml [audit.targets]",
                            sources=sorted(sources))
            continue
        if meson_name in ("headers-only", "public-headers") or meson_name == "pdfium":
            if sources:
                report.hard("targets", f"{label} now compiles sources: {', '.join(sorted(sources))}")
            continue
        if meson_name not in static_components:
            report.hard("sources", f"Meson component {meson_name!r} (for {label}) is not defined under meson/")
            continue
        meson_sources = set(static_components[meson_name]["sources"])
        meson_defines = set(defines)
        if builddir is not None:
            meson_target = meson_targets.get(meson_name)
            if meson_target is None:
                report.hard("sources", f"Meson target {meson_name!r} (for {label}) is not in {builddir}")
                continue
            built_sources, meson_defines = _meson_view(meson_target, meson_pdfium_root)
            if built_sources != meson_sources:
                report.hard("sources", f"{builddir} is stale for {meson_name}: reconfigure it")
        compiled_files |= sources
        where = static_components[meson_name]["file"]
        for missing in sorted(sources - meson_sources):
            report.hard("sources", f"{label}: {missing} is compiled upstream but not by {meson_name} ({where})",
                        target=label, file=missing)
        for extra in sorted(meson_sources - sources):
            moved = [m for m in sources - meson_sources if Path(m).name == Path(extra).name]
            hint = f" — moved to {moved[0]}?" if moved else " (removed or renamed upstream?)"
            report.hard("sources", f"{label}: {meson_name} ({where}) compiles {extra}, which upstream no longer "
                        f"lists{hint}", target=label, file=extra)
        meson_names = {d.split("=", 1)[0] for d in meson_defines}
        for define in defines:
            if define not in meson_defines and ("=" in define or define not in meson_names):
                report.hard("defines", f"{label}: upstream applies -D{define}, Meson target {meson_name} does not",
                            target=label, define=define)
        gn_names = {d.split("=", 1)[0] for d in defines}
        unexplained = sorted(d for d in meson_defines - set(defines)
                             if d.split("=", 1)[0] not in allowed_extra and d.split("=", 1)[0] not in gn_names)
        for define in unexplained:
            report.hard("defines", f"{label}: Meson adds -D{define}, which neither upstream nor upstream.toml "
                        "[audit.extra_defines] accounts for", target=label, define=define)

    # Mapped GN targets the graph no longer reaches: their Meson components are stale.
    for label, meson_name in sorted(mapping.items()):
        if label not in order and meson_name not in ("headers-only", "public-headers", "pdfium"):
            report.hard("targets", f"{label} is no longer part of {audit['root']}: remove Meson component "
                        f"{meson_name!r} (or move its sources) and its upstream.toml [audit.targets] entry")

    # Undecided GN conditions.
    for path, parsed in sorted(graph.files.items()):
        for item in parsed.undecided:
            if any(token in item.what for token in ignored.get(path, [])):
                continue
            report.hard("undecided", f"{path}:{item.line}: {item.what}; extend [audit.args] or "
                        "[audit.ignore_undecided] after checking it")

    # Includes of third_party/... and build/... from the compiled sources.
    shims = repo.root / "meson" / "shims"
    include_prefixes: dict[str, int] = {}
    for relative in sorted(compiled_files):
        path = pdfium_root / relative
        if not path.exists():
            continue
        for match in INCLUDE.finditer(path.read_bytes()):
            include = match.group(1).decode()
            parts = include.split("/")
            prefix = "/".join(parts[:2])
            include_prefixes[prefix] = include_prefixes.get(prefix, 0) + 1
            if not (pdfium_root / include).exists() and not (shims / include).exists():
                guarded = _guarded_include(path, include)
                if not guarded:
                    report.hard("includes", f"{relative} includes {include}, which neither the PDFium tree nor "
                                "meson/shims provides", file=relative, include=include)

    # Public headers: upstream list, upstream directory, installed files.
    upstream_listed = set()
    impl = graph.target("//:pdfium_public_headers_impl")
    if impl:
        upstream_listed = {s.removeprefix("public/") for s in impl.values.get("sources", [])}
    on_disk = {p.relative_to(pdfium_root / "public").as_posix() for p in (pdfium_root / "public").rglob("*.h")}
    installed_headers = set(static_headers)
    if installed:
        installed_headers = {p.split("/include/pdfium/", 1)[1] for p in installed
                             if "/include/pdfium/" in p and p.endswith(".h")}
    for header in sorted((upstream_listed | on_disk) - installed_headers):
        report.hard("headers", f"public/{header} exists upstream but is not installed")
    for header in sorted(installed_headers - on_disk):
        report.hard("headers", f"{header} is installed but no longer exists in public/")
    for header in sorted(on_disk - upstream_listed):
        report.note("headers", f"public/{header} is installed although //:pdfium_public_headers_impl omits it")

    # Facts for the review baseline.
    gni = gn.Evaluator(pdfium_root, audit["args"])
    declared = gni.read("pdfium.gni").declared_args if (pdfium_root / "pdfium.gni").exists() else []
    deps = parse_deps((pdfium_root / "DEPS").read_text())
    report.facts = {
        "pdfium": repo.lock()["pdfium"]["commit"],
        "pdfium_gni_args": sorted(declared),
        "system_deps": {path: deps[path].revision for path in SYSTEM_DEPS if path in deps},
        "targets": facts_targets,
        "include_prefixes": dict(sorted(include_prefixes.items())),
    }


def _guarded_include(path: Path, include: str) -> bool:
    """True when the include sits in a preprocessor branch we do not compile (#if defined(...))."""
    text = path.read_text(errors="replace").splitlines()
    depth_stack: list[str] = []
    for line in text:
        stripped = line.strip()
        if stripped.startswith("#if"):
            depth_stack.append(stripped)
        elif stripped.startswith("#endif") and depth_stack:
            depth_stack.pop()
        elif stripped.startswith("#else") and depth_stack:
            depth_stack[-1] = "#else " + depth_stack[-1]
        elif include in stripped and stripped.startswith("#include"):
            return any(re.search(r"USE_SYSTEM_|PDF_ENABLE_|PDF_USE_SKIA|BUILDFLAG\((IS_WIN|IS_APPLE|IS_MAC|IS_ANDROID)", cond)
                       for cond in depth_stack)
    return False


def compare_baseline(report: Report, baseline: dict) -> None:
    facts = report.facts
    if not baseline:
        report.note("baseline", "audit/baseline.json does not exist yet; review the facts and run --update-baseline")
        return
    if baseline.get("pdfium") == facts["pdfium"]:
        return
    before, after = set(baseline.get("pdfium_gni_args", [])), set(facts["pdfium_gni_args"])
    for arg in sorted(after - before):
        report.note("gn-args", f"pdfium.gni declares a new build argument {arg!r}: decide whether it affects the "
                    "supported configuration ([audit.args])")
    for arg in sorted(before - after):
        report.note("gn-args", f"pdfium.gni no longer declares {arg!r}")
    for path, revision in facts["system_deps"].items():
        old = baseline.get("system_deps", {}).get(path)
        if old != revision:
            report.note("system-deps", f"DEPS moved {path} {str(old)[:12]} -> {revision[:12]}: check the system "
                        "package still satisfies what upstream now expects")
    for label, data in facts["targets"].items():
        old = baseline.get("targets", {}).get(label)
        if old is None:
            report.note("targets", f"{label} is new in the //:pdfium graph")
            continue
        if old.get("defines") != data["defines"]:
            report.note("defines", f"{label}: upstream defines changed {old.get('defines')} -> {data['defines']}")
        if old.get("external_deps") != data["external_deps"]:
            report.note("externals", f"{label}: external deps changed {old.get('external_deps')} -> {data['external_deps']}")
    for label in sorted(set(baseline.get("targets", {})) - set(facts["targets"])):
        report.note("targets", f"{label} left the //:pdfium graph")
    for prefix in sorted(set(facts["include_prefixes"]) - set(baseline.get("include_prefixes", {}))):
        report.note("includes", f"compiled sources now include {prefix}/...")


def run_audit(repo: Repo, *, builddir: str | None, json_out: str | None, update_baseline: bool,
              cpu: str | None = None) -> int:
    directory = Path(builddir) if builddir else None
    report = Report()
    collect(repo, directory, report, cpu)
    baseline_path = repo.root / "audit" / "baseline.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    compare_baseline(report, baseline)

    pdfium = repo.lock()["pdfium"]
    print(f"audit: pdfium {pdfium['commit'][:12]} ({pdfium.get('branch')}) against {directory or 'meson/ (static)'}"
          + (f", target cpu {cpu}" if cpu else ""))
    for finding in report.findings:
        print(f"  DRIFT  [{finding['kind']}] {finding['message']}")
    for item in report.review:
        print(f"  REVIEW [{item['kind']}] {item['message']}")
    mapped = sum(1 for v in repo.policy()["audit"]["targets"].values() if v not in ("headers-only", "public-headers", "pdfium"))
    print(f"audit: {len(report.findings)} drift finding(s), {len(report.review)} review item(s); "
          f"{len(report.facts['targets'])} GN targets in the graph, {mapped} Meson components")
    if json_out:
        Path(json_out).write_text(json.dumps({"findings": report.findings, "review": report.review, "facts": report.facts},
                                             indent=2) + "\n")
    if update_baseline:
        if cpu:
            print("audit: the baseline records the default profile; drop --cpu to update it")
            return 1
        if report.findings:
            print("audit: refusing to update the baseline while drift findings remain")
            return 1
        baseline_path.parent.mkdir(exist_ok=True)
        baseline_path.write_text(json.dumps(report.facts, indent=2, sort_keys=True) + "\n")
        print(f"audit: recorded {baseline_path.relative_to(repo.root)}")
    return 1 if report.findings else 0
