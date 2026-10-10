"""TextEncoder and TextDecoder: the labels the encoding standard names, decoded by codec.

A label is not a codec name: ``iso-8859-1`` is *windows-1252* in the standard -- not Latin-1 --
so a page that asks for it and gets the codec under that label sees the difference on byte 0x80.
Only ``utf-8`` was modelled before, which made ``new TextDecoder('iso-8859-1')`` raise where
Chrome answers (measured against Cloudflare's widget, 2026-10-06).  The table below is the
standard's own: the canonical name ``encoding`` reports, and the labels that alias it.
"""
from __future__ import annotations

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, dictionary, is_nullish, missing_error, usv_string
from ..page_errors import PageTypeError, dom_exception

__all__ = ["TextDecoder", "TextEncoder"]


#: The encoding standard's table: canonical name -> (python codec, aliasing labels).  ``None``
#: marks the two codecs the standard defines without one.
_ENCODINGS = {
    "utf-8": ("utf-8", ("unicode-1-1-utf-8", "unicode11utf8", "unicode20utf8", "utf8",
                        "x-unicode20utf8")),
    "ibm866": ("cp866", ("866", "cp866", "csibm866", "ibm866")),
    "iso-8859-2": ("iso8859-2", ("csisolatin2", "iso-8859-2", "iso-ir-101", "iso8859-2",
                                 "iso88592", "iso_8859-2", "iso_8859-2:1987", "l2", "latin2")),
    "iso-8859-3": ("iso8859-3", ("csisolatin3", "iso-8859-3", "iso-ir-109", "iso8859-3",
                                 "iso88593", "iso_8859-3", "iso_8859-3:1988", "l3", "latin3")),
    "iso-8859-4": ("iso8859-4", ("csisolatin4", "iso-8859-4", "iso-ir-110", "iso8859-4",
                                 "iso88594", "iso_8859-4", "iso_8859-4:1988", "l4", "latin4")),
    "iso-8859-5": ("iso8859-5", ("csisolatincyrillic", "cyrillic", "iso-8859-5", "iso-ir-144",
                                 "iso8859-5", "iso88595", "iso_8859-5", "iso_8859-5:1988")),
    "iso-8859-6": ("iso8859-6", ("arabic", "asmo-708", "csiso88596e", "csiso88596i",
                                 "csisolatinarabic", "ecma-114", "iso-8859-6", "iso-8859-6-e",
                                 "iso-8859-6-i", "iso-ir-127", "iso8859-6", "iso88596",
                                 "iso_8859-6", "iso_8859-6:1987")),
    "iso-8859-7": ("iso8859-7", ("csisolatingreek", "ecma-118", "elot_928", "greek", "greek8",
                                 "iso-8859-7", "iso-ir-126", "iso8859-7", "iso88597",
                                 "iso_8859-7", "iso_8859-7:1987", "sun_eu_greek")),
    "iso-8859-8": ("iso8859-8", ("csiso88598e", "csisolatinhebrew", "hebrew", "iso-8859-8",
                                 "iso-8859-8-e", "iso-ir-138", "iso8859-8", "iso88598",
                                 "iso_8859-8", "iso_8859-8:1988", "visual")),
    "iso-8859-10": ("iso8859-10", ("csisolatin6", "iso-8859-10", "iso-ir-157", "iso8859-10",
                                   "iso885910", "l6", "latin6")),
    "iso-8859-13": ("iso8859-13", ("iso-8859-13", "iso8859-13", "iso885913")),
    "iso-8859-14": ("iso8859-14", ("iso-8859-14", "iso8859-14", "iso885914")),
    "iso-8859-15": ("iso8859-15", ("csisolatin9", "iso-8859-15", "iso8859-15", "iso885915",
                                   "iso_8859-15", "l9")),
    "iso-8859-16": ("iso8859-16", ("iso-8859-16",)),
    "koi8-r": ("koi8-r", ("cskoi8r", "koi", "koi8", "koi8-r", "koi8_r")),
    "koi8-u": ("koi8-u", ("koi8-ru", "koi8-u")),
    "macintosh": ("mac-roman", ("csmacintosh", "mac", "macintosh", "x-mac-roman")),
    "windows-874": ("cp874", ("dos-874", "iso-8859-11", "iso8859-11", "iso885911", "tis-620",
                              "windows-874")),
    "windows-1250": ("cp1250", ("cp1250", "windows-1250", "x-cp1250")),
    "windows-1251": ("cp1251", ("cp1251", "windows-1251", "x-cp1251")),
    "windows-1252": ("cp1252", ("ansi_x3.4-1968", "ascii", "cp1252", "cp819", "csisolatin1",
                                "ibm819", "iso-8859-1", "iso-ir-100", "iso8859-1", "iso88591",
                                "iso_8859-1", "iso_8859-1:1987", "l1", "latin1", "us-ascii",
                                "windows-1252", "x-cp1252")),
    "windows-1253": ("cp1253", ("cp1253", "windows-1253", "x-cp1253")),
    "windows-1254": ("cp1254", ("cp1254", "csisolatin5", "iso-8859-9", "iso-ir-148", "iso8859-9",
                                "iso88599", "iso_8859-9", "iso_8859-9:1989", "l5", "latin5",
                                "windows-1254", "x-cp1254")),
    "windows-1255": ("cp1255", ("cp1255", "windows-1255", "x-cp1255")),
    "windows-1256": ("cp1256", ("cp1256", "windows-1256", "x-cp1256")),
    "windows-1257": ("cp1257", ("cp1257", "windows-1257", "x-cp1257")),
    "windows-1258": ("cp1258", ("cp1258", "windows-1258", "x-cp1258")),
    "x-mac-cyrillic": ("mac-cyrillic", ("x-mac-cyrillic", "x-mac-ukrainian")),
    "gbk": ("gbk", ("chinese", "csgb2312", "csiso58gb231280", "gb2312", "gb_2312", "gb_2312-80",
                    "gbk", "iso-ir-58", "x-gbk")),
    "gb18030": ("gb18030", ("gb18030",)),
    "big5": ("big5", ("big5", "big5-hkscs", "cn-big5", "csbig5", "x-x-big5")),
    "euc-jp": ("euc_jp", ("cseucpkdfmtjapanese", "euc-jp", "x-euc-jp")),
    "iso-2022-jp": ("iso2022_jp", ("csiso2022jp", "iso-2022-jp")),
    "shift_jis": ("shift_jis", ("csshiftjis", "ms932", "ms_kanji", "shift-jis", "shift_jis",
                                "sjis", "windows-31j", "x-sjis")),
    "euc-kr": ("euc_kr", ("cseuckr", "csksc56011987", "euc-kr", "iso-ir-149", "korean",
                          "ks_c_5601-1987", "ks_c_5601-1989", "ksc5601", "ksc_5601",
                          "windows-949")),
    "replacement": (None, ("csiso2022kr", "hz-gb-2312", "iso-2022-cn", "iso-2022-cn-ext",
                           "iso-2022-kr", "replacement")),
    "utf-16be": ("utf-16-be", ("unicodefffe", "utf-16be")),
    "utf-16le": ("utf-16-le", ("csunicode", "iso-10646-ucs-2", "ucs-2", "unicode", "unicodefeff",
                               "utf-16", "utf-16le")),
    "x-user-defined": (None, ("x-user-defined",)),
}


