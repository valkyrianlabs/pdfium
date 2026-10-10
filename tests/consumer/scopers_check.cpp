// The installed C++ helpers (cpp/fpdf_scopers.h) must compile and work from /usr/include/pdfium
// with only `pkg-config --cflags pdfium`.
#include <cpp/fpdf_scopers.h>
#include <fpdf_text.h>
#include <fpdfview.h>

#include <cstdio>

int main(int argc, char** argv) {
  if (argc != 2) {
    std::fprintf(stderr, "usage: %s FILE.pdf\n", argv[0]);
    return 2;
  }
  FPDF_InitLibrary();
  int chars = -1;
  {
    ScopedFPDFDocument document(FPDF_LoadDocument(argv[1], nullptr));
    if (document) {
      ScopedFPDFPage page(FPDF_LoadPage(document.get(), 0));
      ScopedFPDFTextPage text(page ? FPDFText_LoadPage(page.get()) : nullptr);
      chars = text ? FPDFText_CountChars(text.get()) : -1;
    }
  }
  FPDF_DestroyLibrary();
  std::printf("chars=%d\n", chars);
  return chars > 0 ? 0 : 1;
}
