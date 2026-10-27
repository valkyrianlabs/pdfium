# Mirrors core/fpdfapi/edit/BUILD.gn for the supported configuration (upstream.toml [audit.args]).
# Source paths are relative to that directory, as in GN; only compiled sources are listed.
# `tools/pdfium-dist audit` reports every difference from upstream.

# edit: font subsetting uses system HarfBuzz (hb-subset).
pdfium_components += [{
  'name': 'fpdfapi_edit',
  'gn': '//core/fpdfapi/edit:edit',
  'dir': 'core/fpdfapi/edit',
  'dependencies': [harfbuzz_dep],
  'sources': [
    'cpdf_creator.cpp',
    'cpdf_font_util.cpp',
    'cpdf_fontsubsetter.cpp',
    'cpdf_npagetooneexporter.cpp',
    'cpdf_pagecontentgenerator.cpp',
    'cpdf_pagecontentmanager.cpp',
    'cpdf_pageexporter.cpp',
    'cpdf_pageorganizer.cpp',
    'cpdf_stringarchivestream.cpp',
  ],
}]
