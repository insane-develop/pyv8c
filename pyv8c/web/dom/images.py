"""The size an image's own bytes declare, read from the header alone.

A page that measures an image reads ``naturalWidth``/``naturalHeight``, and those are facts of
the file rather than of a decoded bitmap: the header carries them, so no decoder is needed to
answer.  Chromium reports ``0x0`` until the image has loaded and answers with the file's size
after, which is the shape this keeps.
"""
import struct


#: ``struct`` formats are not worth it for four bytes; these read big/little endian directly.
def _be16(data, offset):
    return (data[offset] << 8) | data[offset + 1]


def _be32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def _le16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def image_size(data, content_type=""):
    """``(width, height)`` as *data* declares them, or ``(0, 0)`` when it declares nothing."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 10:
        return (0, 0)
    data = bytes(data)
    kind = str(content_type or "").split(";")[0].strip().lower()
    if data.startswith(b"\x89PNG\r\n\x1a\n") or kind == "image/png":
        return _png_size(data)
    if data[:6] in (b"GIF87a", b"GIF89a") or kind == "image/gif":
        return _gif_size(data)
    if data.startswith(b"\xff\xd8") or kind in ("image/jpeg", "image/jpg"):
        return _jpeg_size(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_size(data)
    if data.lstrip()[:5] in (b"<?xml", b"<svg ") or kind == "image/svg+xml":
        return _svg_size(data)
    return (0, 0)


def _png_size(data):
    """The ``IHDR`` chunk: width and height are the first two fields, big endian."""
    if len(data) < 24 or data[12:16] != b"IHDR":
        return (0, 0)
    return (_be32(data, 16), _be32(data, 20))


def _gif_size(data):
    """The logical screen descriptor, little endian."""
    if len(data) < 10:
        return (0, 0)
    return (_le16(data, 6), _le16(data, 8))


def _jpeg_size(data):
    """Walk the markers to the frame header (``SOF``), which carries both dimensions."""
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        length = _be16(data, index + 2)
        #: Every ``SOF`` except the three that are not frames at all.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return (_be16(data, index + 7), _be16(data, index + 5))
        index += 2 + max(2, length)
    return (0, 0)


def _webp_size(data):
    """Three chunk layouts, each storing the size differently."""
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return (width, height)
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8 " and len(data) >= 30:
        return (_le16(data, 26) & 0x3FFF, _le16(data, 28) & 0x3FFF)
    return (0, 0)


def _svg_size(data):
    """A vector image: what it declares, in attributes or in the ``viewBox``."""
    import re

    head = data[:4096].decode("utf-8", "replace")
    tag = re.search(r"<svg\b[^>]*>", head, re.I)
    if tag is None:
        return (0, 0)
    attributes = tag.group(0)

    def length(name):
        found = re.search(name + r'\s*=\s*["\']\s*([0-9.]+)', attributes, re.I)
        return int(round(float(found.group(1)))) if found else 0

    width, height = length("width"), length("height")
    if width and height:
        return (width, height)
    box = re.search(r'viewBox\s*=\s*["\']([^"\']+)["\']', attributes, re.I)
    if box:
        numbers = [float(part) for part in
                   re.split(r"[\s,]+", box.group(1).strip()) if part]
        if len(numbers) == 4 and numbers[2] > 0 and numbers[3] > 0:
            return (int(round(numbers[2])), int(round(numbers[3])))
    return (0, 0)

__all__ = ["image_size"]
