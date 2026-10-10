#!/usr/bin/env bash
# Package tests in a clean, stock Ubuntu container (run as root by tests/package/run):
# install the built .debs with apt, check metadata and installed files, build independent
# consumers against the installed library (pkg-config, Vaulthalla's include style, C++ helpers,
# CMake), run the integration tests against it, then exercise upgrade, removal and purge.
#
#   tests/package/clean-install.sh DEB_DIR
set -euo pipefail
deb_dir=$(realpath "$1")
repo=$(cd "$(dirname "$0")/../.." && pwd)
version=$(tr -d '[:space:]' < "$repo/VERSION")
soversion=$(tr -d '[:space:]' < "$repo/abi/soversion")
runtime="libpdfium${soversion}"
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
# Stock container images are minimized (dpkg skips /usr/share/doc); behave like a normal system.
rm -f /etc/dpkg/dpkg.cfg.d/excludes

step "install ${runtime} and libpdfium-dev ${version} into a clean system"
apt-get update -qq
apt-get install -y -qq --no-install-recommends "$deb_dir/${runtime}_${version}-"*_${arch}.deb >/dev/null
dpkg -s "$runtime" | grep -q '^Status: install ok installed' || fail "$runtime not installed"
ldconfig -p | grep -q "libpdfium.so.${soversion} " || fail "libpdfium.so.${soversion} not in the ldconfig cache"
apt-get install -y -qq --no-install-recommends build-essential pkgconf python3 "$deb_dir/libpdfium-dev_${version}-"*_${arch}.deb >/dev/null
readelf -d "/usr/lib/${multiarch}/libpdfium.so.${soversion}" | grep -q "SONAME.*libpdfium.so.${soversion}" \
  || fail "unexpected SONAME"
echo "installed: $(dpkg-query -W -f '${Package} ${Version}\n' "$runtime" libpdfium-dev | tr '\n' ' ')"

step "metadata"
[ "$(pkg-config --modversion pdfium)" = "$version" ] || fail "pkg-config version"
[ "$(pkg-config --cflags pdfium | xargs)" = "-I/usr/include/pdfium" ] || fail "pkg-config cflags: $(pkg-config --cflags pdfium)"
[ "$(pkg-config --libs pdfium | xargs)" = "-lpdfium" ] || fail "pkg-config libs: $(pkg-config --libs pdfium)"
cmp -s "/usr/share/doc/${runtime}/sources.lock.json" "$repo/sources.lock.json" || fail "packaged sources.lock.json differs"
exported=$(nm -D --defined-only "/usr/lib/${multiarch}/libpdfium.so.${soversion}" | awk '{print $3}' | grep -cv -E '^(FPDF|FORM_|FSDK_)' || true)
[ "$exported" = 0 ] || fail "$exported exported symbols outside the public API"
stray=$(dpkg -L "$runtime" libpdfium-dev | grep -E '\.(pdf|ttf|otf|cff)$|/testing/' || true)
[ -z "$stray" ] || fail "test assets in the packages: $stray"
echo "pkg-config, provenance, exported symbols ok; no test documents or fonts installed"

step "every public header compiles on its own (C11 and C++17)"
for header in /usr/include/pdfium/*.h; do
  echo "#include <$(basename "$header")>" > "$work/h.c"
  gcc -std=c11 -Wall -Werror -fsyntax-only $(pkg-config --cflags pdfium) "$work/h.c" || fail "C: $header"
  g++ -std=c++17 -Wall -Werror -fsyntax-only -x c++ $(pkg-config --cflags pdfium) "$work/h.c" || fail "C++: $header"
done
echo "$(ls /usr/include/pdfium/*.h | wc -l) headers ok"

step "consumers"
gcc -O2 -o "$work/probe" "$repo/tests/consumer/pdfium_probe.c" $(pkg-config --cflags --libs pdfium)
printf '#include <pdfium/fpdfview.h>\n#include <pdfium/fpdf_text.h>\nint main(void){FPDF_InitLibrary();FPDF_DestroyLibrary();return 0;}\n' > "$work/vaulthalla_style.c"
gcc -o "$work/vaulthalla_style" "$work/vaulthalla_style.c" $(pkg-config --cflags --libs pdfium) && "$work/vaulthalla_style"
g++ -std=c++17 -O2 -o "$work/scopers" "$repo/tests/consumer/scopers_check.cpp" $(pkg-config --cflags --libs pdfium)
"$work/scopers" "$repo/upstream/pdfium/testing/resources/hello_world.pdf"
ldd "$work/probe" | grep -qE "libpdfium.so.${soversion} => (/usr)?/lib/${multiarch}/libpdfium.so.${soversion}" || fail "probe does not use the installed library"
apt-get install -y -qq --no-install-recommends cmake >/dev/null
cmake -S "$repo/tests/consumer/cmake" -B "$work/cmake" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$work/cmake" >/dev/null
"$work/cmake/pdfium_cmake_consumer" "$repo/upstream/pdfium/testing/resources/hello_world.pdf" | grep -q '"pages":1' \
  || fail "CMake consumer"
echo "pkg-config C consumer, <pdfium/...> includes, C++ scopers and find_package(Pdfium) consumer ok"

step "integration tests against the installed library"
python3 "$repo/tests/integration.py" --probe "$work/probe" --resources "$repo/upstream/pdfium/testing/resources" \
  --work "$work/integration" --checks fixtures,malformed,corpus,pixel,consistency \
  --report "$deb_dir/package-integration.json" | tail -3

step "upgrade to a newer package revision"
for package in "$runtime" libpdfium-dev; do
  deb=$(ls "$deb_dir/${package}_${version}-"*_${arch}.deb)
  dpkg-deb -R "$deb" "$work/repack-$package"
  sed -i -E "s/^Version: .*/Version: ${version}-99/; s/\(= ${version}-[0-9]+\)/(= ${version}-99)/" "$work/repack-$package/DEBIAN/control"
  dpkg-deb -b --root-owner-group "$work/repack-$package" "$work/${package}_${version}-99_${arch}.deb" >/dev/null
done
apt-get install -y -qq "$work/${runtime}_${version}-99_${arch}.deb" "$work/libpdfium-dev_${version}-99_${arch}.deb" >/dev/null
[ "$(dpkg-query -W -f '${Version}' libpdfium-dev)" = "${version}-99" ] || fail "upgrade"
"$work/probe" "$repo/upstream/pdfium/testing/resources/hello_world.pdf" | grep -q '"loaded":true' || fail "probe after upgrade"
echo "upgrade ok; the consumer built against the previous revision still runs"

step "removal and purge"
apt-get remove -y -qq libpdfium-dev >/dev/null
[ ! -e /usr/include/pdfium ] && [ ! -e /usr/lib/${multiarch}/libpdfium.so ] || fail "development files left behind"
"$work/probe" "$repo/upstream/pdfium/testing/resources/hello_world.pdf" | grep -q '"loaded":true' || fail "runtime gone with -dev"
apt-get purge -y -qq "$runtime" >/dev/null
[ -z "$(ls /usr/lib/${multiarch}/libpdfium.so* 2>/dev/null)" ] || fail "library files left behind"
! ldconfig -p | grep -q libpdfium.so || fail "ldconfig cache still lists libpdfium"
[ ! -e "/usr/share/doc/${runtime}" ] || fail "documentation left behind"
echo "remove keeps the runtime; purge removes everything"
printf '\nclean-install: all package checks passed\n'
