# Releasing

Published releases and their verification records: [docs/releases/](releases/) (first:
[155.8059.1](releases/155.8059.1.md)).

Releases use [vl-release](https://github.com/valkyrianlabs/vl-release) (`vlr`); `release.toml` is the
contract, `.github/workflows/release.yml` the CI transaction. This page covers what is specific to
PDFium.

## Versions

`VERSION` is `<Chromium milestone>.<Chromium branch>.<n>`:

- `155.8059.0` packages PDFium from `chromium/8059` (Chrome 155).
- `n` counts our releases of that upstream branch: packaging changes and newer cherry-picks from
  the same branch (`vlr cut patch`).
- A new upstream branch resets it: `vlr version set 156.8078.0` (`tools/upstream-candidate` does
  this).

The Debian version is `VERSION-1`. The exact upstream inputs are in `sources.lock.json`, which the
runtime package installs as `/usr/share/doc/libpdfium<so>/sources.lock.json`
(`release.toml` checks it is byte-identical).

The ABI is versioned separately: `abi/soversion` is the SONAME (`libpdfium.so.8059`) and names the
runtime package (`libpdfium8059`). It changes only when the API breaks (`docs/MAINTENANCE.md`),
never just because upstream moved.

**Legacy packages.** The legacy `libpdfium-dev`/`libpdfium-2025` 20250629-1 were removed from
apt.vaulthalla.sh before the first release, because `20250629-1` sorts above every
`<milestone>.<branch>.<n>` version. A machine that still has the legacy `libpdfium-dev` installed
needs one explicit install: `apt install --allow-downgrades libpdfium-dev=155.8059.0-1`. The
legacy runtime `libpdfium-2025` can stay installed next to `libpdfium8059` until nothing uses it.

## Packages and artifacts

| Artifact | Contents |
|---|---|
| `libpdfium<so>_V-1_amd64.deb` | `libpdfium.so.<so>`, copyright, `sources.lock.json` |
| `libpdfium-dev_V-1_amd64.deb` | `/usr/include/pdfium` (+ `public -> .`), `libpdfium.so`, `pdfium.pc`, CMake `PdfiumConfig*.cmake` |
| `libpdfium_V.orig.tar.xz` | this repository's tracked files (prepared release docs), without `debian/` |
| `libpdfium_V.orig-upstream.tar.xz` | `upstream/` as prepared: pinned PDFium + DEPS files (+ patches) |
| `libpdfium_V-1.debian.tar.xz`, `.dsc` | Debian source package (3.0 quilt, unsigned) |
| `libpdfium_V-1.provenance.json` | lock, patch hashes, repository commit, artifact sha256s |
| `.changes`, `.buildinfo`, `SHA256SUMS` | build metadata and checksums |

Rebuild anywhere with stock tools: `dpkg-source -x libpdfium_V-1.dsc && cd libpdfium-V &&
apt-get build-dep . && dpkg-buildpackage -b` (offline; produces the same package contents).
`tools/pdfium-dist verify-snapshot DIR` checks every upstream file in the source package against
the pinned git trees.

Binary packages never contain upstream test assets (documents, fonts): `release.toml`
forbids them and the package tests check it. Standalone test font binaries are not even part of
the source package (`upstream.toml` `export_exclude`).

Licensing: BSD-3-Clause covers this repository's own files only (`LICENSE`); PDFium and every
third-party component keep their licenses, recorded in `debian/copyright` with full texts for
what the binaries contain (PDFium BSD, AGG, OpenJPEG BSD-2, FreeType FTL, fast_float MIT,
dragonbox BSL-1.0).

Consumers: `pkg-config pdfium` gives `-I/usr/include/pdfium -lpdfium`, so both
`#include <pdfium/fpdfview.h>` (Vaulthalla) and `#include <fpdfview.h>` work; CMake consumers use
`find_package(Pdfium)` and `Pdfium::pdfium`. Packages target Ubuntu 24.04 (noble), amd64.

## Cutting a release

```sh
vlr status                       # staged docs, version
tools/release-rehearsal          # the whole pipeline locally, nothing published
vlr cut patch --push             # or: vlr cut 156.8078.0 --push
```

The tag push starts `release.yml`:

1. **release-check**: `vlr check --release`, unit tests.
2. **release-artifacts**: `vlr prepare`; `vlr build-deb` (its `pre_build` runs
   `pdfium-dist prepare` and `snapshot`; the package build compiles PDFium **once** and runs the
   integration tests); `vlr checksums`, `vlr validate-artifacts`, `verify-snapshot`; package tests
   in clean containers (`tests/package/run`: clean install, consumers, integration against the
   installed library, upgrade/removal, legacy transition). Optional dispatch input
   `verify_rebuild` adds the rebuild-from-source comparison (a second compile).
3. **publish-debian** (environment `Production`): `vlr publish-deb --require-enabled` uploads to
   Nexus and verifies the APT index by sha256. Requires `RELEASE_PUBLISH_MODE=nexus` (repository
   variable), `NEXUS_APT_REPO`, `NEXUS_USER`, `NEXUS_PASSWORD`.
4. **github-release**: `vlr github-release` attaches all artifacts.
5. **finalize**: `vlr finalize` commits the promoted `debian/changelog` and `RELEASE_NOTES.md`.

A dry run without publishing: run the workflow by hand with `publish: dry-run`.

`release.toml [publish.apt]` targets apt.vaulthalla.sh only (where Vaulthalla installs
`libpdfium-dev` from). Do not publish to apt.valkyrianlabs.com.

## Publication checklist (first release)

- [x] Legacy packages removed from apt.vaulthalla.sh.
- [ ] Repository `vaulthalla/pdfium` with the `Production` environment, `RELEASE_PUBLISH_MODE`,
      Nexus secrets/variables, and (for finalize on a protected `main`) `RELEASE_FINALIZE_TOKEN`.
- [ ] "Allow GitHub Actions to create and approve pull requests" for `upstream.yml`.
- [ ] Rebuild Vaulthalla against the new `libpdfium-dev` (its runtime dependency becomes
      `libpdfium8059`).
