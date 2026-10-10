"""The upstream audit detects drift (synthetic upstream tree + Meson introspection data)."""

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from pdfiumdist import audit  # noqa: E402
from pdfiumdist.sources import Repo  # noqa: E402

POLICY = """
[upstream]
git = "https://example.invalid/pdfium"
[audit]
root = "//:pdfium"
[audit.args]
is_linux = true
[audit.targets]
"//:pdfium" = "pdfium"
"//:pdfium_public_headers_impl" = "public-headers"
"//core/a:a" = "a"
[audit.external]
[audit.extra_defines]
NDEBUG = "release"
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


class AuditTest(unittest.TestCase):
    def build(self, tmp: Path, *, upstream_a: str, meson_sources: list[str], meson_params: list[str]) -> list[dict]:
        repo = tmp / "repo"
        write(repo / "upstream.toml", POLICY)
        write(repo / "meson.build", "project('x')\n")
        write(repo / "sources.lock.json", json.dumps({"schema": 1, "pdfium": {"commit": "0" * 40}, "dependencies": []}))
        pdfium = repo / "upstream" / "pdfium"
        write(pdfium / "BUILD.gn", """
            source_set("pdfium_public_headers_impl") { sources = [ "public/fpdfview.h" ] }
            component("pdfium") {
              deps = [ "core/a" ]
              public_deps = [ ":pdfium_public_headers_impl" ]
            }
        """)
        write(pdfium / "core" / "a" / "BUILD.gn", upstream_a)
        write(pdfium / "pdfium.gni", "declare_args() { pdf_enable_v8 = false }\n")
        write(pdfium / "DEPS", "deps = {}\n")
        for name in ("x.cpp", "y.cpp", "z.cpp", "new.cpp"):
            write(pdfium / "core" / "a" / name, "int f();\n")
        write(pdfium / "public" / "fpdfview.h", "\n")
        listed = "".join(f"    '{name}',\n" for name in meson_sources)
        write(repo / "meson" / "core" / "a" / "meson.build",
              "pdfium_components += [{\n  'name': 'a',\n  'gn': '//core/a:a',\n  'dir': 'core/a',\n"
              f"  'sources': [\n{listed}  ],\n}}]\n")
        write(repo / "meson" / "public" / "meson.build", "pdfium_public_headers = [\n  'fpdfview.h',\n]\n")
        info = tmp / "build" / "meson-info"
        write(info / "meson-info.json", json.dumps({"directories": {"source": str(repo)}}))
        write(info / "intro-targets.json", json.dumps([{
            "name": "a",
            "target_sources": [{"sources": [str(pdfium / "core" / "a" / s) for s in meson_sources],
                                "parameters": meson_params}],
        }]))
        write(info / "intro-installed.json", json.dumps({"h": "/usr/include/pdfium/fpdfview.h"}))
        report = audit.Report()
        audit.collect(Repo(repo), tmp / "build", report)
        static = audit.Report()
        audit.collect(Repo(repo), None, static)
        source_kinds = {"sources", "targets", "externals", "headers"}
        self.assertEqual([f for f in report.findings if f["kind"] in source_kinds],
                         [f for f in static.findings if f["kind"] in source_kinds],
                         "static and introspection audits disagree")
        return report.findings

    def test_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            findings = self.build(Path(tmp),
                                  upstream_a='source_set("a") { sources = [ "x.cpp", "y.cpp" ]\n defines = [ "A" ] }\n',
                                  meson_sources=["x.cpp", "y.cpp"], meson_params=["-DA", "-DNDEBUG"])
            self.assertEqual(findings, [])

    def test_drift_is_reported(self):
        upstream = textwrap.dedent("""
            source_set("a") {
              sources = [ "x.cpp", "y.cpp", "new.cpp" ]
              defines = [ "A", "B=1" ]
              deps = [ ":b", "//third_party/newlib" ]
            }
            source_set("b") { sources = [ "z.cpp" ] }
        """)
        with tempfile.TemporaryDirectory() as tmp:
            findings = self.build(Path(tmp), upstream_a=upstream, meson_sources=["x.cpp", "renamed.cpp"],
                                  meson_params=["-DA", "-DSTRAY"])
        kinds = [(f["kind"], f.get("file") or f.get("define") or f["message"]) for f in findings]
        self.assertIn(("sources", "core/a/y.cpp"), kinds)
        self.assertIn(("sources", "core/a/new.cpp"), kinds)
        self.assertIn(("sources", "core/a/renamed.cpp"), kinds)
        self.assertIn(("defines", "B=1"), kinds)
        self.assertIn(("defines", "STRAY"), kinds)
        self.assertTrue(any(k == "targets" and "//core/a:b" in m for k, m in kinds), kinds)
        self.assertTrue(any(k == "externals" and "//third_party/newlib" in m for k, m in kinds), kinds)


if __name__ == "__main__":
    unittest.main()
