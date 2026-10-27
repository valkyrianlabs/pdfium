# Vaulthalla PDFium release notes

<!-- vl-release:entry version=156.8078.1 -->
## 156.8078.1 — PDFium from Chromium 156

_Released 2026-10-27_

Updates PDFium to `chromium/8078` (Chromium 156.0.8078.47, the revision Chrome Stable ships), commit
`2f22dfe030de`.

### Valkyrian Labs APT repository

The packages are now published to `apt.valkyrianlabs.com`, the repository that also serves Vaulthalla and the
libpqxx SDK, signed by the Valkyrian Labs LLC key (`494D 71A1 EEDD F87B D48E  5048 6D64 CB7B DFC3 0EC6`).
`apt.vaulthalla.sh` serves the same index, so existing sources keep working:

```sh
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://apt.valkyrianlabs.com/pubkey.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/valkyrianlabs.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/valkyrianlabs.gpg] https://apt.valkyrianlabs.com stable main" \
  | sudo tee /etc/apt/sources.list.d/valkyrianlabs.list
sudo apt update && sudo apt install libpdfium-dev
```

<!-- vl-release:entry version=155.8059.1 -->
## 155.8059.1 — Native Meson build of current PDFium

_Released 2026-10-10_

The first release of Vaulthalla's PDFium distribution. It replaces the legacy CMake-based
`libpdfium` packages (a 2025-06-29 snapshot) with PDFium from Chrome Stable, built natively with
Meson + Ninja from the official googlesource.com repository. No CMake, GN, gclient or
depot_tools is involved.

### Install (Ubuntu 24.04, amd64)

```sh
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://apt.vaulthalla.sh/pubkey.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/vaulthalla.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/vaulthalla.gpg] https://apt.vaulthalla.sh stable main" \
  | sudo tee /etc/apt/sources.list.d/vaulthalla.list
sudo apt update
sudo apt install libpdfium-dev        # pulls in libpdfium8059
```

Build against it with `pkg-config pdfium` (`-I/usr/include/pdfium -lpdfium`): both
`#include <pdfium/fpdfview.h>` and `#include <fpdfview.h>` work. CMake consumers can use
`find_package(Pdfium)` and the `Pdfium::pdfium` target.

### What you get

- **PDFium from Chromium 155**: commit `bc8e654f49c9` on `chromium/8059`, the revision Chrome
  Stable 155.0.8059.39 ships on Linux. Every upstream input (PDFium, Chromium build headers,
  fast_float, dragonbox) is pinned by commit and checksum in
  `/usr/share/doc/libpdfium8059/sources.lock.json`.
- **Packages:** `libpdfium8059` (shared library `libpdfium.so.8059`) and `libpdfium-dev`
  (headers in `/usr/include/pdfium`, `pdfium.pc`, CMake package files).
- **Headless, server-oriented build:** AGG renderer; no V8 JavaScript, no XFA, no Skia, no Rust
  codecs. System FreeType, HarfBuzz, ICU, lcms2, libjpeg, zlib and abseil; PDFium's own patched
  AGG and OpenJPEG. All predefined CJK CMaps are included, so CJK documents render and extract
  text (the legacy build rendered them blank).
- **A clean ABI:** the library exports exactly the public C API (466 functions) and ships a
  Debian symbols file; the legacy build also exported libstdc++ internals.
- **Hardened for untrusted documents:** pattern-initialized locals and libstdc++ assertions, plus
  Ubuntu's standard hardening flags.
- **Reproducible and traceable:** the release carries a Debian source package and a provenance
  record; rebuilding the source package produces byte-identical `.deb` files.

### Verification

Before publication every release is built once in CI and must pass: the integration tests
(real documents for text, rendering, CJK and right-to-left text, JPEG 2000, encryption, forms;
malformed inputs; all of upstream's test PDFs without a crash; page renders compared with
upstream's own reference images), source provenance against the pinned upstream commits, the
Debian package contracts, and installation into a clean Ubuntu 24.04 container with independent
C, C++ and CMake consumers.

### Compatibility

- The runtime package is renamed (`libpdfium-2025` → `libpdfium8059`) because the ABI changed:
  rebuild consumers against `libpdfium-dev`. Both runtime packages can be installed side by side.
- Upstream API changes since the legacy snapshot include `FPDFCatalog_SetLanguage` (now takes an
  `FPDF_WIDESTRING`) and `FPDFPage_InsertObject` (now returns `FPDF_BOOL`).
- A machine that still has the legacy `libpdfium-dev 20250629-1` installed needs one explicit
  install: `sudo apt install --allow-downgrades libpdfium-dev=155.8059.0-1`.

### Limitations

- Ubuntu 24.04 (noble) on amd64 only; arm64 is planned.
- No JavaScript, XFA forms or Skia rendering in this configuration.
