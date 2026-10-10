# Forwarding headers for system libraries

PDFium includes a few dependencies by their Chromium checkout path and offers no
`USE_SYSTEM_*` switch for them. Each header here forwards that path to the system
library's own header (the same technique as Chromium's `build/linux/unbundle` shims), so
no upstream source is patched:

| Upstream include | System package |
|---|---|
| `third_party/abseil-cpp/absl/...` | `libabsl-dev` (Ubuntu's abseil is built with `ABSL_OPTION_USE_STD_*=0`, so its ABI does not depend on the C++ standard we compile with) |

`tools/pdfium-dist audit` lists every `third_party/...` include in the compiled sources, so a
new one shows up as soon as upstream adds it.
