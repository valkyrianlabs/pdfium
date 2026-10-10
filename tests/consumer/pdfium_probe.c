/* pdfium_probe: an independent consumer of the installed PDFium C API.
 *
 * Loads each PDF, renders every page (like upstream's pdfium_test: FPDF_ANNOT, forms drawn with
 * FPDF_FFLDraw, transparent pages into BGRA) and extracts text, printing one JSON object per
 * document. With --out DIR it also writes page images (PAM, RGB_ALPHA) and UTF-8 text files, which
 * tests/integration.py compares against references.
 *
 * Only API that exists in both the legacy (2025-06) and current headers is used, so the same
 * program can be built against either package.
 */
#include <fpdf_doc.h>
#include <fpdf_edit.h>
#include <fpdf_formfill.h>
#include <fpdf_text.h>
#include <fpdfview.h>

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct options {
  double scale;
  int max_pages;
  const char* out_dir;
  const char* password;
  int from_memory;
};

static uint64_t fnv1a(uint64_t hash, const void* data, size_t size) {
  const unsigned char* p = (const unsigned char*)data;
  for (size_t i = 0; i < size; ++i) {
    hash ^= p[i];
    hash *= 1099511628211ULL;
  }
  return hash;
}

static void json_string(FILE* out, const char* s) {
  fputc('"', out);
  for (; *s; ++s) {
    unsigned char c = (unsigned char)*s;
    if (c == '"' || c == '\\') {
      fprintf(out, "\\%c", c);
    } else if (c < 0x20) {
      fprintf(out, "\\u%04x", c);
    } else {
      fputc(c, out);
    }
  }
  fputc('"', out);
}

