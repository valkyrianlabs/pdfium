<!--
vl-release staged changelog: package-facing changes for the NEXT release.
Maintained continuously while working; `vlr prepare` renders it into debian/changelog
and resets this file to this template.

Format: one "- " bullet per change (concise, technical). Indent continuation lines.
Optional "## Section" headings group bullets; if used, every bullet must be under one.
One level of nested "  - " detail bullets is allowed. Consolidate; don't paste commit logs.
-->
- Update PDFium to chromium/8078 (Chromium 156.0.8078.47, 2f22dfe030de).
  - Meson: `core/fpdfapi/edit/cpdf_contentstream_write_utils.cpp` moved upstream to `core/fxcrt/stream_write_utils.cpp`
    (with its dragonbox dependency); the `fpdfapi_contentstream_write_utils` component is gone. Audit baseline updated.
  - ABI: compatible additions `FPDFAttachment_GetAFRelationship`, `FPDFPageObj_GetRenderedFillPattern`; SONAME stays
    `libpdfium.so.8059`.
- Publish to the Valkyrian Labs APT repository (`release.toml` `repository_url` = `https://apt.valkyrianlabs.com`,
  Production `NEXUS_REPO_URL` likewise; apt.vaulthalla.sh is an alias of the same index); shared
  `valkyrianlabs-apt-publish` concurrency group; package tests read from apt.valkyrianlabs.com.
