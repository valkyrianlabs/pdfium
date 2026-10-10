#!/usr/bin/env bash
# Rebuild the release from its source package alone, in a clean stock container (run as root by
# tests/package/run): verify and unpack the .dsc, install its Build-Depends from the Ubuntu
# archive, build without network access, and compare the result with the released .debs.
#
#   tests/package/rebuild-from-source.sh DEB_DIR
set -euo pipefail
deb_dir=$(realpath "$1")
version=$(tr -d '[:space:]' < "$(dirname "$0")/../../VERSION")
work=$(mktemp -d)
export DEBIAN_FRONTEND=noninteractive
arch=$(dpkg --print-architecture)
case "$arch" in
  amd64) multiarch=x86_64-linux-gnu ;;
  arm64) multiarch=aarch64-linux-gnu ;;
  *) echo "unsupported architecture $arch" >&2; exit 2 ;;
esac
step() { printf '\n=== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
rm -f /etc/dpkg/dpkg.cfg.d/excludes
# Contents of an unpacked package: type, path, symlink target and sha256 of every member.
manifest() {
  (cd "$1" && find . -printf '%y %p %l\n' | sort && find . -type f -exec sha256sum {} + | sort -k2)
}

dsc=$(ls "$deb_dir"/libpdfium_"${version}"-*.dsc)
step "unpack $(basename "$dsc")"
apt-get update -qq
apt-get install -y -qq --no-install-recommends dpkg-dev ca-certificates >/dev/null
dpkg-source -x "$dsc" "$work/src" | tail -3

step "install Build-Depends from the Ubuntu archive"
apt-get build-dep -y -qq "$work/src" >/dev/null
gcc-14 --version | head -1

step "build without network access"
cd "$work/src"
nproc_jobs=${REBUILD_JOBS:-4}
if unshare --net true 2>/dev/null; then
  unshare --net env DEB_BUILD_OPTIONS="parallel=${nproc_jobs}" dpkg-buildpackage -us -uc -b > "$work/build.log" 2>&1 \
    || { tail -40 "$work/build.log"; fail "offline rebuild failed"; }
  echo "built in a network namespace without connectivity"
else
  DEB_BUILD_OPTIONS="parallel=${nproc_jobs}" dpkg-buildpackage -us -uc -b > "$work/build.log" 2>&1 \
    || { tail -40 "$work/build.log"; fail "rebuild failed"; }
  echo "built (no network namespace available in this container; the build ran with an empty"
  echo "upstream git cache, so 'pdfium-dist prepare --offline' could not have fetched anything)"
fi
grep -E '^ *1/1 integration' "$work/build.log" || true

step "compare with the released packages"
identical=0
total=0
for rebuilt in "$work"/*.deb; do
  name=$(basename "$rebuilt")
  released="$deb_dir/$name"
  [ -f "$released" ] || fail "rebuild produced $name, which was not released"
  total=$((total + 1))
  if cmp -s "$rebuilt" "$released"; then
    identical=$((identical + 1))
    echo "identical   $name"
    continue
  fi
  mkdir -p "$work/a/$name" "$work/b/$name"
  dpkg-deb -x "$rebuilt" "$work/a/$name"
  dpkg-deb -x "$released" "$work/b/$name"
  if [ "$(manifest "$work/a/$name")" = "$(manifest "$work/b/$name")" ]; then
    echo "same files  $name (only package metadata differs)"
  else
    echo "DIFFERENT   $name:"
    diff <(manifest "$work/a/$name") <(manifest "$work/b/$name") | head -10 || true
  fi
done
echo "$identical/$total packages byte-identical to the release"
[ "$total" -gt 0 ] || fail "nothing was rebuilt"
printf '\nrebuild-from-source: done\n'
