"""Source snapshot tarballs are deterministic."""

import hashlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from pdfiumdist.snapshot import write_tarball  # noqa: E402


class SnapshotTest(unittest.TestCase):
    def test_tarball_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tree"
            (root / "a" / "b").mkdir(parents=True)
            (root / "a" / "b" / "file.txt").write_text("hello\n")
            (root / "run").write_text("#!/bin/sh\n")
            os.chmod(root / "run", 0o755)
            os.symlink(".", root / "a" / "self")
            files = ["a/b/file.txt", "a/self", "run"]
            first, second = Path(tmp) / "1.tar.xz", Path(tmp) / "2.tar.xz"
            write_tarball(first, root, "pkg-1", files, 1_700_000_000)
            time.sleep(1.1)
            os.utime(root / "a" / "b" / "file.txt")
            write_tarball(second, root, "pkg-1", files, 1_700_000_000)
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(),
                             hashlib.sha256(second.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
