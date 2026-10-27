<!--
vl-release staged changelog: package-facing changes for the NEXT release.
Maintained continuously while working; `vlr prepare` renders it into debian/changelog
and resets this file to this template.

Format: one "- " bullet per change (concise, technical). Indent continuation lines.
Optional "## Section" headings group bullets; if used, every bullet must be under one.
One level of nested "  - " detail bullets is allowed. Consolidate; don't paste commit logs.
-->
- Update PDFium to chromium/8078 (Chromium 156.0.8078.47, 2f22dfe030de).
- Publish to the Valkyrian Labs APT repository (`release.toml` `repository_url` = `https://apt.valkyrianlabs.com`,
  Production `NEXUS_REPO_URL` likewise; apt.vaulthalla.sh is an alias of the same index); shared
  `valkyrianlabs-apt-publish` concurrency group; package tests read from apt.valkyrianlabs.com.
