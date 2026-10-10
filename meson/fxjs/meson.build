# Mirrors fxjs/BUILD.gn for the supported configuration (upstream.toml [audit.args]).
# Source paths are relative to that directory, as in GN; only compiled sources are listed.
# `tools/pdfium-dist audit` reports every difference from upstream.

# fxjs: JavaScript stubs (no V8).
pdfium_components += [{
  'name': 'fxjs',
  'gn': '//fxjs:fxjs',
  'dir': 'fxjs',
  'sources': [
    'cjs_event_context_stub.cpp',
    'cjs_runtimestub.cpp',
    'ijs_runtime.cpp',
  ],
}]
