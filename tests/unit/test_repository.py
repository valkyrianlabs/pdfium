"""Repository consistency: the facts that must agree across files when something changes.

Run by `vlr cut` (release.toml test_command) and CI. Each failure message says what to update.
"""

import json
import re
import sys
import tomllib
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def control_paragraphs() -> list[dict[str, str]]:
    paragraphs = []
    for block in read("debian/control").strip().split("\n\n"):
        fields: dict[str, str] = {}
        key = None
        for line in block.splitlines():
            if line.startswith((" ", "\t")) and key:
                fields[key] += "\n" + line.strip()
            elif ":" in line:
                key, _, value = line.partition(":")
                fields[key] = value.strip()
        paragraphs.append(fields)
    return paragraphs


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.soversion = read("abi/soversion").strip()
        self.runtime = f"libpdfium{self.soversion}"
        self.version = read("VERSION").strip()
        self.lock = json.loads(read("sources.lock.json"))
        self.policy = tomllib.loads(read("upstream.toml"))
        self.release = tomllib.loads(read("release.toml"))

    def test_runtime_package_follows_soversion(self):
        packages = [p["Package"] for p in control_paragraphs()[1:]]
        self.assertEqual(packages, [self.runtime, "libpdfium-dev"],
                         "debian/control package names must follow abi/soversion")
        dev = control_paragraphs()[2]
        self.assertIn(f"{self.runtime} (= ${{binary:Version}})", dev["Depends"])
        for suffix in ("install", "docs", "lintian-overrides", "symbols"):
            self.assertTrue((ROOT / "debian" / f"{self.runtime}.{suffix}").exists(), f"debian/{self.runtime}.{suffix}")
        self.assertEqual(sorted(p.name for p in (ROOT / "debian").glob("libpdfium[0-9]*")),
                         sorted(f"{self.runtime}.{s}" for s in ("install", "docs", "lintian-overrides", "symbols")),
                         "stale per-SONAME files in debian/")
        self.assertIn(f"libpdfium.so.{self.soversion}*", read(f"debian/{self.runtime}.install"))
        self.assertTrue(read(f"debian/{self.runtime}.symbols").startswith(f"libpdfium.so.{self.soversion} {self.runtime} "))
        self.assertIn(self.runtime, read("debian/rules"))
        contracts = {p["name"]: p for p in self.release["debian"]["packages"]}
        self.assertEqual(set(contracts), {self.runtime, "libpdfium-dev"}, "release.toml [[debian.packages]]")
        self.assertIn(f"usr/lib/*/libpdfium.so.{self.soversion}", contracts[self.runtime]["required_paths"])

    def test_abi_baseline_matches_soversion(self):
        header = read("abi/public-api.txt").splitlines()[:3]
        self.assertIn(f"# soversion {self.soversion}", header, "run tools/pdfium-dist abi --update")

    def test_version_policy(self):
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", self.version)
        self.assertIsNotNone(match, "VERSION must be MAJOR.MINOR.PATCH")
        pdfium = self.lock["pdfium"]
        self.assertEqual(int(match.group(1)), pdfium["milestone"], "VERSION major must be the Chromium milestone")
        self.assertEqual(f"chromium/{match.group(2)}", pdfium["branch"], "VERSION minor must be the Chromium branch")

    def test_lock_matches_policy(self):
        self.assertEqual(self.lock["schema"], 1)
        self.assertEqual(self.lock["pdfium"]["git"], self.policy["upstream"]["git"])
        wanted = [d["deps_path"] for d in self.policy["dependency"]]
        self.assertEqual([d["deps_path"] for d in self.lock["dependencies"]], wanted,
                         "run tools/pdfium-dist update after changing upstream.toml dependencies")
        for entry in [self.lock["pdfium"], *self.lock["dependencies"]]:
            self.assertRegex(entry["commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(entry["tree"], r"^[0-9a-f]{40}$")

    def test_meson_components_match_audit_mapping(self):
        names = set()
        labels = {}
        for path in (ROOT / "meson").rglob("meson.build"):
            text = path.read_text()
            for name, label in re.findall(r"'name': '([^']+)',\s*'gn': '([^']+)'", text):
                names.add(name)
                labels[name] = label
        mapping = self.policy["audit"]["targets"]
        mapped = {v for v in mapping.values() if v not in ("headers-only", "public-headers", "pdfium")}
        self.assertEqual(names, mapped, "meson components and upstream.toml [audit.targets] disagree")
        for name, label in labels.items():
            self.assertEqual(mapping.get(label), name, f"{label} must map to {name}")

    def test_container_carries_build_depends(self):
        build_depends = control_paragraphs()[0]["Build-Depends"]
        names = [re.split(r"[\s(|]", item.strip())[0] for item in build_depends.replace("\n", " ").split(",") if item.strip()]
        containerfile = read("ci/Containerfile")
        for name in names:
            if name in ("debhelper-compat",):
                continue
            self.assertRegex(containerfile, rf"\b{re.escape(name)}\b", f"ci/Containerfile lacks Build-Depends {name}")

    def test_patches_series(self):
        series = ROOT / "patches" / "series"
        if not series.exists():
            return
        for line in series.read_text().splitlines():
            name = line.split("#", 1)[0].strip()
            if name:
                self.assertTrue((ROOT / "patches" / name).is_file(), f"patches/{name}")

    def test_tools_are_executable(self):
        for relative in ("tools/pdfium-dist", "tools/release-rehearsal", "ci/run-ci", "debian/rules",
                         "tests/package/run", "tests/package/clean-install.sh"):
            self.assertTrue((ROOT / relative).stat().st_mode & 0o111, f"{relative} is not executable")


if __name__ == "__main__":
    unittest.main()