#: label -> canonical name, built once from the table above.
_LABELS = {}
for _name, (_codec, _aliases) in _ENCODINGS.items():
    _LABELS[_name] = _name
    for _alias in _aliases:
        _LABELS[_alias] = _name
del _name, _codec, _aliases, _alias

_BOM = b"\xef\xbb\xbf"


def _view(value):
    """The ArrayBufferView a caller passed, or ``None`` for anything else."""
    return value if isinstance(value, pyv8.JSTypeArray) else None


def _bytes_of(value):
    """The bytes of an ArrayBufferView, or ``None`` when the value is not one."""
    view = _view(value)
    if view is None:
        return bytes(value) if isinstance(value, (bytes, bytearray)) else None
    return bytes(int(view[index]) for index in range(int(view._get("byteLength"))))


def _code_units(character):
    """How many UTF-16 code units one code point occupies."""
    return 2 if ord(character) > 0xFFFF else 1


@interface('TextEncoder', None)
class TextEncoder:
    def __init__(self, *_extra, **_):
        # Chrome's constructor takes no arguments; the binding still passes the realm.
        pass

    def get_encoding(self): return "utf-8"

    def fn_encode(self, source=pyv8.Undefined, *_extra):
        text = "" if source is pyv8.Undefined else usv_string(source)
        return pyv8.JSTypeArray(list(text.encode("utf-8")), pyv8.ArrayType.Uint8Array)

    def fn_encodeInto(self, source=MISSING, destination=MISSING, *_extra):
        if source is MISSING:
            raise missing_error('TextEncoder', 'encodeInto', 2, 0, constructor=False)
        if destination is MISSING:
            raise missing_error('TextEncoder', 'encodeInto', 2, 1, constructor=False)
        target = _view(destination)
        if target is None:
            raise PageTypeError("Failed to execute 'encodeInto' on 'TextEncoder': "
                              "parameter 2 is not of type 'Uint8Array'.")
        text = usv_string(source)
        room = int(target._get("length"))
        written, read = 0, 0
        for character in text:
            encoded = character.encode("utf-8")
            # A code point is written whole or not at all, which is what ``read`` counts.
            if written + len(encoded) > room:
                break
            for offset, byte in enumerate(encoded):
                target[written + offset] = byte
            written += len(encoded)
            read += _code_units(character)
        return {"read": read, "written": written}


