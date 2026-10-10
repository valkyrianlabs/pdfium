# Maintaining Vaulthalla PDFium

This is the procedure for moving to a newer upstream PDFium, plus the reference for the tools
involved. Anything here can be done by hand or by an agent; every step reports what it found.

## How upstream publishes PDFium

PDFium has no release tags or SemVer. Chromium cuts a `chromium/<build>` branch per milestone
(`chromium/8059` = Chrome 155) and cherry-picks fixes onto it; Chrome ships the head of that branch.
The Chromium Dashboard (`fetch_releases`) reports the exact PDFium commit each Chrome release ships.

**Tracking policy** (`upstream.toml [tracking]`): adopt the PDFium commit Chrome **Stable** ships on
**Linux**. `tools/pdfium-dist discover` reads it from the dashboard and checks on googlesource.com
that it belongs to the named release branch. Nothing is adopted or released automatically.

## The pins

`sources.lock.json` is the single record of upstream inputs, written only by
`tools/pdfium-dist update`:

- `pdfium`: repository, commit, git tree id, commit date and subject, branch, Chromium version,
  milestone.
- `dependencies`: the DEPS entries `upstream.toml [[dependency]]` asks for (Chromium `build/`
  headers, fast_float, dragonbox). Their revisions come from **that PDFium revision's DEPS**, so
  they move with upstream. Each records commit, tree id, and sha256 of every exported file.

`tools/pdfium-dist prepare` fetches exactly those commits (shallow, by id, into
`~/.cache/vaulthalla-pdfium/git` or `$PDFIUM_CACHE_DIR`), verifies the tree ids and file hashes,
exports them into `upstream/` where gclient would have put them (minus `upstream.toml`
`export_exclude`: standalone test font binaries we never use), applies `patches/series` (empty
today), and writes `upstream/.prepared.json`. `verify` re-checks everything; Meson warns if the
stamp is missing.

## Updating to a newer upstream

```sh
CI_CACHE_NAMES="ccache pdfium-git" PDFIUM_CACHE_DIR=/cache/pdfium-git \
  ./ci/run-ci tools/upstream-candidate            # or: --commit SHA --branch chromium/N \
                                                  #     --chromium-version V --milestone M
cat build/upstream-candidate/report.md
```

The weekly `upstream.yml` workflow does the same and opens a pull request with the report.
`upstream-candidate` pins the revision, sets `VERSION` (see `docs/RELEASING.md`), prepares the
sources, runs the static audit, builds, runs the integration tests, the full audit and the ABI
check, and stages templated release docs. Then work through the report:

### 1. Reconcile audit findings (`DRIFT` lines)

`tools/pdfium-dist audit` reads upstream's `BUILD.gn` files statically (`tools/pdfiumdist/gn.py`;
GN is never run), walks the `//:pdfium` graph under the build arguments in
`upstream.toml [audit.args]`, and compares it with the Meson build. Without `--builddir` it reads
`meson/**/meson.build` directly (so it works when a moved file breaks `meson setup`); with
`--builddir` it also compares compile defines and installed headers from Meson's introspection.

| Finding | Fix |
|---|---|
| `[sources] X is compiled upstream but not by C` | add it to the component's `'sources'` in `meson/<dir>/meson.build` |
| `[sources] C compiles X, which upstream no longer lists — moved to Y?` | remove/move it |
| `[targets] //a:b compiles N files but has no Meson component` | add a component dict to `meson/a/meson.build`, map it in `[audit.targets]` |
| `[targets] //a:b is no longer part of //:pdfium` | remove its component and mapping |
| `[externals] //third_party/x is a new external dependency` | decide: system package (add `dependency()` in `meson.build`, Build-Depends, Containerfile; a forwarding header in `meson/shims` if upstream has no `USE_SYSTEM_*` switch) or a DEPS export (`upstream.toml [[dependency]]`, then `update`); record it in `[audit.external]` |
| `[defines] upstream applies -DX` | add it (globally in `meson.build`, or `'cpp_args'` on the component) |
| `[defines] Meson adds -DX ...` | remove it, or justify it in `[audit.extra_defines]` |
| `[includes] F includes third_party/...` | same decision as a new external |
| `[headers] public/X exists upstream but is not installed` | add to `meson/public/meson.build`, `release.toml` contract if important |
| `[undecided] BUILD.gn:N: if (...)` | add the argument to `[audit.args]`, or to `[audit.ignore_undecided]` after checking it is irrelevant |

