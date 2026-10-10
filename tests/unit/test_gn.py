"""The static GN reader on representative BUILD.gn constructs (no GN binary involved)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from pdfiumdist import gn  # noqa: E402

BUILD_GN = """
import("../../pdfium.gni")

config("strict") {
  defines = [ "STRICT" ]
  if (!is_clang) {
    cflags = [ "-Wno-return-type" ]
  }
}

source_set("fxcrt") {
  sources = [
    "a.cpp",
    "a.h",
  ]
  configs += [ ":strict" ]
  deps = [ "//third_party/fast_float" ]
  public_deps = [ "../fxge:fxge" ]
  if (pdf_use_partition_alloc) {
    sources += [ "pa.cpp" ]
  } else {
    sources += [ "malloc.cpp" ]
  }
  if (is_posix && !is_win) {
    sources += [ "posix.cpp" ]
  } else if (is_win) {
    sources += [ "win.cpp" ]
  }
  if (some_unknown_arg) {
    sources += [ "maybe.cpp" ]
  }
  sources -= [ "a.h" ]
}

pdfium_unittest_source_set("unittests") {
  sources = [ "a_unittest.cpp" ]
}
"""


class GnReaderTest(unittest.TestCase):
    def read(self, args):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "core" / "fxcrt" / "BUILD.gn"
            path.parent.mkdir(parents=True)
            path.write_text(BUILD_GN)
            return gn.Evaluator(Path(tmp), args).read("core/fxcrt/BUILD.gn")

    def test_conditions_and_lists(self):
        parsed = self.read({"is_posix": True, "is_win": False, "is_clang": False, "pdf_use_partition_alloc": False})
        target = parsed.targets["//core/fxcrt:fxcrt"]
        self.assertEqual(target.values["sources"],
                         ["core/fxcrt/a.cpp", "core/fxcrt/malloc.cpp", "core/fxcrt/posix.cpp"])
        self.assertEqual(target.values["deps"], ["//third_party/fast_float:fast_float"])
        self.assertEqual(target.values["public_deps"], ["//core/fxge:fxge"])
        self.assertEqual(target.values["configs"], ["//core/fxcrt:strict"])
        self.assertEqual(parsed.configs["//core/fxcrt:strict"].values["cflags"], ["-Wno-return-type"])
        self.assertIn("//core/fxcrt:unittests", parsed.targets)

    def test_undecidable_condition_is_reported_not_guessed(self):
        parsed = self.read({"is_posix": True, "is_win": False, "is_clang": True, "pdf_use_partition_alloc": True})
        self.assertIn("core/fxcrt/pa.cpp", parsed.targets["//core/fxcrt:fxcrt"].values["sources"])
        self.assertNotIn("core/fxcrt/maybe.cpp", parsed.targets["//core/fxcrt:fxcrt"].values["sources"])
        self.assertTrue(any("some_unknown_arg" in item.what for item in parsed.undecided))

    def test_labels(self):
        self.assertEqual(gn._label("core/fxcrt", ":x"), "//core/fxcrt:x")
        self.assertEqual(gn._label("core/fxcrt", "../../third_party:fx_agg"), "//third_party:fx_agg")
        self.assertEqual(gn._label("", "core/fxge"), "//core/fxge:fxge")
        self.assertEqual(gn.label_file("//:pdfium"), "BUILD.gn")


if __name__ == "__main__":
    unittest.main()
