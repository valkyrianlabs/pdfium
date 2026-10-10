#!/usr/bin/env bash
# Verify a published release from the public APT repository, in a clean stock Ubuntu container
# (run as root by tests/package/run): apt resolves libpdfium-dev to VERSION (not the legacy
# 20250629-1), the installed .debs are byte-identical to the validated release artifacts (when
# DEB_DIR has them), an external consumer builds with pkg-config, and real PDFs render and yield
# text.
#
#   tests/package/published.sh [DEB_DIR]
set -euo pipefail
deb_dir=$(realpath "${1:-release}")
repo=$(cd "$(dirname "$0")/../.." && pwd)
version=$(tr -d '[:space:]' < "$repo/VERSION")
soversion=$(tr -d '[:space:]' < "$repo/abi/soversion")
runtime="libpdfium${soversion}"
apt_url=${APT_URL:-https://apt.vaulthalla.sh}
[ -n "$apt_url" ] || apt_url=https://apt.vaulthalla.sh
work=$(mktemp -d)
export DEBIAN_FRONTEND=noninteractive
arch=$(dpkg --print-architecture)
step() { printf '\n=== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
rm -f /etc/dpkg/dpkg.cfg.d/excludes

step "configure $apt_url"
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg >/dev/null
install -d -m 0755 /etc/apt/keyrings
curl -fsSL "$apt_url/pubkey.gpg" | gpg --dearmor -o /etc/apt/keyrings/vaulthalla.gpg
echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/vaulthalla.gpg] $apt_url stable main" \
  > /etc/apt/sources.list.d/vaulthalla.list
apt-get update -qq

step "apt resolution"
apt-cache policy libpdfium-dev "$runtime" | sed 's/^/  /'
candidate=$(apt-cache policy libpdfium-dev | awk '/Candidate:/ {print $2}')
[ "$candidate" = "${version}-1" ] || fail "libpdfium-dev candidate is $candidate, expected ${version}-1"
! apt-cache madison libpdfium-dev | grep -q 20250629 || fail "the legacy libpdfium-dev is still offered"

step "install libpdfium-dev from the repository"
apt-get install -y -qq --no-install-recommends libpdfium-dev build-essential pkgconf python3 >/dev/null
dpkg-query -W -f '  ${Package} ${Version} ${Status}\n' "$runtime" libpdfium-dev
[ "$(dpkg-query -W -f '${Version}' "$runtime")" = "${version}-1" ] || fail "$runtime version"

if ls "$deb_dir/${runtime}_${version}-1_${arch}.deb" >/dev/null 2>&1; then
  step "published bytes == validated release artifacts"
  for package in "$runtime" libpdfium-dev; do
    (cd "$work" && apt-get download -qq "${package}=${version}-1" 2>/dev/null)
    downloaded="$work/${package}_${version}-1_${arch}.deb"
    [ -f "$downloaded" ] || fail "cannot download ${package}=${version}-1"
    cmp -s "$downloaded" "$deb_dir/${package}_${version}-1_${arch}.deb" || fail "$package differs from the validated artifact"
    echo "  identical: ${package}_${version}-1_${arch}.deb ($(sha256sum < "$downloaded" | cut -c1-16)...)"
  done
fi

step "external consumer"
gcc -O2 -o "$work/probe" "$repo/tests/consumer/pdfium_probe.c" $(pkg-config --cflags --libs pdfium)
printf '#include <pdfium/fpdfview.h>\nint main(void){FPDF_InitLibrary();FPDF_DestroyLibrary();return 0;}\n' > "$work/vh.c"
gcc -o "$work/vh" "$work/vh.c" $(pkg-config --cflags --libs pdfium) && "$work/vh"
ldd "$work/probe" | grep "libpdfium.so.${soversion}"
resources="$repo/upstream/pdfium/testing/resources"
mkdir -p "$work/out"
for pdf in hello_world.pdf vertical_text.pdf jpx_lzw.pdf annots.pdf; do
  "$work/probe" --out "$work/out" "$resources/$pdf" | tee -a "$work/results.jsonl" | cut -c1-160
done
python3 - "$work/results.jsonl" "$work/out" <<'EOF' || fail "rendering/text checks"
import json, sys, pathlib
docs = [json.loads(line) for line in open(sys.argv[1])]
out = pathlib.Path(sys.argv[2])
assert all(d["loaded"] and d["page_info"] and d["page_info"][0]["rendered"] for d in docs), "load/render"
assert all(d["page_info"][0]["ink"] > 0 for d in docs), "blank render"
text = (out / "hello_world.pdf.0.txt").read_text()
assert "Hello, world!" in text, text
assert "Hello World!" in (out / "vertical_text.pdf.0.txt").read_text(), "CJK CMap text"
print(f"  {len(docs)} documents rendered with ink; text extracted (incl. CJK vertical text)")
EOF
printf '\npublished: libpdfium-dev %s from %s verified\n' "${version}-1" "$apt_url"
