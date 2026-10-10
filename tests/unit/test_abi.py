"""ABI classification: the SONAME policy's break/addition rules."""

import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from pdfiumdist import abi  # noqa: E402


class AbiTest(unittest.TestCase):
    def test_parameter_names_do_not_matter(self):
        self.assertEqual(abi._strip_name("FPDF_FILEWRITE* pFileWrite"), "FPDF_FILEWRITE*")
        self.assertEqual(abi._strip_name("unsigned long"), "unsigned long")
        self.assertEqual(abi._strip_name("const char** paths"), "const char**")
        self.assertEqual(abi._strip_name("int (*callback)(void* param, int x)"), "int (*)(void*, int)")

    def test_real_upstream_breaks(self):
        # Changes between the legacy snapshot (2025-06) and chromium/8059.
        old = {"func FPDFCatalog_SetLanguage": "FPDF_BOOL FPDFCatalog_SetLanguage(FPDF_DOCUMENT, FPDF_BYTESTRING)",
               "func FPDFPage_InsertObject": "void FPDFPage_InsertObject(FPDF_PAGE, FPDF_PAGEOBJECT)",
               "struct FPDF_LIBRARY_CONFIG": "{ int; const char**; void*; unsigned int; void*; FPDF_RENDERER_TYPE }",
               "const FPDF_ERR_PASSWORD": "4"}
        new = {"func FPDFCatalog_SetLanguage": "FPDF_BOOL FPDFCatalog_SetLanguage(FPDF_DOCUMENT, FPDF_WIDESTRING)",
               "func FPDFPage_InsertObject": "FPDF_BOOL FPDFPage_InsertObject(FPDF_PAGE, FPDF_PAGEOBJECT)",
               "struct FPDF_LIBRARY_CONFIG": "{ int; const char**; void*; unsigned int; void*; FPDF_RENDERER_TYPE; FPDF_FONT_BACKEND_TYPE }",
               "const FPDF_ERR_PASSWORD": "4",
               "func FPDFText_SetPositions": "FPDF_BOOL FPDFText_SetPositions(FPDF_PAGEOBJECT)"}
        breaks, additions = abi.classify(old, new)
        self.assertEqual(len(breaks), 2)
        self.assertTrue(any("FPDFCatalog_SetLanguage" in b for b in breaks))
        self.assertTrue(any("FPDFPage_InsertObject" in b for b in breaks))
        self.assertTrue(any("extended versioned struct FPDF_LIBRARY_CONFIG" in a for a in additions))
        self.assertTrue(any("FPDFText_SetPositions" in a for a in additions))

    def test_removal_and_constant_change_break(self):
        breaks, _ = abi.classify({"func FPDF_X": "void FPDF_X(void)", "const FPDF_Y": "1"}, {"const FPDF_Y": "2"})
        self.assertEqual(len(breaks), 2)

    def test_unversioned_struct_growth_breaks(self):
        breaks, _ = abi.classify({"struct S": "{ FPDF_DWORD; int }"}, {"struct S": "{ FPDF_DWORD; int; int }"})
        self.assertEqual(len(breaks), 1)


if __name__ == "__main__":
    unittest.main()
