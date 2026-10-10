"""``forgiving-base64``: the algorithms ``atob``/``btoa`` run.

The Infra rules (drop ASCII whitespace, forgive padding, reject a bad length or
character) live here; the bit work goes to :mod:`binascii`.  Encoding takes a string
whose code points are already bytes -- the caller owns the Latin1 check.
"""
from __future__ import annotations

import binascii
import re

ASCII_WHITESPACE = "\t\n\x0c\r "
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_NOT_IN_ALPHABET = re.compile("[^%s]" % re.escape(ALPHABET))
_DROP_WHITESPACE = {ord(character): None for character in ASCII_WHITESPACE}


def forgiving_base64_decode(text):
    """The bytes *text* carries, or ``None`` where the standard says failure."""
    data = text.translate(_DROP_WHITESPACE)
    if len(data) % 4 == 0:
        if data.endswith("=="):
            data = data[:-2]
        elif data.endswith("="):
            data = data[:-1]
    if len(data) % 4 == 1 or _NOT_IN_ALPHABET.search(data):
        return None
    return binascii.a2b_base64(data + "=" * (-len(data) % 4)).decode("latin-1")


def forgiving_base64_encode(data):
    """*data* as padded RFC 4648 base64."""
    return binascii.b2a_base64(data.encode("latin-1"), newline=False).decode("ascii")


__all__ = ["ASCII_WHITESPACE", "ALPHABET", "forgiving_base64_decode", "forgiving_base64_encode"]
