"""The DEPS reader parses without executing the file."""

import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from pdfiumdist.deps import parse_deps  # noqa: E402

DEPS = """
use_relative_paths = True
vars = {
  'chromium_git': 'https://chromium.googlesource.com',
  'build_revision': 'aaaa',
  'fast_float_revision': 'bbbb',
}
deps = {
  'build': Var('chromium_git') + '/chromium/src/build.git@' + Var('build_revision'),
  'third_party/fast_float/src': {
    'url': Var('chromium_git') + '/external/fast_float.git@' + Var('fast_float_revision'),
    'condition': 'checkout_x',
  },
  'tools/cipd': {'packages': [{'package': 'x', 'version': 'y'}], 'dep_type': 'cipd'},
}
hooks = [ {'action': ['python3', '-c', 'import os; os.system("rm -rf /")']} ]
"""


class DepsTest(unittest.TestCase):
    def test_parse(self):
        deps = parse_deps(DEPS)
        self.assertEqual(deps["build"].url, "https://chromium.googlesource.com/chromium/src/build.git")
        self.assertEqual(deps["build"].revision, "aaaa")
        self.assertEqual(deps["third_party/fast_float/src"].revision, "bbbb")
        self.assertEqual(deps["third_party/fast_float/src"].condition, "checkout_x")
        self.assertNotIn("tools/cipd", deps)


if __name__ == "__main__":
    unittest.main()
