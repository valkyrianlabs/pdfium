"""Public API/ABI tracking and the SONAME policy (abi/).

PDFium has no ABI guarantee: its C API mostly grows, but functions do change signature (e.g.
FPDFCatalog_SetLanguage took FPDF_BYTESTRING, now FPDF_WIDESTRING) and structs grow. So every
upstream update is classified against abi/public-api.txt, the API of the current SONAME:

  break       a function/symbol removed or its signature changed, a constant's value changed, or
              a struct changed other than by appending fields to a versioned struct
              -> abi/soversion must change (to the new Chromium branch number), which renames the
                 runtime package (libpdfium<soversion>)
  additions   new functions, constants or appended versioned-struct fields: same SONAME
  identical   nothing to do

The API is read from the public headers preprocessed for the supported configuration (no XFA,
V8 or Skia) and cross-checked against the library's exported dynamic symbols. `--update`
records the current API and rewrites debian/libpdfium<soversion>.symbols (dpkg-gensymbols then
fails the package build if a symbol ever disappears).
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from .sources import Repo, SourcesError

C_TYPE_WORDS = {"int", "long", "short", "char", "float", "double", "unsigned", "signed", "void", "const", "struct", "enum", "_Bool"}
EXIT_BREAK = 1
EXIT_OUTDATED = 3


def _preprocess(repo: Repo, headers: list[Path]) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "api.c"
        source.write_text("".join(f'#include "{h.name}"\n' for h in headers))
        result = subprocess.run(
            ["cc", "-E", "-std=c11", "-DPDF_USE_AGG", f"-I{repo.pdfium_dir / 'public'}", str(source)],
            capture_output=True, text=True,
        )
    if result.returncode != 0:
        raise SourcesError(f"preprocessing the public headers failed:\n{result.stderr[-2000:]}")
    # Keep only text that came from the public headers.
    keep, lines = False, []
    public = str(repo.pdfium_dir / "public")
    for line in result.stdout.splitlines():
        if line.startswith("# "):
            match = re.match(r'# \d+ "([^"]+)"', line)
            keep = bool(match and match.group(1).startswith(public))
            continue
        if keep:
            lines.append(line)
    return "\n".join(lines)


def _split_top(text: str, separator: str = ",") -> list[str]:
    parts, depth, current = [], 0, []
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _strip_name(parameter: str) -> str:
    parameter = re.sub(r"\s+", " ", parameter).strip()
    parameter = re.sub(r"\s*\*\s*", "* ", parameter).strip()
    while "* *" in parameter:
        parameter = parameter.replace("* *", "**")
    if "(" in parameter:  # function pointer: drop the declarator name and parameter names
        parameter = re.sub(r"\(\s*\*\s*\w*\s*\)", "(*)", parameter).replace("* )", "*)")
        match = re.match(r"^(.*\(\*\))\s*\((.*)\)$", parameter, re.S)
        if match:
            inner = ", ".join(_strip_name(p) for p in _split_top(match.group(2))) or "void"
            return f"{match.group(1)}({inner})"
        return parameter
    match = re.match(r"^(.*?[\s\*])(\w+)(\s*\[[^\]]*\])?$", parameter)
    if match and match.group(2) not in C_TYPE_WORDS and match.group(1).strip():
        return (match.group(1).strip() + (match.group(3) or "").replace(" ", "")).strip()
    return parameter


def _normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*\*\s*", "* ", text)
    while "* *" in text:
        text = text.replace("* *", "**")
    return re.sub(r"\s+([,)\]])", r"\1", text).replace("( ", "(").strip()


def extract_api(repo: Repo) -> dict[str, str]:
    headers = sorted((repo.pdfium_dir / "public").glob("*.h"))
    text = _preprocess(repo, headers)
    text = re.sub(r"__attribute__\s*\(\(.*?\)\)", "", text)
    api: dict[str, str] = {}
    # Struct layouts (typedef struct NAME { ... } ALIAS;).
    for match in re.finditer(r"typedef\s+struct\s+(\w+)?\s*\{(.*?)\}\s*(\w+)\s*;", text, re.S):
        body, alias = match.group(2), match.group(3)
        members = [_strip_name(m) for m in _split_top(body, ";")]
        api[f"struct {alias}"] = "{ " + "; ".join(_normalize(m) for m in members) + " }"
    # Enumerators with explicit values.
    for match in re.finditer(r"typedef\s+enum\s*\w*\s*\{(.*?)\}\s*(\w+)\s*;", text, re.S):
        api[f"enum {match.group(2)}"] = "{ " + ", ".join(_normalize(e) for e in _split_top(match.group(1))) + " }"
    flat = re.sub(r"typedef[^;{]*\{.*?\}[^;]*;", ";", text, flags=re.S)
    flat = re.sub(r"typedef[^;]*;", ";", flat)
    for statement in flat.split(";"):
        statement = _normalize(statement.replace('extern "C" {', "").replace("}", ""))
        match = re.match(r"^(.*?)\b(\w+)\s*\((.*)\)$", statement, re.S)
        if not match or not match.group(1).strip():
            continue
        ret, name, params = match.group(1).strip(), match.group(2), match.group(3)
        if not re.match(r"^(FPDF|FORM_|FSDK_)", name):
            continue
        parameters = ", ".join(_strip_name(p) for p in _split_top(params)) or "void"
        api[f"func {name}"] = _normalize(f"{ret} {name}({parameters})")
    # Macro constants from the raw headers (values are part of the contract).
    for header in headers:
        for match in re.finditer(r"^#define\s+((?:FPDF|FPDFANNOT|FORMTYPE|FXCT|FLATTEN|PDF|FPDFDEST|FXFONT|FPDF_ERR|FSDK|JSPLAT|FIELDFLAG|FORMFLAG|FPDFTEXT|FILEIDTYPE|PAGEOBJ|FPDFBitmap|FPDF_)\w*)\s+(\S.*?)\s*(?://.*)?$",
                                 header.read_text(), re.M):
            name, value = match.group(1), match.group(2)
            if name.endswith("_H_") or "(" in name:
                continue
            api[f"const {name}"] = _normalize(value)
    return api


def exported_symbols(library: Path) -> set[str]:
    result = subprocess.run(["nm", "-D", "--defined-only", str(library)], capture_output=True, text=True)
    if result.returncode != 0:
        raise SourcesError(f"nm failed for {library}: {result.stderr}")
    return {line.split()[-1] for line in result.stdout.splitlines() if line.strip() and line.split()[1] in "TtWwDdBbRr"}


def read_baseline(path: Path) -> tuple[dict[str, str], str | None]:
    if not path.exists():
        return {}, None
    api, soversion = {}, None
    for line in path.read_text().splitlines():
        if line.startswith("# soversion "):
            soversion = line.split()[2]
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("\t")
        api[key] = value
    return api, soversion


def write_baseline(path: Path, api: dict[str, str], soversion: str, commit: str) -> None:
    lines = [
        "# libpdfium public API of the current SONAME (tools/pdfium-dist abi --update; do not edit).",
        f"# soversion {soversion}",
        f"# pdfium {commit}",
    ]
    lines += [f"{key}\t{api[key]}" for key in sorted(api)]
    path.write_text("\n".join(lines) + "\n")


def _versioned_append(old: str, new: str) -> bool:
    old_members = old.strip("{} ").split("; ")
    new_members = new.strip("{} ").split("; ")
    return bool(old_members) and old_members[0] == "int" and new_members[: len(old_members)] == old_members


def classify(old: dict[str, str], new: dict[str, str]) -> tuple[list[str], list[str]]:
    breaks, additions = [], []
    for key in sorted(old):
        if key not in new:
            breaks.append(f"removed: {key}  {old[key]}")
        elif old[key] != new[key]:
            if key.startswith("struct ") and _versioned_append(old[key], new[key]):
                additions.append(f"extended versioned {key}: {new[key]}")
            elif key.startswith("enum ") and new[key].startswith(old[key].rstrip(" }")):
                additions.append(f"extended {key}: {new[key]}")
            else:
                breaks.append(f"changed: {key}\n      was {old[key]}\n      now {new[key]}")
    for key in sorted(set(new) - set(old)):
        additions.append(f"added: {key}  {new[key]}")
    return breaks, additions


def write_symbols(repo: Repo, symbols: set[str], soversion: str, version: str) -> Path:
    path = repo.root / "debian" / f"libpdfium{soversion}.symbols"
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            match = re.match(r"^ (\S+)@Base (\S+)", line)
            if match:
                existing[match.group(1)] = match.group(2)
    for stale in (repo.root / "debian").glob("libpdfium*.symbols"):
        if stale != path:
            stale.unlink()
    lines = [f"libpdfium.so.{soversion} libpdfium{soversion} #MINVER#",
             "* Build-Depends-Package: libpdfium-dev"]
    lines += [f" {name}@Base {existing.get(name, version)}" for name in sorted(symbols)]
    path.write_text("\n".join(lines) + "\n")
    return path


def run_abi(repo: Repo, *, library: str | None, update: bool) -> int:
    soversion = (repo.root / "abi" / "soversion").read_text().strip()
    version = (repo.root / "VERSION").read_text().strip()
    baseline_path = repo.root / "abi" / "public-api.txt"
    api = extract_api(repo)
    functions = {key.split(" ", 1)[1] for key in api if key.startswith("func ")}
    status = 0
    symbols: set[str] = set()
    if library:
        symbols = exported_symbols(Path(library))
        missing = sorted(functions - symbols)
        unknown = sorted(symbols - functions)
        for name in missing:
            print(f"  NOT EXPORTED  {name} is declared in public/ but not exported by the library")
        for name in unknown:
            print(f"  UNDECLARED    {name} is exported but not declared in public/")
        if missing or unknown:
            status = EXIT_BREAK
    old, old_soversion = read_baseline(baseline_path)
    breaks, additions = classify(old, api) if old else ([], [])
    print(f"abi: {len(functions)} functions, {sum(k.startswith('struct ') for k in api)} structs, "
          f"{sum(k.startswith('const ') for k in api)} constants; soversion {soversion}")
    for line in breaks:
        print(f"  BREAK     {line}")
    for line in additions:
        print(f"  ADDITION  {line}")
    if not old:
        print("abi: no baseline yet (abi/public-api.txt)")
        status = status or EXIT_OUTDATED
    elif breaks and old_soversion == soversion:
        print(f"abi: incompatible change with unchanged soversion {soversion}: set abi/soversion to the new "
              "Chromium branch number, rename the runtime package in debian/control, then --update "
              "(docs/MAINTENANCE.md, 'ABI breaks')")
        status = EXIT_BREAK
    elif breaks or additions or old_soversion != soversion:
        print("abi: compatible with the recorded API" if not breaks else
              f"abi: soversion changed {old_soversion} -> {soversion}, as the breaks require")
        status = status or EXIT_OUTDATED
    else:
        print("abi: identical to the recorded API")
    if update:
        if status == EXIT_BREAK and not (breaks and old_soversion != soversion):
            print("abi: not updating while the check fails")
            return status
        if not library:
            print("abi: --update needs --library (to record the exported symbols)")
            return 2
        commit = repo.lock()["pdfium"]["commit"]
        write_baseline(baseline_path, api, soversion, commit)
        symbols_path = write_symbols(repo, symbols, soversion, version)
        print(f"abi: recorded {baseline_path.relative_to(repo.root)} and {symbols_path.relative_to(repo.root)}")
        return 0
    return status


def soname_bump(repo: Repo, new: str) -> list[str]:
    """Move the packaging to a new SONAME: abi/soversion, debian/ package files, release.toml."""
    if not new.isdigit():
        raise SourcesError("the new soversion must be a number (the Chromium branch, e.g. 8078)")
    old = (repo.root / "abi" / "soversion").read_text().strip()
    if old == new:
        return []
    changed = []
    (repo.root / "abi" / "soversion").write_text(new + "\n")
    changed.append("abi/soversion")
    debian = repo.root / "debian"
    for path in sorted(debian.glob(f"libpdfium{old}.*")):
        target = debian / path.name.replace(f"libpdfium{old}", f"libpdfium{new}", 1)
        if path.suffix == ".symbols":
            path.unlink()  # regenerated by `abi --update` for the new SONAME
            changed.append(f"removed {path.relative_to(repo.root)}")
            continue
        target.write_text(path.read_text().replace(f"libpdfium{old}", f"libpdfium{new}")
                          .replace(f"libpdfium.so.{old}", f"libpdfium.so.{new}"))
        path.unlink()
        changed.append(f"{path.relative_to(repo.root)} -> {target.relative_to(repo.root)}")
    for relative in ("debian/control", "debian/rules", "release.toml"):
        path = repo.root / relative
        text = path.read_text()
        updated = (text.replace(f"libpdfium{old}", f"libpdfium{new}")
                   .replace(f"libpdfium.so.{old}", f"libpdfium.so.{new}")
                   .replace(f"Chromium branch {old}", f"Chromium branch {new}"))
        if updated != text:
            path.write_text(updated)
            changed.append(relative)
    return changed