/* UTF-16LE (as returned by PDFium) to UTF-8; returns a malloc'd string. */
static char* utf16_to_utf8(const unsigned short* in, size_t count) {
  char* out = malloc(count * 4 + 1);
  size_t n = 0;
  if (!out) return NULL;
  for (size_t i = 0; i < count; ++i) {
    uint32_t cp = in[i];
    if (cp >= 0xD800 && cp <= 0xDBFF && i + 1 < count && in[i + 1] >= 0xDC00 && in[i + 1] <= 0xDFFF) {
      cp = 0x10000 + ((cp - 0xD800) << 10) + (in[i + 1] - 0xDC00);
      ++i;
    }
    if (cp == 0) continue;
    if (cp < 0x80) {
      out[n++] = (char)cp;
    } else if (cp < 0x800) {
      out[n++] = (char)(0xC0 | (cp >> 6));
      out[n++] = (char)(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
      out[n++] = (char)(0xE0 | (cp >> 12));
      out[n++] = (char)(0x80 | ((cp >> 6) & 0x3F));
      out[n++] = (char)(0x80 | (cp & 0x3F));
    } else {
      out[n++] = (char)(0xF0 | (cp >> 18));
      out[n++] = (char)(0x80 | ((cp >> 12) & 0x3F));
      out[n++] = (char)(0x80 | ((cp >> 6) & 0x3F));
      out[n++] = (char)(0x80 | (cp & 0x3F));
    }
  }
  out[n] = '\0';
  return out;
}

static const char* basename_of(const char* path) {
  const char* slash = strrchr(path, '/');
  return slash ? slash + 1 : path;
}

static int write_pam(const char* path, FPDF_BITMAP bitmap, int has_alpha) {
  int width = FPDFBitmap_GetWidth(bitmap);
  int height = FPDFBitmap_GetHeight(bitmap);
  int stride = FPDFBitmap_GetStride(bitmap);
  const unsigned char* buffer = (const unsigned char*)FPDFBitmap_GetBuffer(bitmap);
  FILE* f = fopen(path, "wb");
  if (!f) return -1;
  fprintf(f, "P7\nWIDTH %d\nHEIGHT %d\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n", width, height);
  unsigned char* row = malloc((size_t)width * 4);
  for (int y = 0; y < height && row; ++y) {
    const unsigned char* src = buffer + (size_t)y * stride;
    for (int x = 0; x < width; ++x) {
      row[x * 4 + 0] = src[x * 4 + 2];
      row[x * 4 + 1] = src[x * 4 + 1];
      row[x * 4 + 2] = src[x * 4 + 0];
      row[x * 4 + 3] = has_alpha ? src[x * 4 + 3] : 255;
    }
    fwrite(row, 1, (size_t)width * 4, f);
  }
  free(row);
  return fclose(f);
}

static unsigned char* read_file(const char* path, size_t* size) {
  FILE* f = fopen(path, "rb");
  if (!f) return NULL;
  fseek(f, 0, SEEK_END);
  long length = ftell(f);
  fseek(f, 0, SEEK_SET);
  unsigned char* data = malloc(length > 0 ? (size_t)length : 1);
  if (data && length > 0 && fread(data, 1, (size_t)length, f) != (size_t)length) {
    free(data);
    data = NULL;
  }
  fclose(f);
  *size = length > 0 ? (size_t)length : 0;
  return data;
}

static void probe(const char* path, const struct options* opt, FILE* out) {
  unsigned char* memory = NULL;
  size_t memory_size = 0;
  FPDF_DOCUMENT doc;
  if (opt->from_memory) {
    memory = read_file(path, &memory_size);
    doc = memory ? FPDF_LoadMemDocument(memory, (int)memory_size, opt->password) : NULL;
  } else {
    doc = FPDF_LoadDocument(path, opt->password);
  }
  fprintf(out, "{\"file\":");
  json_string(out, basename_of(path));
  if (!doc) {
    fprintf(out, ",\"loaded\":false,\"error\":%lu}\n", (unsigned long)FPDF_GetLastError());
    free(memory);
    return;
  }

  FPDF_FORMFILLINFO form_info;
  memset(&form_info, 0, sizeof(form_info));
  form_info.version = 1;
  FPDF_FORMHANDLE form = FPDFDOC_InitFormFillEnvironment(doc, &form_info);

  int file_version = 0;
  FPDF_GetFileVersion(doc, &file_version);
  int page_count = FPDF_GetPageCount(doc);
  unsigned short title16[256];
  unsigned long title_len = FPDF_GetMetaText(doc, "Title", title16, sizeof(title16));
  char* title = utf16_to_utf8(title16, title_len >= 2 && title_len <= sizeof(title16) ? title_len / 2 : 0);
  int bookmarks = 0;
  for (FPDF_BOOKMARK b = FPDFBookmark_GetFirstChild(doc, NULL); b && bookmarks < 10000;
       b = FPDFBookmark_GetNextSibling(doc, b)) {
    ++bookmarks;
  }
  fprintf(out, ",\"loaded\":true,\"version\":%d,\"pages\":%d,\"bookmarks\":%d,\"title\":", file_version, page_count, bookmarks);
  json_string(out, title ? title : "");
  free(title);
  fprintf(out, ",\"page_info\":[");

  int limit = opt->max_pages > 0 && opt->max_pages < page_count ? opt->max_pages : page_count;
  for (int i = 0; i < limit; ++i) {
    FPDF_PAGE page = FPDF_LoadPage(doc, i);
    if (i) fputc(',', out);
    if (!page) {
      fprintf(out, "{\"index\":%d,\"loaded\":false}", i);
      continue;
    }
    if (form) {
      FORM_OnAfterLoadPage(page, form);
    }
    float width_pt = FPDF_GetPageWidthF(page);
    float height_pt = FPDF_GetPageHeightF(page);
    int width = (int)(width_pt * opt->scale);
    int height = (int)(height_pt * opt->scale);
    int has_alpha = FPDFPage_HasTransparency(page) ? 1 : 0;
    uint64_t pixel_hash = 1469598103934665603ULL;
    double ink = 0.0;
    int rendered = 0;
    if (width > 0 && height > 0 && (long long)width * height <= 64LL * 1024 * 1024) {
      FPDF_BITMAP bitmap = FPDFBitmap_Create(width, height, has_alpha);
      if (bitmap) {
        FPDFBitmap_FillRect(bitmap, 0, 0, width, height, has_alpha ? 0x00000000 : 0xFFFFFFFF);
        FPDF_RenderPageBitmap(bitmap, page, 0, 0, width, height, 0, FPDF_ANNOT);
        if (form) {
          FPDF_FFLDraw(form, bitmap, page, 0, 0, width, height, 0, FPDF_ANNOT);
        }
        const unsigned char* buffer = (const unsigned char*)FPDFBitmap_GetBuffer(bitmap);
        int stride = FPDFBitmap_GetStride(bitmap);
        long long inked = 0;
        for (int y = 0; y < height; ++y) {
          const unsigned char* row = buffer + (size_t)y * stride;
          pixel_hash = fnv1a(pixel_hash, row, (size_t)width * 4);
          for (int x = 0; x < width; ++x) {
            const unsigned char* px = row + x * 4;
            int opaque_ink = !has_alpha && (px[0] < 250 || px[1] < 250 || px[2] < 250);
            int alpha_ink = has_alpha && px[3] > 5;
            inked += opaque_ink || alpha_ink;
          }
        }
        ink = (double)inked / ((double)width * height);
        rendered = 1;
        if (opt->out_dir) {
          char image_path[4096];
          snprintf(image_path, sizeof(image_path), "%s/%s.%d.pam", opt->out_dir, basename_of(path), i);
          write_pam(image_path, bitmap, has_alpha);
        }
        FPDFBitmap_Destroy(bitmap);
      }
    }

    int chars = -1;
    uint64_t text_hash = 1469598103934665603ULL;
    FPDF_TEXTPAGE text_page = FPDFText_LoadPage(page);
    if (text_page) {
      chars = FPDFText_CountChars(text_page);
      if (chars > 0) {
        unsigned short* buffer16 = calloc((size_t)chars + 1, sizeof(unsigned short));
        int got = buffer16 ? FPDFText_GetText(text_page, 0, chars, buffer16) : 0;
        char* text = got > 0 ? utf16_to_utf8(buffer16, (size_t)got) : NULL;
        if (text) {
          text_hash = fnv1a(text_hash, text, strlen(text));
          if (opt->out_dir) {
            char text_path[4096];
            snprintf(text_path, sizeof(text_path), "%s/%s.%d.txt", opt->out_dir, basename_of(path), i);
            FILE* tf = fopen(text_path, "wb");
            if (tf) {
              fputs(text, tf);
              fclose(tf);
            }
          }
        }
        free(text);
        free(buffer16);
      }
      FPDFText_ClosePage(text_page);
    }
    fprintf(out,
            "{\"index\":%d,\"loaded\":true,\"width_pt\":%.2f,\"height_pt\":%.2f,\"rendered\":%s,"
            "\"alpha\":%s,\"ink\":%.5f,\"pixel_hash\":\"%016llx\",\"chars\":%d,\"text_hash\":\"%016llx\"}",
            i, width_pt, height_pt, rendered ? "true" : "false", has_alpha ? "true" : "false", ink,
            (unsigned long long)pixel_hash, chars, (unsigned long long)text_hash);
    if (form) {
      FORM_OnBeforeClosePage(page, form);
    }
    FPDF_ClosePage(page);
  }
  fprintf(out, "]}\n");
  if (form) {
    FPDFDOC_ExitFormFillEnvironment(form);
  }
  FPDF_CloseDocument(doc);
  free(memory);
}

int main(int argc, char** argv) {
  struct options opt = {1.0, 0, NULL, NULL, 0};
  int first = 1;
  for (; first < argc && strncmp(argv[first], "--", 2) == 0; ++first) {
    const char* arg = argv[first];
    if (strcmp(arg, "--") == 0) {
      ++first;
      break;
    } else if (strcmp(arg, "--scale") == 0 && first + 1 < argc) {
      opt.scale = atof(argv[++first]);
    } else if (strcmp(arg, "--max-pages") == 0 && first + 1 < argc) {
      opt.max_pages = atoi(argv[++first]);
    } else if (strcmp(arg, "--out") == 0 && first + 1 < argc) {
      opt.out_dir = argv[++first];
    } else if (strcmp(arg, "--password") == 0 && first + 1 < argc) {
      opt.password = argv[++first];
    } else if (strcmp(arg, "--memory") == 0) {
      opt.from_memory = 1;
    } else {
      fprintf(stderr, "usage: %s [--scale S] [--max-pages N] [--out DIR] [--password PW] [--memory] FILE.pdf...\n", argv[0]);
      return 2;
    }
  }
  if (first >= argc || opt.scale <= 0) {
    fprintf(stderr, "usage: %s [--scale S] [--max-pages N] [--out DIR] [--password PW] [--memory] FILE.pdf...\n", argv[0]);
    return 2;
  }

  FPDF_LIBRARY_CONFIG config;
  memset(&config, 0, sizeof(config));
  config.version = 2;
  FPDF_InitLibraryWithConfig(&config);
  for (int i = first; i < argc; ++i) {
    probe(argv[i], &opt, stdout);
    fflush(stdout);
  }
  FPDF_DestroyLibrary();
  return 0;
}
