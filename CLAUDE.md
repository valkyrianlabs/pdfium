# Vaulthalla PDFium — notes for agents

Native Meson + Ninja build and Debian packaging of upstream PDFium. **Never** introduce CMake,
GN, gclient, depot_tools or Chromium build scripts into the build, and never commit upstream
sources (they live in the ignored `upstream/`, exported from `sources.lock.json`).

- Overview and layout: `README.md`.
- Updating upstream (the usual task): `docs/MAINTENANCE.md`. Start with
  `./ci/run-ci tools/upstream-candidate` and work through its report.
- Releases, versions, artifacts, publication: `docs/RELEASING.md`; vl-release skill in
  `.claude/skills/vl-release/`.
- CI, runner resource limits, caches: `docs/CI.md`.

Rules of thumb:

- Run builds inside `./ci/run-ci` (set `CI_CACHE_NAMES="ccache pdfium-git"` and
  `PDFIUM_CACHE_DIR=/cache/pdfium-git`); do not install packages on the host.
- `meson/**/meson.build` are data files mirroring upstream `BUILD.gn`; keep them that way. The
  audit (`tools/pdfium-dist audit`) must report zero drift before a release.
- Never edit `sources.lock.json`, `abi/public-api.txt`, `debian/libpdfium*.symbols`,
  `debian/changelog` or `RELEASE_NOTES.md` by hand; use `pdfium-dist update`, `abi --update`
  and vl-release.
- Keep `.release/*_NEXT.md` current with material changes.
- Before finishing: `python3 -m unittest discover -s tests/unit -t .`, `vlr check`, and for build
  changes `meson test` plus `tools/release-rehearsal`.
- Do not publish, push tags or touch Nexus without the maintainer's approval.
