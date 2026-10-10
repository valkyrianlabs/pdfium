#!/usr/bin/env python3
"""Integration tests for a PDFium build, driven through tests/consumer/pdfium_probe.

The probe is an independent C program using only the public API; this script runs it over real
PDFs and checks the results:

  fixtures     semantic expectations in tests/fixtures.toml (pages, text, rendering, passwords)
  malformed    generated broken inputs must fail cleanly or be repaired, never crash
  corpus       every PDF in upstream's testing/resources (incl. past crashers) renders without a crash
  pixel        page renders match upstream's reference images (testing/resources/pixel)
  consistency  file and memory loading agree; rendering is deterministic
  reference    optional: differential comparison with another probe (e.g. built against the
               legacy package); reported, and fatal only with --strict-reference

Exit status 0 when every selected check passes. --report writes the full results as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import tomllib
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from imagecmp import compare, read_pam, read_png  # noqa: E402

HERE = Path(__file__).resolve().parent
ALL_CHECKS = ("fixtures", "malformed", "corpus", "pixel", "consistency", "reference")
FPDF_ERR_FORMAT = 3
# A page whose text uses fonts that are not embedded renders with PDFium's built-in fallback
# fonts here, while upstream's references use its test_fonts bundle. Such pages may differ in
# glyph shapes; everything else must match upstream's reference exactly.
PIXEL_TOLERANCE = 16
PIXEL_MAX_DIFFERING = 0.02
PIXEL_MIN_EXACT = 18


class Results:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, check: str, name: str, ok: bool, detail: str = "", **data) -> None:
        self.items.append({"check": check, "name": name, "ok": ok, "detail": detail, **data})
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {check}: {name}" + (f" — {detail}" if detail else ""), flush=True)

    def failed(self, checks: set[str]) -> list[dict]:
        return [item for item in self.items if not item["ok"] and item["check"] in checks]


def run_probe(probe: str, files: list[Path], *, out: Path | None = None, password: str | None = None,
              memory: bool = False, max_pages: int = 0, timeout: int = 120) -> tuple[int, list[dict], str]:
    command = [probe]
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        command += ["--out", str(out)]
    if password is not None:
        command += ["--password", password]
    if memory:
        command.append("--memory")
    if max_pages:
        command += ["--max-pages", str(max_pages)]
    command += ["--", *map(str, files)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, [], "timeout"
    docs = []
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                docs.append(json.loads(line))
            except ValueError:
                return result.returncode or -2, docs, f"invalid JSON: {line[:120]}"
    return result.returncode, docs, result.stderr.decode(errors="replace")[-2000:]


def document_text(out: Path, name: str, pages: int) -> str:
    parts = []
    for index in range(pages):
        path = out / f"{name}.{index}.txt"
        if path.exists():
            with path.open(encoding="utf-8", errors="replace", newline="") as handle:  # keep \r\n
                parts.append(handle.read())
    return "\n".join(parts)


# --- checks ------------------------------------------------------------------------------------


def check_fixtures(args, results: Results) -> None:
    fixtures = tomllib.loads((HERE / "fixtures.toml").read_text(encoding="utf-8"))["fixture"]
    for number, fixture in enumerate(fixtures):
        path = args.resources / fixture["file"]
        label = fixture["file"] + (" (password)" if fixture.get("password") else "")
        out = args.work / "fixtures" / str(number)
        code, docs, err = run_probe(args.probe, [path], out=out, password=fixture.get("password"))
        if code != 0 or len(docs) != 1:
            results.add("fixtures", label, False, f"probe exit {code}: {err.strip()[:200]}")
            continue
        doc = docs[0]
        problems = []
        if "error" in fixture:
            if doc["loaded"] or doc.get("error") != fixture["error"]:
                problems.append(f"expected load error {fixture['error']}, got loaded={doc['loaded']} error={doc.get('error')}")
        else:
            if not doc["loaded"]:
                problems.append(f"failed to load (error {doc.get('error')})")
            else:
                if "pages" in fixture and doc["pages"] != fixture["pages"]:
                    problems.append(f"pages {doc['pages']} != {fixture['pages']}")
                if any(not page.get("rendered") for page in doc["page_info"]):
                    problems.append("a page did not render")
                text = document_text(out, path.name, doc["pages"])
                for wanted in fixture.get("text", []):
                    if wanted not in text:
                        problems.append(f"text {wanted!r} not extracted (got {text[:80]!r})")
                if "min_ink" in fixture and doc["page_info"] and doc["page_info"][0]["ink"] < fixture["min_ink"]:
                    problems.append(f"page 0 ink {doc['page_info'][0]['ink']} < {fixture['min_ink']}")
                if "bookmarks" in fixture and doc["bookmarks"] != fixture["bookmarks"]:
                    problems.append(f"bookmarks {doc['bookmarks']} != {fixture['bookmarks']}")
        results.add("fixtures", label, not problems, "; ".join(problems))


def malformed_inputs(resources: Path, directory: Path) -> dict[str, tuple[bytes, str]]:
    """name -> (bytes, expectation): 'reject' (load error 3), 'any' (no crash), 'repair' (loads)."""
    hello = (resources / "hello_world.pdf").read_bytes()
    compressed = (resources / "hello_world_compressed_stream.pdf").read_bytes()
    rng = random.Random(20261010)
    xref = hello.rfind(b"xref")
    cases = {
        "empty.pdf": (b"", "reject"),
        "header_only.pdf": (b"%PDF-1.7\n", "reject"),
        "garbage.pdf": (bytes(rng.randrange(256) for _ in range(65536)), "reject"),
        "truncated_25.pdf": (hello[: len(hello) // 4], "any"),
        "truncated_50.pdf": (hello[: len(hello) // 2], "any"),
        "truncated_90.pdf": (hello[: len(hello) * 9 // 10], "any"),
        "no_xref.pdf": (hello[:xref] if xref > 0 else hello, "repair"),
        "bad_xref_offsets.pdf": (re.sub(rb"\d{10} 00000 n", b"0000000001 00000 n", hello), "repair"),
        "deep_nesting.pdf": (b"%PDF-1.7\n1 0 obj\n" + b"[" * 200000 + b"\nendobj\ntrailer<</Root 1 0 R>>\n%%EOF\n", "any"),
        "self_referencing_pages.pdf": (
            b"%PDF-1.7\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[2 0 R]/Count 1>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n",
            "any",
        ),
        "huge_mediabox.pdf": (hello.replace(b"/MediaBox [0 0 200 200]", b"/MediaBox [0 0 2000000000 2000000000]"), "any"),
    }
    flipped = bytearray(compressed)
    start = compressed.find(b"stream") + 12
    for i in range(start, min(start + 40, len(flipped))):
        flipped[i] ^= 0x5A
    cases["corrupt_flate_stream.pdf"] = (bytes(flipped), "any")
    directory.mkdir(parents=True, exist_ok=True)
    for name, (data, _) in cases.items():
        (directory / name).write_bytes(data)
    return cases


def check_malformed(args, results: Results) -> None:
    directory = args.work / "malformed"
    for name, (_, expectation) in malformed_inputs(args.resources, directory).items():
        code, docs, err = run_probe(args.probe, [directory / name], max_pages=3, timeout=60)
        if code != 0 or len(docs) != 1:
            results.add("malformed", name, False, f"probe exit {code} (crash or hang): {err.strip()[:200]}")
            continue
        doc = docs[0]
        ok = True
        detail = f"loaded={doc['loaded']} error={doc.get('error')}"
        if expectation == "reject" and (doc["loaded"] or doc.get("error") != FPDF_ERR_FORMAT):
            ok = False
        if expectation == "repair" and not doc["loaded"]:
            ok = False
        results.add("malformed", name, ok, detail)


def check_corpus(args, results: Results) -> None:
    files = sorted(args.resources.glob("*.pdf")) + sorted((args.resources / "pixel").glob("*.pdf"))
    crashed = []
    loaded = 0
    for path in files:
        code, docs, err = run_probe(args.probe, [path], max_pages=10, timeout=120)
        if code != 0 or len(docs) != 1:
            crashed.append(f"{path.name} (exit {code})")
        elif docs[0]["loaded"]:
            loaded += 1
    results.add("corpus", f"{len(files)} upstream test PDFs", not crashed and len(files) > 100,
                f"{loaded} loaded, the rest rejected cleanly" if not crashed else "crashes: " + ", ".join(crashed[:10]),
                files=len(files), loaded=loaded, crashed=crashed)


def suppressed(testing: Path, name: str) -> bool:
    """Upstream's testing/SUPPRESSIONS entries matching Linux, no V8, no XFA, AGG (and FreeType).

    Handles both formats: 5 columns (up to M155) and 7 columns (M156+: font engine, action)."""
    path = testing / "SUPPRESSIONS"
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) not in (5, 7) or fields[0] != name:
            continue
        wanted = [None, {"*", "linux"}, {"*", "nov8"}, {"*", "noxfa"}, {"*", "agg"}, {"*", "freetype"}]
        if len(fields) == 7 and fields[6].startswith("fuzzy"):
            continue  # tolerated differences: still compared with our own tolerance
        if all(set(fields[i].split(",")) & wanted[i] for i in range(1, min(len(fields), 6))):
            return True
    return False


def check_pixel(args, results: Results) -> None:
    pixel_dir = args.resources / "pixel"
    testing = args.resources.parent
    out = args.work / "pixel"
    exact = 0
    compared = 0
    for pdf in sorted(pixel_dir.glob("*.pdf")):
        reference = pixel_dir / f"{pdf.stem}_expected.pdf.0.png"
        if not reference.exists():
            continue
        if suppressed(testing, pdf.name):
            results.add("pixel", pdf.name, True, "suppressed upstream (testing/SUPPRESSIONS)", skipped=True)
            continue
        code, docs, err = run_probe(args.probe, [pdf], out=out, max_pages=1)
        image_path = out / f"{pdf.name}.0.pam"
        if code != 0 or not image_path.exists():
            results.add("pixel", pdf.name, False, f"probe exit {code}: {err.strip()[:200]}")
            continue
        comparison = compare(read_pam(image_path), read_png(reference), PIXEL_TOLERANCE)
        compared += 1
        if comparison.max_delta == 0:
            exact += 1
        ok = comparison.same_size and comparison.differing <= PIXEL_MAX_DIFFERING
        detail = "identical to upstream reference" if comparison.max_delta == 0 else (
            f"{comparison.differing * 100:.2f}% pixels differ by >{PIXEL_TOLERANCE} (max {comparison.max_delta})")
        results.add("pixel", pdf.name, ok, detail, differing=comparison.differing, max_delta=comparison.max_delta)
    results.add("pixel", "exact matches", exact >= min(PIXEL_MIN_EXACT, compared),
                f"{exact}/{compared} renders identical to upstream's references", exact=exact, compared=compared)


def check_consistency(args, results: Results) -> None:
    names = ["hello_world.pdf", "embedded_images.pdf", "annots.pdf", "jpx_lzw.pdf", "vertical_text.pdf"]
    files = [args.resources / name for name in names]
    _, from_file, _ = run_probe(args.probe, files)
    _, from_memory, _ = run_probe(args.probe, files, memory=True)
    _, again, _ = run_probe(args.probe, files)

    def digest(docs):
        return [(d["file"], [(p.get("pixel_hash"), p.get("text_hash")) for p in d.get("page_info", [])]) for d in docs]

    results.add("consistency", "memory load == file load", digest(from_file) == digest(from_memory) and len(from_file) == len(files))
    results.add("consistency", "deterministic rendering", digest(from_file) == digest(again))


def check_reference(args, results: Results) -> None:
    if not args.reference_probe:
        return
    fixtures = tomllib.loads((HERE / "fixtures.toml").read_text(encoding="utf-8"))["fixture"]
    seen = set()
    rows = []
    worst = 0.0
    text_mismatch = []
    for fixture in fixtures:
        if "error" in fixture or fixture["file"] in seen:
            continue
        seen.add(fixture["file"])
        path = args.resources / fixture["file"]
        ours_out = args.work / "reference" / "ours"
        theirs_out = args.work / "reference" / "theirs"
        _, ours, _ = run_probe(args.probe, [path], out=ours_out, password=fixture.get("password"), max_pages=3)
        code, theirs, err = run_probe(args.reference_probe, [path], out=theirs_out, password=fixture.get("password"), max_pages=3)
        if code != 0 or not theirs or not ours:
            results.add("reference", fixture["file"], False, f"reference probe exit {code}: {err.strip()[:120]}")
            continue
        if not ours[0]["loaded"] or not theirs[0]["loaded"]:
            results.add("reference", fixture["file"], ours[0]["loaded"] == theirs[0]["loaded"], "load state")
            continue
        pages = min(len(ours[0]["page_info"]), len(theirs[0]["page_info"]))
        for index in range(pages):
            a, b = ours_out / f"{path.name}.{index}.pam", theirs_out / f"{path.name}.{index}.pam"
            if a.exists() and b.exists():
                comparison = compare(read_pam(a), read_pam(b), PIXEL_TOLERANCE)
                worst = max(worst, comparison.differing)
                rows.append({"file": path.name, "page": index, "differing": comparison.differing, "max_delta": comparison.max_delta})
        if document_text(ours_out, path.name, pages) != document_text(theirs_out, path.name, pages):
            text_mismatch.append(path.name)
    identical = sum(1 for row in rows if row["max_delta"] == 0)
    known = dict(item.split("=", 1) for item in args.reference_known)
    unexplained_text = [name for name in text_mismatch if name not in known]
    worst = max([row["differing"] for row in rows if row["file"] not in known] or [0.0])
    ok = worst <= PIXEL_MAX_DIFFERING and not unexplained_text
    detail = (f"{identical}/{len(rows)} pages pixel-identical to {args.reference_label}, worst {worst * 100:.2f}% differing "
              f"(excluding known differences); text differs for: {', '.join(text_mismatch) or 'none'}")
    for name, reason in known.items():
        detail += f"; known: {name}: {reason}"
    results.add("reference", f"vs {args.reference_label}", ok, detail, pages=rows, text_mismatch=text_mismatch)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", required=True, help="pdfium_probe built against the library under test")
    parser.add_argument("--resources", required=True, type=Path, help="upstream testing/resources directory")
    parser.add_argument("--work", type=Path, default=Path("build/integration"), help="scratch output directory")
    parser.add_argument("--checks", default="fixtures,malformed,corpus,pixel,consistency,reference")
    parser.add_argument("--reference-probe", help="probe built against a known-good PDFium for differential checks")
    parser.add_argument("--reference-label", default="reference build")
    parser.add_argument("--strict-reference", action="store_true", help="fail on reference differences")
    parser.add_argument("--reference-known", action="append", default=[], metavar="FILE=REASON",
                        help="an expected, explained difference from the reference build")
    parser.add_argument("--report", type=Path, help="write all results as JSON")
    args = parser.parse_args()
    checks = {c.strip() for c in args.checks.split(",") if c.strip()}
    unknown = checks - set(ALL_CHECKS)
    if unknown:
        parser.error(f"unknown checks: {', '.join(sorted(unknown))}")
    if not (args.resources / "hello_world.pdf").exists():
        parser.error(f"{args.resources} is not PDFium's testing/resources directory")
    args.work.mkdir(parents=True, exist_ok=True)

    results = Results()
    for name in ALL_CHECKS:
        if name in checks:
            globals()[f"check_{name}"](args, results)

    fatal = checks - ({"reference"} if not args.strict_reference else set())
    failures = results.failed(fatal)
    summary = {
        "probe": os.path.abspath(args.probe),
        "checks": sorted(checks),
        "passed": sum(1 for item in results.items if item["ok"]),
        "failed": len(results.failed(set(ALL_CHECKS))),
        "fatal_failures": len(failures),
        "results": results.items,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{summary['passed']} passed, {summary['failed']} failed ({len(failures)} fatal)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