`REVIEW` lines compare upstream with `audit/baseline.json` (new `pdfium.gni` arguments, moved
DEPS pins of libraries we take from the system, changed per-target defines/deps). Read them; once
the update is understood, record the new facts:

```sh
./ci/run-ci python3 tools/pdfium-dist audit --builddir build/candidate --update-baseline
```

Example (M155 → M156): one finding, `core/fpdfapi/edit/cpdf_contentstream_write_utils.cpp` moved to
`core/fxcrt/stream_write_utils.cpp` (with the dragonbox dependency). Fix: three lines in
`meson/core/fxcrt` and `meson/core/fpdfapi/edit`, one line in `upstream.toml`.

### 2. Make the tests pass

`meson test -C build/candidate` runs `tests/integration.py`: fixtures (`tests/fixtures.toml`),
malformed inputs, every upstream test PDF, pixel comparison against upstream's reference images
(honouring upstream's `testing/SUPPRESSIONS`), and consistency. A pixel or text change caused by
an intentional upstream change is fine to accept by adjusting a fixture with a comment; a crash
never is. For bigger updates also run the sanitizer build (`sanitizers.yml`, or the commands in it).

### 3. ABI decision

`tools/pdfium-dist abi --library build/candidate/libpdfium.so` extracts the public API from the
preprocessed public headers (prototypes without parameter names, struct layouts, constants),
checks it against the library's exported symbols, and classifies it against `abi/public-api.txt`:

- **identical / additions** (exit 0 / 3): same SONAME. Run with `--update` to record the API and
  add the new symbols to `debian/libpdfium<so>.symbols` with the new version.
- **break** (exit 1: removed or changed function, changed constant, non-append struct change):
  move to a new SONAME named after the new Chromium branch, then record:

  ```sh
  python3 tools/pdfium-dist soname-bump 8078    # abi/soversion, debian/*, release.toml
  # rebuild, then
  python3 tools/pdfium-dist abi --library build/candidate/libpdfium.so --update
  ```

  The runtime package becomes `libpdfium8078`; old and new runtime packages are co-installable,
  and consumers (Vaulthalla) must be rebuilt. Say so in the release notes.

`python3 -m unittest discover -s tests/unit -t .` checks that package names, symbols file, ABI
baseline, release contracts, `VERSION` and the lock agree.

### 4. Release

Edit `.release/RELEASE_NOTES_NEXT.md` and `.release/CHANGELOG_NEXT.md` into a real description,
run `tools/release-rehearsal`, merge, and cut (`docs/RELEASING.md`).

## Patches

Prefer none: today the build needs no upstream patch. If one becomes unavoidable, put it in
`patches/` (`-p1` relative to the PDFium root) and list it in `patches/series` with a comment
saying why and when it can go. `prepare` applies it; the stamp records its hash; the source package
carries it in `upstream/` (already applied) and in `patches/`.

## Other maintenance

- Licensing: when upstream adds a third-party directory that ends up compiled (audit `externals`
  / `includes`) or in the source package, add it to `debian/copyright` with its exact notice; full
  license texts go into `debian/copyright` unless they are in `/usr/share/common-licenses`.
- ARM64: `tools/pdfium-dist audit --cpu arm64` checks the GN graph for arm64 too (see `docs/CI.md`).

- `tools/pdfium-dist cache-prune`: drop cached upstream commits the lock no longer pins.
- Packaging-only change (no upstream change): edit, stage docs, `vlr cut patch`.
- Newer cherry-picks on the same branch: `upstream-candidate` bumps the patch version.
- Changing supported features (e.g. enabling Brotli): change `[audit.args]` and the build together;
  the audit then expects the matching sources/defines.
