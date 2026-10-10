# Mirrors core/fxcodec/BUILD.gn for the supported configuration (upstream.toml [audit.args]).
# Source paths are relative to that directory, as in GN; only compiled sources are listed.
# `tools/pdfium-dist audit` reports every difference from upstream.

# fxcodec: system zlib/libjpeg/lcms2 (USE_SYSTEM_* in meson.build), bundled
# libopenjpeg; no XFA image codecs, Brotli or Rust codecs.
pdfium_components += [{
  'name': 'fxcodec',
  'gn': '//core/fxcodec:fxcodec',
  'dir': 'core/fxcodec',
  'dependencies': [zlib_dep, jpeg_dep, lcms2_dep],
  'sources': [
    'basic/basicmodule.cpp',
    'data_and_bytes_consumed.cpp',
    'fax/faxmodule.cpp',
    'flate/flatemodule.cpp',
    'fx_codec.cpp',
    'icc/icc_transform.cpp',
    'image_predictors.cpp',
    'jbig2/jbig2_arith_decoder.cpp',
    'jbig2/jbig2_arith_int_decoder.cpp',
    'jbig2/jbig2_bit_stream.cpp',
    'jbig2/jbig2_context.cpp',
    'jbig2/jbig2_decoder.cpp',
    'jbig2/jbig2_document_context.cpp',
    'jbig2/jbig2_grd_proc.cpp',
    'jbig2/jbig2_grrd_proc.cpp',
    'jbig2/jbig2_htrd_proc.cpp',
    'jbig2/jbig2_huffman_decoder.cpp',
    'jbig2/jbig2_huffman_table.cpp',
    'jbig2/jbig2_image.cpp',
    'jbig2/jbig2_pattern_dict.cpp',
    'jbig2/jbig2_pdd_proc.cpp',
    'jbig2/jbig2_sdd_proc.cpp',
    'jbig2/jbig2_segment.cpp',
    'jbig2/jbig2_symbol_dict.cpp',
    'jbig2/jbig2_trd_proc.cpp',
    'jpeg/jpegmodule.cpp',
    'jpx/cjpx_decoder.cpp',
    'jpx/jpx_decode_utils.cpp',
    'scanlinedecoder.cpp',
    # !pdf_enable_rust_jpeg
    'jpeg/libjpeg_scanline_decoder.cpp',
    # !pdf_enable_rust_jpeg || is_win
    'jpeg/jpeg_common.c',
  ],
}]
