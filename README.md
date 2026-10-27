# Vaulthalla PDFium

Native **Meson + Ninja** build and Debian packaging of upstream [PDFium](https://pdfium.googlesource.com/pdfium),
tracking the revision Chrome Stable ships. No CMake, GN, gclient, depot_tools or Chromium tooling.

Upstream sources are never committed: `sources.lock.json` pins every input (commits + git tree ids +
file sha256s) and `tools/pdfium-dist prepare` exports them into the ignored `upstream/`.

## Layout

| Path | What |
|---|---|
| `upstream.toml` | tracking policy, DEPS deps we take, audit profile (GN args, target mapping) |
| `sources.lock.json` | resolved pins (written by `pdfium-dist update`) |
| `meson.build`, `meson/**/meson.build` | the build; one file per upstream `BUILD.gn`, pure source lists |
| `meson/shims/` | forwarding headers for system abseil |
| `abi/` | `soversion` (SONAME) and `public-api.txt` (API baseline) |
| `audit/baseline.json` | upstream facts last reviewed |
| `tools/pdfium-dist` | `discover`, `update`, `prepare`, `verify`, `audit`, `abi`, `soname-bump`, `snapshot`, `verify-snapshot`, `cache-prune` |
| `tools/upstream-candidate` | adopt a newer upstream as a candidate and write a report |
| `tools/release-rehearsal` | run the full release pipeline locally, no publishing |
| `tools/ci-guard` | runner protection: one heavy build at a time, disk check, low priority |
| `tools/clean` | remove disposable output (`build/`, `release/`; `--all` also `upstream/` + cache prune) |
| `tests/` | integration driver + probe consumer, package tests, tooling unit tests |
| `debian/`, `release.toml` | packaging (`libpdfium<soversion>`, `libpdfium-dev`) and vl-release contract |
| `ci/` | `run-ci` (shared, verbatim from Vaulthalla) + `Containerfile` (Ubuntu 24.04) |

## Everyday commands (inside `./ci/run-ci …` for an isolated environment)

```sh
CI_CACHE_NAMES=ccache ./ci/run-ci bash -c '
  python3 tools/pdfium-dist prepare
  meson setup build/dev && ninja -C build/dev -j4 && meson test -C build/dev
  python3 tools/pdfium-dist audit --builddir build/dev
  python3 tools/pdfium-dist abi --library build/dev/libpdfium.so'
python3 -m unittest discover -s tests/unit -t .
tools/release-rehearsal            # prepare → build-deb → validate → package tests
```

## Updating upstream (summary)

1. `tools/upstream-candidate` (or the weekly `upstream.yml` workflow, which opens a PR and releases
   it by itself when `pdfium-dist gate` passes: docs/MAINTENANCE.md, *Auto-release*).
2. Fix every `DRIFT` line of the audit in `meson/**` / `upstream.toml`; re-run until clean.
3. ABI: additions keep the SONAME; a break triggers `soname-bump <branch>` then `abi --update`.
4. Edit `.release/*_NEXT.md`, merge, `vlr cut … --push`.

Versioning: `<milestone>.<branch>.<n>` (e.g. `155.8059.0` = Chromium 155, `chromium/8059`).

## Documentation

- [docs/MAINTENANCE.md](docs/MAINTENANCE.md): upstream tracking, the audit, ABI policy, patches.
- [docs/RELEASING.md](docs/RELEASING.md): versions, artifacts, vl-release and Nexus publication.
- [docs/CI.md](docs/CI.md): runner isolation, resource limits, caches, workflows.
