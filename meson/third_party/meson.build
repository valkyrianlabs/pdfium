# Mirrors third_party/BUILD.gn for the supported configuration (upstream.toml [audit.args]).
# Source paths are relative to that directory, as in GN; only compiled sources are listed.
# `tools/pdfium-dist audit` reports every difference from upstream.

# fx_agg: AGG 2.3 carrying PDFium's patches (in PDFium's tree).
pdfium_components += [{
  'name': 'fx_agg',
  'gn': '//third_party:fx_agg',
  'dir': 'third_party',
  'sources': [
    'agg23/agg_curves.cpp',
    'agg23/agg_path_storage.cpp',
    'agg23/agg_rasterizer_scanline_aa.cpp',
    'agg23/agg_vcgen_dash.cpp',
    'agg23/agg_vcgen_stroke.cpp',
  ],
}]

# fx_libopenjpeg: OpenJPEG carrying PDFium's security patches (in PDFium's
# tree): upstream's default, and what the legacy build used.
pdfium_components += [{
  'name': 'fx_libopenjpeg',
  'gn': '//third_party:fx_libopenjpeg',
  'dir': 'third_party',
  'c_args': pdfium_third_party_c_args,
  'sources': [
    'libopenjpeg/bio.c',
    'libopenjpeg/cio.c',
    'libopenjpeg/dwt.c',
    'libopenjpeg/event.c',
    'libopenjpeg/function_list.c',
    'libopenjpeg/ht_dec.c',
    'libopenjpeg/image.c',
    'libopenjpeg/invert.c',
    'libopenjpeg/j2k.c',
    'libopenjpeg/jp2.c',
    'libopenjpeg/mct.c',
    'libopenjpeg/mqc.c',
    'libopenjpeg/openjpeg.c',
    'libopenjpeg/opj_malloc.cc',
    'libopenjpeg/pi.c',
    'libopenjpeg/sparse_array.c',
    'libopenjpeg/t1.c',
    'libopenjpeg/t2.c',
    'libopenjpeg/tcd.c',
    'libopenjpeg/tgt.c',
    'libopenjpeg/thread.c',
  ],
}]
