# Vaulthalla PDFium: a native Meson + Ninja build of upstream PDFium.
#
# The PDFium sources are not part of this repository: `tools/pdfium-dist prepare` exports the
# revision pinned in sources.lock.json into upstream/ (see README.md). meson/**/meson.build mirror
# upstream's BUILD.gn files as plain source lists; this file turns them into one library.
# No GN, CMake, gclient or depot_tools is involved anywhere.
project(
  'pdfium',
  'c', 'cpp',
  version: files('VERSION'),
  license: 'BSD-3-Clause',
  meson_version: '>= 1.3.0',
  default_options: [
    'buildtype=release',
    'default_library=shared',
    'cpp_std=c++20',
    # Chromium's toolchain configuration, which PDFium is written against: no exceptions, no RTTI,
    # release builds without DCHECKs.
    'cpp_eh=none',
    'cpp_rtti=false',
    'b_ndebug=true',
    'warning_level=0',
  ],
)

fs = import('fs')
pkg = import('pkgconfig')
cc = meson.get_compiler('c')
cxx = meson.get_compiler('cpp')

if host_machine.system() != 'linux'
  error('Only Linux is supported so far (the meson/ lists encode is_linux/is_posix).')
endif

# --- upstream sources ----------------------------------------------------------------------------
upstream_dir = get_option('upstream_dir')
pdfium_root = upstream_dir / 'pdfium'
if not fs.is_file(pdfium_root / 'public' / 'fpdfview.h')
  error('PDFium sources not found in @0@: run `tools/pdfium-dist prepare` first.'.format(pdfium_root))
endif
if not fs.is_file(upstream_dir / '.prepared.json')
  warning('@0@ has no .prepared.json stamp: it was not produced by tools/pdfium-dist prepare.'.format(upstream_dir))
endif
soversion = fs.read('abi' / 'soversion').strip()

# --- dependencies ----------------------------------------------------------------------------------
# System libraries (Debian/Ubuntu packages). PDFium's own USE_SYSTEM_* switches select them.
freetype_dep = dependency('freetype2')
icu_dep = dependency('icu-uc')
zlib_dep = dependency('zlib')
jpeg_dep = dependency('libjpeg')
lcms2_dep = dependency('lcms2')
harfbuzz_dep = [dependency('harfbuzz'), dependency('harfbuzz-subset')]
# abseil: included as third_party/abseil-cpp/absl/... (forwarding headers in meson/shims).
absl_dep = [
  dependency('absl_cleanup'),
  dependency('absl_flat_hash_set'),
  dependency('absl_inlined_vector'),
]
threads_dep = dependency('threads')

# Header-only DEPS dependencies exported into the PDFium tree by `prepare`; PDFium includes them
# by path from its root, so they need nothing beyond the root include directory.
fast_float_dep = declare_dependency()
dragonbox_dep = declare_dependency()

# --- compiler configuration ------------------------------------------------------------------------
# PDFium's GN configs (pdfium_common_config, pdfium_public_config, third_party groups) for the
# supported configuration, plus the parts of Chromium's toolchain config PDFium relies on.
pdfium_defines = [
  '-DPDF_USE_AGG',            # pdfium_public_config: AGG renderer (no Skia)
  '-DOPJ_STATIC',             # pdfium_common_config: bundled libopenjpeg
  '-DUSE_SYSTEM_ZLIB',        # //third_party:zlib
  '-DUSE_SYSTEM_LIBJPEG',     # //third_party:jpeg
  '-DUSE_SYSTEM_LCMS2',       # //third_party:lcms2
  '-DUSE_SYSTEM_ICUUC',       # //third_party/icu (system ICU)
  '-DFPDF_IMPLEMENTATION',    # pdfium_implementation_config
]
if get_option('default_library') != 'static'
  # Chromium defines COMPONENT_BUILD for shared builds; it makes FPDF_EXPORT mark the public
  # API with default visibility while everything else stays hidden.
  pdfium_defines += '-DCOMPONENT_BUILD'
endif

pdfium_common_args = cxx.get_supported_arguments(
  '-fno-strict-aliasing',                 # Chromium //build/config/compiler
  '-fno-delete-null-pointer-checks',      # Chromium //build/config/compiler
  '-ffunction-sections',
  '-fdata-sections',
)
if get_option('hardening')
  # Hardening for parsing untrusted documents: zero-pattern-initialized locals (Chromium does
  # the same) and libstdc++ bounds/precondition checks (the libc++ hardening mode's analogue).
  pdfium_common_args += cxx.get_supported_arguments('-ftrivial-auto-var-init=pattern')
  pdfium_defines += '-D_GLIBCXX_ASSERTIONS'
endif

pdfium_cpp_args = pdfium_defines + pdfium_common_args + cxx.get_supported_arguments(
  '-Wno-return-type',  # pdfium_common_config for GCC (exhaustive switches)
)
# GCC misreads RetainPtr's intrusive refcounting under LTO (operator delete "with nonzero
# offset"); a known false positive that would otherwise flood the logs.
gcc_false_positives = cxx.get_id() == 'gcc' ? cxx.get_supported_arguments('-Wno-free-nonheap-object') : []
pdfium_cpp_args += gcc_false_positives
pdfium_c_args = pdfium_defines + pdfium_common_args
# Third-party C code (libopenjpeg) is built with upstream's no_chromium_code warning level.
pdfium_third_party_c_args = cc.get_supported_arguments('-Wno-unused-but-set-variable', '-Wno-stringop-overflow')

