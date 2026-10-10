<!-- vl-release:generated-local -- regenerate with `vlr install-local-skill` after changing release.toml; local edits are overwritten -->
# Vaulthalla PDFium: release specifics

Generated from this repository's `release.toml`. The general workflow is in `SKILL.md`;
this file says how it applies here.

## Files

| Purpose | Path |
|---|---|
| Release contract | `release.toml` |
| Staged changelog (keep current) | `.release/CHANGELOG_NEXT.md` |
| Staged release notes (keep current) | `.release/RELEASE_NOTES_NEXT.md` |
| Published release notes (do not edit) | `RELEASE_NOTES.md` |
| Published Debian changelog (do not edit) | `debian/changelog` |

## Versions

- Canonical: `VERSION`
- Kept in sync by `vlr version …`: no other files
- Tags: `vX.Y.Z` on branch `main`; GitHub release title: `vX.Y.Z — <title>`

## Release channels

- **Debian packages** (built by `vlr build-deb` from `debian/`, revision 1):
  - `libpdfium8059`: architecture `amd64`; must ship `usr/lib/x86_64-linux-gnu/libpdfium.so.8059`, `usr/lib/x86_64-linux-gnu/libpdfium.so.8059.0.0`, `usr/share/doc/libpdfium8059/copyright`, `usr/share/doc/libpdfium8059/sources.lock.json`; must never ship `usr/include/*`, `usr/lib/x86_64-linux-gnu/libpdfium.so`, `usr/lib/x86_64-linux-gnu/pkgconfig/*`, `usr/lib/x86_64-linux-gnu/*.a`; ships byte-identical copies of `sources.lock.json`
  - `libpdfium-dev`: architecture `amd64`; must ship `usr/include/pdfium/fpdfview.h`, `usr/include/pdfium/fpdf_text.h`, `usr/include/pdfium/fpdf_thumbnail.h`, `usr/include/pdfium/cpp/fpdf_scopers.h`, `usr/include/pdfium/public`, `usr/lib/x86_64-linux-gnu/libpdfium.so`, `usr/lib/x86_64-linux-gnu/pkgconfig/pdfium.pc`, `usr/lib/x86_64-linux-gnu/cmake/pdfium/PdfiumConfig.cmake`, `usr/lib/x86_64-linux-gnu/cmake/pdfium/PdfiumConfigVersion.cmake`, `usr/share/doc/libpdfium-dev/copyright`; must never ship `usr/lib/x86_64-linux-gnu/libpdfium.so.*`, `usr/lib/x86_64-linux-gnu/*.a`, `usr/include/pdfium/*.cpp`
  - pre-build: `python3 tools/pdfium-dist prepare`; `python3 tools/pdfium-dist snapshot --output build/source`
- **APT**: https://apt.vaulthalla.sh (suite `stable`, components main), published by release CI with `vlr publish-deb`

## Before considering substantial work complete

```sh
vlr check
python3 -m unittest discover -s tests/unit -t .
```
