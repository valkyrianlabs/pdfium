#!/usr/bin/env bash
# Transition from the legacy packages (libpdfium-2025 / libpdfium-dev 20250629-1) to these
# packages, and a differential comparison of both builds (run as root by tests/package/run).
# The legacy .debs come from LEGACY_DEB_DIR (a directory inside the repository, e.g. a copy of
# the last legacy build) or, failing that, from apt.vaulthalla.sh (read-only); when neither has
# them the suite is skipped. Nothing is published.
#
#   [LEGACY_DEB_DIR=DIR] tests/package/legacy-transition.sh DEB_DIR
set -euo pipefail
deb_dir=$(realpath "$1")
repo=$(cd "$(dirname "$0")/../.." && pwd)
version=$(tr -d '[:space:]' < "$repo/VERSION")
soversion=$(tr -d '[:space:]' < "$repo/abi/soversion")
work=$(mktemp -d)
# Reports never go into DEB_DIR: release/ holds exactly the checksummed release artifacts.
reports=${REPORT_DIR:-$repo/build/package-tests}
mkdir -p "$reports"
export DEBIAN_FRONTEND=noninteractive
arch=$(dpkg --print-architecture)
case "$arch" in
  amd64) multiarch=x86_64-linux-gnu ;;
  arm64) multiarch=aarch64-linux-gnu ;;
  *) echo "unsupported architecture $arch" >&2; exit 2 ;;
esac
step() { printf '\n=== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
# Stock container images are minimized (dpkg skips /usr/share/doc); behave like a normal system.
rm -f /etc/dpkg/dpkg.cfg.d/excludes

step "install the legacy packages"
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg build-essential pkgconf python3 >/dev/null
[ -n "${LEGACY_DEB_DIR:-}" ] && LEGACY_DEB_DIR=$(realpath "$LEGACY_DEB_DIR")  # apt needs a path, not pkg/release
if [ -n "${LEGACY_DEB_DIR:-}" ] && ls "$LEGACY_DEB_DIR"/libpdfium-dev_20250629-1_${arch}.deb >/dev/null 2>&1; then
  echo "from $LEGACY_DEB_DIR"
  apt-get install -y -qq --no-install-recommends "$LEGACY_DEB_DIR"/libpdfium-2025_20250629-1_${arch}.deb \
    "$LEGACY_DEB_DIR"/libpdfium-dev_20250629-1_${arch}.deb >/dev/null
else
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://apt.vaulthalla.sh/pubkey.gpg | gpg --dearmor -o /etc/apt/keyrings/vaulthalla.gpg
  echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/vaulthalla.gpg] https://apt.vaulthalla.sh stable main" \
    > /etc/apt/sources.list.d/vaulthalla.list
  apt-get update -qq
  if ! apt-cache show libpdfium-dev=20250629-1 >/dev/null 2>&1; then
    echo "SKIP: no legacy packages (set LEGACY_DEB_DIR; apt.vaulthalla.sh no longer publishes them)"
    exit 0
  fi
  echo "from apt.vaulthalla.sh"
  apt-get install -y -qq --no-install-recommends libpdfium-dev=20250629-1 >/dev/null
fi
echo "legacy: $(dpkg-query -W -f '${Package} ${Version}\n' libpdfium-2025 libpdfium-dev | tr '\n' ' ')"
gcc -O2 -o "$work/probe-legacy" "$repo/tests/consumer/pdfium_probe.c" $(pkg-config --cflags --libs pdfium) -I/usr/include/pdfium

step "install the new packages over them"
# 20250629-1 sorts above every <milestone>.<branch>.<n> version, so a machine that still has the
# legacy -dev package installed needs an explicit install (--allow-downgrades) once.
apt-get install -y -qq --allow-downgrades "$deb_dir/libpdfium${soversion}_${version}-"*_${arch}.deb \
  "$deb_dir/libpdfium-dev_${version}-"*_${arch}.deb >/dev/null
[ "$(dpkg-query -W -f '${Version}' libpdfium-dev)" = "${version}-1" ] || fail "libpdfium-dev was not replaced"
dpkg -s libpdfium-2025 | grep -q '^Status: install ok installed' || fail "legacy runtime was removed"
[ -z "$(find /usr/lib/${multiarch}/cmake/pdfium -name 'PdfiumTargets*' 2>/dev/null)" ] || fail "stale legacy CMake files"
"$work/probe-legacy" "$repo/upstream/pdfium/testing/resources/hello_world.pdf" | grep -q '"loaded":true' \
  || fail "a binary built against the legacy package stopped working"
echo "libpdfium-dev replaced cleanly; libpdfium-2025 stays co-installed and its consumers keep working"
gcc -O2 -o "$work/probe" "$repo/tests/consumer/pdfium_probe.c" $(pkg-config --cflags --libs pdfium)

step "differential comparison with the legacy build (same consumer, same documents)"
python3 "$repo/tests/integration.py" --probe "$work/probe" --reference-probe "$work/probe-legacy" \
  --reference-label "legacy 20250629-1" \
  --reference-known "vertical_text.pdf=the legacy build compiled out the CJK CMaps and renders this page blank" \
  --strict-reference --resources "$repo/upstream/pdfium/testing/resources" \
  --work "$work/integration" --checks fixtures,reference --report "$reports/legacy-comparison.json" | tail -4

step "apt autoremove after the transition"
apt-get purge -y -qq libpdfium-2025 >/dev/null
"$work/probe" "$repo/upstream/pdfium/testing/resources/hello_world.pdf" | grep -q '"loaded":true' || fail "new consumer broke"
printf '\nlegacy-transition: all checks passed\n'
