- Build PDFium natively with Meson + Ninja from the official googlesource.com repository
  (chromium/8059, Chromium 155.0.8059.39); no CMake, GN, gclient or depot_tools.
- Rename the runtime package to libpdfium8059 (SONAME libpdfium.so.8059) for the new ABI.
- Export only the public C API (linker version script) and ship a symbols file.
- Use system abseil, FreeType, HarfBuzz, ICU, lcms2, libjpeg and zlib; keep PDFium's bundled,
  patched AGG and OpenJPEG.
- Include all predefined CJK CMaps (the legacy build disabled them).
- Install fpdf_thumbnail.h and make the C++ helper headers usable from /usr/include/pdfium.
- pdfium.pc adds -I${includedir}/pdfium; drop the -dev package's unneeded dependencies on other
  libraries' -dev packages.
- Ship the upstream source lock (sources.lock.json) in the runtime package documentation.
- Rewrite debian/copyright: exact PDFium, OpenJPEG, AGG, FreeType (FTL), fast_float and dragonbox
  notices with full license texts; test fonts are excluded from the source package and no test
  assets are installed.
- Run the integration tests (rendering, text extraction, malformed input) during the build.
