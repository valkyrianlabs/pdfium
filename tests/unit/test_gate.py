"""The auto-release gate: only a candidate that needs no judgement is released without a maintainer."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from pdfiumdist import gate  # noqa: E402

# The shape tools/upstream-candidate writes; values of the M155 -> M156 update (156.8078.0),
# which needed a Meson fix (one DRIFT finding) and recorded ABI additions.
M156 = {
    "status": "failed", "abi": "additions", "soversion": "8059", "drift": 1, "review": 0,
    "old_version": "155.8059.1", "new_version": "156.8078.0",
    "old_branch": "chromium/8059", "new_branch": "chromium/8078",
    "old_commit": "a" * 40, "new_commit": "2f22dfe030ded6a7666aeea4899d400bd2e338a4",
    "chromium_version": "156.0.7800.0", "milestone": "156",
}
CLEAN = {**M156, "status": "passed", "drift": 0, "review": 0, "abi": "identical"}


class GateTest(unittest.TestCase):
    def test_clean_candidate_releases(self):
        self.assertEqual(gate.blockers(CLEAN), [])
        self.assertEqual(gate.blockers({**CLEAN, "abi": "additions"}), [])

    def test_the_m156_update_needed_a_maintainer(self):
        reasons = gate.blockers(M156)
        self.assertTrue(any("failed" in r for r in reasons))
        self.assertTrue(any("1 audit DRIFT" in r for r in reasons))

    def test_each_judgement_call_blocks(self):
        cases = {
            "REVIEW": {"review": 2},
            "ABI break": {"abi": "break", "soversion": "8078"},
            "not checked": {"abi": "unknown"},
            "no audit": {"drift": None},
            "VERSION": {"new_version": ""},
        }
        for needle, change in cases.items():
            with self.subTest(needle):
                reasons = gate.blockers({**CLEAN, **change})
                self.assertEqual(len(reasons), 1, reasons)
                self.assertIn(needle, reasons[0])

    def test_missing_fields_fail_closed(self):
        self.assertGreaterEqual(len(gate.blockers({})), 3)

    def test_abi_exit_status(self):
        self.assertEqual([gate.abi_class(n) for n in (0, 3, 1, 2)], ["identical", "additions", "break", "unknown"])

    def test_github_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = Path(tmp, "result.json"), Path(tmp, "out")
            result.write_text(json.dumps({**CLEAN, "review": 1}))
            with contextlib.redirect_stdout(io.StringIO()):
                status = gate.run_gate(str(result), github_output=str(output))
            self.assertEqual(status, 1)
            text = output.read_text()
            self.assertIn("release=false\n", text)
            self.assertIn("version=156.8078.0\n", text)
            self.assertIn("- 1 new audit REVIEW item(s)", text)

    def test_accepted_review_items_do_not_count(self):
        from pdfiumdist import audit

        standing = {"kind": "headers", "message": "public/fpdf_thumbnail.h is installed although x omits it"}
        new = {"kind": "gn-args", "message": "pdfium.gni declares a new build argument 'x'"}
        baseline = {"accepted_review": [standing["message"]]}
        self.assertEqual(audit.unaccepted([standing, new], baseline), [new])
        self.assertEqual(audit.unaccepted([standing], {}), [standing])

    def test_baseline_accepts_the_standing_items(self):
        baseline = json.loads((ROOT / "audit/baseline.json").read_text())
        self.assertIn("accepted_review", baseline)

    def test_workflow_releases_through_the_gate(self):
        workflow = (ROOT / ".github/workflows/upstream.yml").read_text()
        self.assertIn("tools/pdfium-dist gate", workflow)
        self.assertIn("vars.UPSTREAM_AUTO_RELEASE != 'false'", workflow)
        self.assertIn('gh workflow run release.yml --ref "$tag" -f publish=publish', workflow)
        self.assertIn("--match-head-commit", workflow)
        self.assertIn("git add -A sources.lock.json VERSION abi audit debian release.toml .release", workflow)


if __name__ == "__main__":
    unittest.main()
