# Integration tests against the library built here. The same consumer and test driver run
# against the installed Debian packages (tests/package/), so this is a faster first gate.
pdfium_probe = executable(
  'pdfium_probe',
  'consumer/pdfium_probe.c',
  dependencies: pdfium_dep,
  install: false,
)

python3 = find_program('python3')
resources = meson.project_source_root() / pdfium_root / 'testing' / 'resources'
if fs.is_absolute(pdfium_root)
  resources = pdfium_root / 'testing' / 'resources'
endif

test(
  'integration',
  python3,
  args: [
    files('integration.py'),
    '--probe', pdfium_probe,
    '--resources', resources,
    '--work', meson.current_build_dir() / 'integration',
  ],
  timeout: 900,
  is_parallel: false,
)