pdfium_inc = include_directories(pdfium_root, 'meson' / 'shims')
pdfium_common_deps = [freetype_dep, icu_dep, absl_dep, threads_dep]

# --- components ------------------------------------------------------------------------------------
# Each meson/<dir>/meson.build appends {name, gn, dir, sources[, dependencies, c_args, cpp_args]}.
pdfium_components = []
foreach component_dir : [
  'third_party',
  'constants',
  'core/fxcrt',
  'core/fdrm',
  'core/fxge',
  'core/fxcodec',
  'core/fpdfapi/cmaps',
  'core/fpdfapi/parser',
  'core/fpdfapi/edit',
  'core/fpdfapi/font',
  'core/fpdfapi/page',
  'core/fpdfapi/render',
  'core/fpdfdoc',
  'core/fpdftext',
  'fxjs',
  'fpdfsdk/pwl',
  'fpdfsdk/formfiller',
  'fpdfsdk',
  'public',
]
  subdir('meson' / component_dir)
endforeach

pdfium_component_libs = []
foreach component : pdfium_components
  component_sources = []
  foreach source : component['sources']
    component_sources += pdfium_root / component['dir'] / source
  endforeach
  pdfium_component_libs += static_library(
    component['name'],
    component_sources,
    include_directories: pdfium_inc,
    dependencies: pdfium_common_deps + component.get('dependencies', []),
    c_args: pdfium_c_args + component.get('c_args', []),
    cpp_args: pdfium_cpp_args + component.get('cpp_args', []),
    gnu_symbol_visibility: 'inlineshidden',
    pic: true,
    install: false,
  )
endforeach

# --- libpdfium -------------------------------------------------------------------------------------
version_script = meson.current_source_dir() / 'meson' / 'pdfium.map'
pdfium_link_args = cxx.get_supported_link_arguments('-Wl,--gc-sections') + gcc_false_positives
pdfium_link_depends = []
if get_option('default_library') != 'static'
  pdfium_link_args += '-Wl,--version-script=' + version_script
  pdfium_link_depends += files('meson' / 'pdfium.map')
endif

pdfium_external_deps = [freetype_dep, icu_dep, zlib_dep, jpeg_dep, lcms2_dep, harfbuzz_dep, absl_dep, threads_dep]
libpdfium = library(
  'pdfium',
  link_whole: pdfium_component_libs,
  dependencies: pdfium_external_deps,
  link_args: pdfium_link_args,
  link_depends: pdfium_link_depends,
  gnu_symbol_visibility: 'inlineshidden',
  version: soversion + '.0.0',
  soversion: soversion,
  install: true,
)

public_headers = []
foreach header : pdfium_public_headers
  public_headers += pdfium_root / 'public' / header
endforeach
install_headers(public_headers, subdir: 'pdfium')
public_cpp_headers = []
foreach header : pdfium_public_cpp_headers
  public_cpp_headers += pdfium_root / 'public' / header
endforeach
install_headers(public_cpp_headers, subdir: 'pdfium' / 'cpp')
install_symlink('public', pointing_to: '.', install_dir: get_option('includedir') / 'pdfium')

pdfium_dep = declare_dependency(
  link_with: libpdfium,
  include_directories: include_directories(pdfium_root / 'public'),
)
meson.override_dependency('pdfium', pdfium_dep)

# pdfium.pc: Cflags -I${includedir}/pdfium, so both <fpdfview.h> and <pdfium/fpdfview.h> work.
# A shared libpdfium needs nothing else at compile or link time; only a static one carries its
# dependencies (Requires.private), which pkg-config would otherwise demand even for --cflags.
pkg_library = libpdfium
if get_option('default_library') == 'shared'
  pkg_library = []
  pkg_extra = {'libraries': ['-L${libdir}', '-lpdfium']}
else
  pkg_extra = {}
endif
pkg.generate(
  pkg_library,
  kwargs: pkg_extra,
  name: 'PDFium',
  filebase: 'pdfium',
  description: 'PDF rendering library (PDFium from Chromium, Vaulthalla build)',
  url: 'https://pdfium.googlesource.com/pdfium',
  subdirs: 'pdfium',
)

if get_option('cmake_package')
  # Plain CMake package files for find_package(Pdfium) consumers (the legacy package shipped
  # them). They are text templates; CMake is not used to build anything.
  cmake_dir = get_option('libdir') / 'cmake' / 'pdfium'
  cmake_conf = configuration_data({
    'VERSION': meson.project_version(),
    'LIBDIR': get_option('libdir'),
    'INCLUDEDIR': get_option('includedir'),
    'LIBRARY': fs.name(libpdfium.full_path()),
    'SONAME': 'libpdfium.so.' + soversion,
  })
  configure_file(input: 'meson' / 'cmake' / 'PdfiumConfig.cmake.in', output: 'PdfiumConfig.cmake',
                 configuration: cmake_conf, install_dir: cmake_dir)
  configure_file(input: 'meson' / 'cmake' / 'PdfiumConfigVersion.cmake.in', output: 'PdfiumConfigVersion.cmake',
                 configuration: cmake_conf, install_dir: cmake_dir)
endif

if get_option('tests')
  subdir('tests')
endif

summary({
  'PDFium': '@0@ (@1@)'.format(pdfium_root, get_option('default_library')),
  'SONAME': 'libpdfium.so.' + soversion,
  'Features': 'AGG renderer; no V8, XFA, Skia, Rust codecs or PartitionAlloc',
  'Hardening': get_option('hardening'),
}, section: 'Vaulthalla PDFium')