@interface('TextDecoder', None)
class TextDecoder:
    def __init__(self, label="utf-8", options=None, *_extra, **_):
        text = "utf-8" if is_nullish(label) else str(label).strip().lower()
        #: The standard matches a label with its ASCII whitespace removed.
        name = _LABELS.get("".join(text.split()))
        if name is None:
            raise dom_exception("RangeError",
                                "Failed to construct 'TextDecoder': The encoding label"
                                f" provided ('{text}') is invalid.")
        self._name = name
        self._codec = _ENCODINGS[name][0]
        options = {} if is_nullish(options) else dictionary(options, name="TextDecoderOptions")
        self._fatal = bool(options.get("fatal", False))
        self._ignore_bom = bool(options.get("ignoreBOM", False))

    def get_encoding(self): return self._name
    def get_fatal(self): return self._fatal
    def get_ignoreBOM(self): return self._ignore_bom

    def fn_decode(self, input=pyv8.Undefined, options=None, *_extra):
        data = b"" if input is pyv8.Undefined or is_nullish(input) else _bytes_of(input)
        if data is None:
            raise PageTypeError("Failed to execute 'decode' on 'TextDecoder': "
                              "parameter 1 is not of type 'BufferSource'.")
        if self._codec is None:
            #: The two the standard defines without one.
            if self._name == "replacement":
                return "\ufffd" * len(data)
            return "".join(chr(0xF780 + byte) for byte in data)
        if not self._ignore_bom:
            if self._name == "utf-8" and data.startswith(_BOM):
                data = data[len(_BOM):]
            elif self._name.startswith("utf-16") and data[:2] in (b"\xff\xfe", b"\xfe\xff"):
                data = data[2:]
        try:
            return data.decode(self._codec, "strict" if self._fatal else "replace")
        except (UnicodeDecodeError, LookupError) as error:
            raise PageTypeError("Failed to execute 'decode' on 'TextDecoder': "
                              "The encoded data was not valid.") from error
