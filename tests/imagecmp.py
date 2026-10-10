"""Minimal, stdlib-only image I/O and comparison for the integration tests.

Reads 8-bit PNGs (as written by upstream's pdfium_test/png codec) and the PAM files written by
tests/consumer/pdfium_probe.c, and compares RGBA rasters with a per-channel tolerance.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Image:
    width: int
    height: int
    rgba: bytes  # width * height * 4


def read_png(path: Path) -> Image:
    """Decode a non-interlaced PNG of any bit depth/color type into 8-bit RGBA."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    pos = 8
    width = height = depth = color = interlace = 0
    idat = bytearray()
    palette = b""
    transparency = b""
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            transparency = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if interlace:
        raise ValueError(f"{path}: interlaced PNGs are not supported")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    bits_per_pixel = channels * depth
    stride = (width * bits_per_pixel + 7) // 8
    unit = max(1, bits_per_pixel // 8)
    raw = zlib.decompress(bytes(idat))
    rows = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        kind = raw[offset]
        line = bytearray(raw[offset + 1 : offset + 1 + stride])
        offset += 1 + stride
        for i in range(stride):
            left = line[i - unit] if i >= unit else 0
            up = previous[i]
            upper_left = previous[i - unit] if i >= unit else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif kind == 4:
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                predictor = left if pa <= pb and pa <= pc else (up if pb <= pc else upper_left)
                line[i] = (line[i] + predictor) & 0xFF
        previous = line
        rows.append(bytes(line))

    def samples(line: bytes) -> list[int]:
        if depth == 8:
            return list(line)
        if depth == 16:
            return [line[i] for i in range(0, len(line), 2)]
        per_byte = 8 // depth
        mask = (1 << depth) - 1
        values = []
        for byte in line:
            for k in range(per_byte):
                values.append((byte >> (8 - depth * (k + 1))) & mask)
        return values[: width * channels]

    scale = 255 // ((1 << min(depth, 8)) - 1) if color in (0, 4) and depth < 8 else 1
    alpha_table = transparency if color == 3 else b""
    out = bytearray(width * height * 4)
    for y, line in enumerate(rows):
        values = samples(line)
        for x in range(width):
            o = (y * width + x) * 4
            v = values[x * channels : x * channels + channels]
            if color == 3:
                index = v[0]
                out[o : o + 3] = palette[index * 3 : index * 3 + 3]
                out[o + 3] = alpha_table[index] if index < len(alpha_table) else 255
            elif color == 0:
                g = v[0] * scale
                out[o : o + 4] = bytes((g, g, g, 255))
            elif color == 4:
                g = v[0] * scale
                out[o : o + 4] = bytes((g, g, g, v[1] * scale))
            elif color == 2:
                out[o : o + 4] = bytes((v[0], v[1], v[2], 255))
            else:
                out[o : o + 4] = bytes(v)
    return Image(width, height, bytes(out))


def read_pam(path: Path) -> Image:
    data = path.read_bytes()
    header_end = data.index(b"ENDHDR\n") + len(b"ENDHDR\n")
    fields = dict(line.split(" ", 1) for line in data[:header_end].decode().splitlines()[1:-1])
    width, height = int(fields["WIDTH"]), int(fields["HEIGHT"])
    if int(fields["DEPTH"]) != 4:
        raise ValueError(f"{path}: expected RGB_ALPHA")
    return Image(width, height, data[header_end : header_end + width * height * 4])


@dataclass
class Comparison:
    same_size: bool
    max_delta: int
    differing: float  # fraction of pixels with any channel delta > tolerance
    mean_delta: float


def compare(a: Image, b: Image, tolerance: int = 16) -> Comparison:
    if (a.width, a.height) != (b.width, b.height):
        return Comparison(False, 255, 1.0, 255.0)
    pixels = a.width * a.height
    if pixels == 0:
        return Comparison(True, 0, 0.0, 0.0)
    max_delta = 0
    differing = 0
    total = 0
    pa, pb = a.rgba, b.rgba
    for i in range(0, pixels * 4, 4):
        if pa[i : i + 4] == pb[i : i + 4]:
            continue
        delta = max(abs(pa[i] - pb[i]), abs(pa[i + 1] - pb[i + 1]), abs(pa[i + 2] - pb[i + 2]), abs(pa[i + 3] - pb[i + 3]))
        total += delta
        max_delta = max(max_delta, delta)
        if delta > tolerance:
            differing += 1
    return Comparison(True, max_delta, differing / pixels, total / pixels)
