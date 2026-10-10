# CI and build resources

The workflows run on the shared self-hosted VPS runner (`[self-hosted, Linux, X64, vaulthalla,
vps-ci]`, user `gh-vaulthalla`, in the `ci.slice` pool it shares with the ValkyrianLabs runner).
Nothing runs on the host directly:

- Every step runs in a disposable rootless Podman container: `ci/run-ci` (copied verbatim from
  Vaulthalla; keep it in sync there) with this repository's `ci/Containerfile` (Ubuntu 24.04 pinned
  by digest, the Debian Build-Depends, g++ 14, Debian tooling, gh, vl-release). No host compilers,
  Python packages or system configuration are touched.
- Package tests run in stock `ubuntu:24.04` containers (`ci/run-ci --image ... --root`).

## Bounds

| Control | Default | Where |
|---|---|---|
| Compile parallelism | `PDFIUM_JOBS=4` (`ninja -j`, `DEB_BUILD_OPTIONS=parallel=4`, LTO jobs) | workflow `env`, `debian/rules` |
| Container memory | `CI_MEMORY=8g` (10g for sanitizers) | `ci/run-ci --memory` |
| One heavy PDFium build per runner | `flock ~/.cache/pdfium-ci/heavy-build.lock`, waits up to 90 min | `tools/ci-guard` |
| Free disk before heavy steps | 8 GiB at work tree, caches, Podman storage | `tools/ci-guard` (`PDFIUM_MIN_FREE_GIB`) |
| Priority | `nice 10`, `ionice -c2 -n7` | `tools/ci-guard` |
| Workflow concurrency | per ref (CI cancels superseded runs; releases queue) | workflow `concurrency` |
| Job timeouts | 15 / 60 / 90 / 120 min | workflows |

Measured on the dev machine (i9-13900H): a full native build at `-j8` takes about 66 s; the whole
release pipeline (`build-deb` with LTO and tests, snapshot, validation) about 2 min; the rehearsal
with all package tests about 5 min. `build-deb` reports the container's peak memory
(`memory.peak`) in the workflow log; check it on the first VPS runs and tune `PDFIUM_JOBS` /
`CI_MEMORY`, because the VPS's CPU and memory could not be inspected from here.

## Caches (all persistent, all bounded)

| Cache | Location on the runner | Bound |
|---|---|---|
| ccache | `~/.cache/pdfium-ci/ccache` → `/cache/ccache` | `CCACHE_MAXSIZE=5G` |
| Upstream git objects | `~/.cache/pdfium-ci/pdfium-git` → `/cache/pdfium-git` | pinned commits only after `tools/pdfium-dist cache-prune` (~25 MB per revision) |
| CI image | `localhost/pdfium-ci:<hash>` | `ci/run-ci` removes superseded tags |

Workspaces are cleaned by `actions/checkout`; build trees live in the ignored `build/` and
`release/` of the checkout.

Known hazard of the shared `ci/run-ci`: when a new vl-release/pmdocs version appears it rebuilds
the image and force-removes older tags, which kills containers still running from them (observed
once locally). Fixing it belongs in the shared script (e.g. not removing images in use).

## Workflows

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | push to main, PRs from this repository | unit tests, `vlr check`, source verification; guarded build, integration tests, audit, ABI |
| `release.yml` | `v*` tag, manual | `docs/RELEASING.md` |
| `upstream.yml` | weekly, manual | discover; candidate build/test; pull request with the report |
| `sanitizers.yml` | monthly, manual | ASan + UBSan build over all test documents |

Fork pull requests never run on the self-hosted runner.

## ARM64 (Raspberry Pi 5 node)

amd64 is the release target today. The build itself is architecture-neutral, which
`tools/pdfium-dist audit --cpu arm64` confirms for the pinned revision (upstream's BUILD.gn yields
the same sources and defines; SIMD paths such as OpenJPEG's SSE/NEON are chosen by the
preprocessor). `ci/Containerfile` uses a multi-arch base and the host architecture for its APT
sources; the package tests derive the architecture and multiarch triplet; the release contracts
use `usr/lib/*/` paths. What an arm64 release still needs:

1. A runner label for the node and an arm64 variant of the build/test jobs (`runs-on`).
2. vl-release contracts are per package name with one `architecture`: either a vl-release feature
   for several architectures per contract, or a per-architecture release configuration.
3. `[publish.apt] architectures = ["amd64", "arm64"]` once arm64 packages exist.
4. A first native arm64 build + `meson test` on the node (expect identical results; the pixel
   references are architecture-independent for AGG, but verify).
