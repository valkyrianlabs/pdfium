# Mirrors //:pdfium_public_headers_impl (BUILD.gn): the installed public API, under
# <includedir>/pdfium. Upstream's list omits public/fpdf_thumbnail.h although the library exports
# its functions (fpdfsdk/fpdf_thumbnail.cpp); it is installed too (`audit` checks public/ itself).
pdfium_public_headers = [
  'fpdf_annot.h',
  'fpdf_attachment.h',
  'fpdf_catalog.h',
  'fpdf_dataavail.h',
  'fpdf_doc.h',
  'fpdf_edit.h',
  'fpdf_ext.h',
  'fpdf_flatten.h',
  'fpdf_formfill.h',
  'fpdf_fwlevent.h',
  'fpdf_javascript.h',
  'fpdf_ppo.h',
  'fpdf_progressive.h',
  'fpdf_save.h',
  'fpdf_searchex.h',
  'fpdf_signature.h',
  'fpdf_structtree.h',
  'fpdf_sysfontinfo.h',
  'fpdf_text.h',
  'fpdf_thumbnail.h',
  'fpdf_transformpage.h',
  'fpdfview.h',
]
# The C++ helpers include "public/cpp/fpdf_deleters.h" and "public/fpdfview.h" (paths relative to
# PDFium's root); the `public` -> `.` symlink installed by meson.build resolves them below
# <includedir>/pdfium.
pdfium_public_cpp_headers = [
  'cpp/fpdf_deleters.h',
  'cpp/fpdf_scopers.h',
]
