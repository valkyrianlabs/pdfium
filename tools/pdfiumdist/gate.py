"""Auto-release gate for an upstream candidate (`tools/upstream-candidate`, `upstream.yml`).

`upstream-candidate` writes build/upstream-candidate/result.json. A candidate is released without a
maintainer only when nothing in it needs judgement:

  status    the build, integration tests, audits, `vlr check` and the unit tests passed
  drift     no audit DRIFT finding (the Meson build mirrors upstream)
  review    no new audit REVIEW item (no new pdfium.gni argument, moved system DEPS pin, changed
            per-target defines or externals, unaccepted standing note: facts a maintainer has not
            seen; items accepted in audit/baseline.json `accepted_review` do not count)
  abi       identical or additions only (same SONAME); a break moves the SONAME and every
            consumer must be rebuilt, so it is always a maintainer's release

Anything else stays an ordinary pull request, labelled for review, with the reasons listed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

RELEASABLE_ABI = ("identical", "additions")
ABI_BY_EXIT = {0: "identical", 3: "additions", 1: "break"}


def abi_class(exit_status: int) -> str:
    """`pdfium-dist abi` exit status -> identical / additions / break / unknown (no library, error)."""
    return ABI_BY_EXIT.get(exit_status, "unknown")


def blockers(result: dict) -> list[str]:
    """Why `result` must not be released automatically; empty means it may be."""
    found = []
    if result.get("status") != "passed":
        found.append("the candidate failed: see the failing steps in the report")
    drift = result.get("drift")
    if drift is None:
        found.append("no audit with compile defines (the build did not get that far)")
    elif drift:
        found.append(f"{drift} audit DRIFT finding(s) to reconcile")
    if result.get("review"):
        found.append(f"{result['review']} new audit REVIEW item(s) to read before the baseline is updated")
    abi = result.get("abi", "unknown")
    if abi == "break":
        found.append(f"ABI break: the SONAME moves to libpdfium.so.{result.get('soversion')}; consumers must be rebuilt")
    elif abi not in RELEASABLE_ABI:
        found.append("the ABI was not checked against a built library")
    if not result.get("new_version"):
        found.append("no candidate VERSION recorded")
    return found


def run_gate(path: str, *, github_output: str | None) -> int:
    """Print the decision; with `github_output` ("" = $GITHUB_OUTPUT) also append release/version/blockers."""
    result = json.loads(Path(path).read_text())
    found = blockers(result)
    release = not found
    print(f"gate: {result.get('new_version')} ({result.get('new_branch')} {str(result.get('new_commit'))[:12]}): "
          + ("release automatically" if release else "needs a maintainer"))
    for reason in found:
        print(f"  - {reason}")
    output = os.environ.get("GITHUB_OUTPUT") if github_output == "" else github_output
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"release={'true' if release else 'false'}\n")
            handle.write(f"version={result.get('new_version') or ''}\n")
            handle.write("blockers<<GATE_EOF\n" + "".join(f"- {reason}\n" for reason in found) + "GATE_EOF\n")
    return 0 if release else 1
