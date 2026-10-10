"""``MediaSource.isTypeSupported``: the codecs a media source can carry.

A media source is only supported for a container when the codecs it names are ones
Chrome was built with, so the table is the reference build's codec list.
"""
import re

from ...idl import interface
from ...foundation.arguments import MISSING, dom_string, missing_error
from .events import EventTarget


#: ``audio/*`` or ``video/*`` with the type's own characters: Chrome refuses anything else.
_TYPE = re.compile(r"^(?:audio|video)/[a-z0-9][a-z0-9!#$&^_.+-]*$")


#: The codec families Chrome answers true for, by container; a family matches by equality.
CONTAINER_CODECS = {
    "audio/mp4": ("mp4a",),
    "audio/webm": ("opus", "vorbis"),
    "video/mp4": ("avc1", "avc3", "hvc1", "hev1", "vp09", "av01", "mp4a"),
    "video/webm": ("vp8", "vp9", "vp09", "av01", "opus", "vorbis"),
}


#: Types that are complete on their own, so a media source carries them with no codecs.
SELF_CONTAINED = ("audio/mpeg",)


#: What ``canPlayType`` answers per container: the bare answer and the families it plays there.
#: A looser table than ``isTypeSupported`` reads, which is why ``audio/wav; codecs="1"`` is
#: "probably" yet not a media source.
PLAY_CONTAINERS = {
    "audio/mpeg": ("probably", ("mp3",)),
    #: ``audio/mp3`` is only an alias for the bare name; Chromium does not match codecs in it.
    "audio/mp3": ("probably", ()),
    "audio/aac": ("probably", ()),
    "audio/flac": ("probably", ()),
    "audio/mp4": ("maybe", ("mp4a", "opus", "flac", "mp3")),
    "audio/ogg": ("maybe", ("vorbis", "opus", "flac")),
    "audio/wav": ("maybe", ("1",)),
    "audio/x-wav": ("maybe", ("1",)),
    "audio/webm": ("maybe", ("vorbis", "opus")),
    "video/mp4": ("maybe", ("avc1", "avc3", "hvc1", "hev1", "vp09", "av01", "mp4a", "opus",
                            "flac", "mp3")),
    "video/webm": ("maybe", ("vp8", "vp9", "vp09", "av01", "opus", "vorbis")),
    "video/ogg": ("maybe", ("opus", "vorbis", "vp8", "flac")),
    "application/ogg": ("maybe", ("vorbis", "opus", "flac", "vp8")),
}


def _six_hex(text):
    """``avc1.42E01E``: Chromium wants exactly six hex digits after the dot."""
    return len(text) == 6 and all(character in "0123456789abcdefABCDEF" for character in text)


def _components(text):
    """``hvc1.1.6.L93.B0``: at least one non-empty component after the dot."""
    return bool(text) and all(part for part in text.split("."))


#: Codecs whose meaning is in the structure after the dot; the name before it is the family.
_STRUCTURED_CODECS = {
    "avc1": _six_hex,
    "avc3": _six_hex,
    "hvc1": _components,
    "hev1": _components,
    "vp09": _components,
    "av01": _components,
    "mp4a": _components,
}


#: Codecs that are a whole answer on their own; the name is case sensitive, as in Chromium.
_SIMPLE_CODECS = ("vp8", "vp9", "opus", "vorbis", "flac", "mp3", "1")


#: Names Chromium calls *ambiguous*: "maybe" for ``canPlayType``, refused by ``isTypeSupported``.
_AMBIGUOUS_CODECS = {"avc1": "avc1", "avc3": "avc3", "mp4a.40": "mp4a"}


#: The ``video/VP8`` style content types the WebRTC path uses: registry name, and its family.
#: Chromium matches the name exactly.
WEBRTC_VIDEO_FAMILIES = {"VP8": "vp8", "VP9": "vp09", "AV1": "av01", "H264": "avc1",
                         "H265": "hvc1"}


#: The audio side of that registry, with no family: Chromium calls them all power efficient.
WEBRTC_AUDIO_CODECS = ("opus", "G722", "PCMU", "PCMA")


def webrtc_codec(content_type):
    """``(kind, family)`` for a WebRTC content type this vocabulary knows, else ``None``.

    ``family`` is ``None`` for the audio codecs, which need no hardware.
    """
    kind, _, codec = str(content_type).partition("/")
    if kind == "video" and codec in WEBRTC_VIDEO_FAMILIES:
        return kind, WEBRTC_VIDEO_FAMILIES[codec]
    if kind == "audio" and codec in WEBRTC_AUDIO_CODECS:
        return kind, None
    return None


#: The WebRTC content types a collection asks about: every name the vocabulary above holds.
WEBRTC_CONTENT_TYPES = tuple([f"video/{name}" for name in WEBRTC_VIDEO_FAMILIES]
                             + [f"audio/{name}" for name in WEBRTC_AUDIO_CODECS])


def webrtc_supported(content_type, direction, services):
    """Whether the reference browser carries this WebRTC type in this direction.

    A profile collected before these keys existed has no answer, so the vocabulary decides --
    every name above is one the reference build carries.
    """
    facts = media_facts(services)
    carried = () if facts is None else tuple(facts.webrtc.get(direction, ()))
    if carried:
        return str(content_type) in carried
    return webrtc_codec(content_type) is not None


def hardware_encoders(services):
    """The codec families the collected facts encode in hardware.

    Separate from ``hardware_codecs``: a build decodes H.264 in hardware and still encodes it
    in software, which is what Chrome answers here.
    """
    facts = media_facts(services)
    return () if facts is None else tuple(facts.hardware_encode)


#: One representative codec string per family: how a probe asks about that family.
FAMILY_EXAMPLES = {
    "avc1": "avc1.42E01E",
    "avc3": "avc3.42E01E",
    "hvc1": "hvc1.1.6.L93.B0",
    "hev1": "hev1.1.6.L93.B0",
    "vp09": "vp09.00.10.08",
    "av01": "av01.0.04M.08",
    "mp4a": "mp4a.40.2",
    "vp8": "vp8",
    "vp9": "vp9",
    "opus": "opus",
    "vorbis": "vorbis",
    "flac": "flac",
    "mp3": "mp3",
    "1": "1",
}


def probe_families():
    """Every codec family this parser reads, in a stable order."""
    return tuple(dict.fromkeys([*_STRUCTURED_CODECS, *_SIMPLE_CODECS,
                                *_AMBIGUOUS_CODECS.values()]))


def probe_containers():
    """Every container this parser reads, in a stable order."""
    return tuple(dict.fromkeys([*CONTAINER_CODECS, *PLAY_CONTAINERS, *SELF_CONTAINED]))


def probe_candidates():
    """``container -> probes``: the bare container, then one probe per codec family.

    Generated from the two vocabularies above, so a collected profile answers for every
    string this parser can read: a family or container added to the tables widens the next
    collection rather than being missed by a hand-written list.
    """
    absent = [family for family in probe_families() if family not in FAMILY_EXAMPLES]
    if absent:
        raise ValueError(f"no probe codec for family: {', '.join(absent)}")
    return {container: [container]
            + [f'{container}; codecs="{FAMILY_EXAMPLES[family]}"'
               for family in probe_families()]
            for container in probe_containers()}


def codec_family(codec):
    """``(family, is_ambiguous)`` for one codec string, or ``(None, False)`` when unreadable."""
    family = _AMBIGUOUS_CODECS.get(codec)
    if family is not None:
        return family, True
    for prefix, valid in _STRUCTURED_CODECS.items():
        if codec.startswith(prefix + "."):
            return (prefix, False) if valid(codec[len(prefix) + 1:]) else (None, False)
    return (codec, False) if codec in _SIMPLE_CODECS else (None, False)


def parse_type(text):
    """``(container, codec strings)``: the container trimmed and lowered, the codecs as written.

    Chromium trims inside the quotes as well, which is why ``codecs=" avc1.42E01E "`` is one.
    """
    container, _, parameters = str(text).partition(";")
    for parameter in parameters.split(";"):
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "codecs":
            return container.strip().lower(), [part.strip().strip('"').strip()
                                               for part in value.split(",") if part.strip()]
    return container.strip().lower(), []


def families_in(text):
    """The codec families a type string names; codecs we cannot read are left out."""
    _container, codecs = parse_type(text)
    return tuple(family for family, _ambiguous in (codec_family(codec) for codec in codecs)
                 if family is not None)


def _families(container, facts):
    """The codec families a container plays, or None when it is not one we know."""
    if facts is not None and container in facts.containers:
        return tuple(facts.containers[container])
    return CONTAINER_CODECS.get(container)


def _self_contained(facts):
    """The containers that carry no codecs at all, as collected when a profile has them."""
    if facts is not None and facts.self_contained:
        return tuple(facts.self_contained)
    return SELF_CONTAINED


def media_facts(services):
    """The collected media facts of this realm, or None when the profile carries none."""
    realm = getattr(services, "realm_services", None)
    environment = getattr(realm, "environment", None) if realm is not None else None
    return getattr(environment, "media", None) if environment is not None else None


def hardware_codecs(services):
    """The codec prefixes the collected facts decode in hardware."""
    facts = media_facts(services)
    return () if facts is None else tuple(facts.hardware_decode)


def device_kinds(services):
    """The capture devices the collected facts list, by kind, in the machine's own order.

    A machine without a profile lists nothing: a guessed device would be a device the page
    can then ask for by name.
    """
    facts = media_facts(services)
    return () if facts is None else tuple(facts.devices)


def is_type_supported(text, facts=None):
    """Whether this build decodes ``type/subtype; codecs=...``.

    The strict question, as Chromium reads it: every codec named has to be one the container
    carries, and an *ambiguous* name (a bare ``avc1``, ``mp4a.40``) is refused -- that is the
    case ``canPlayType`` answers "maybe" to instead.

    ``facts`` is what a profile collected; a container it does not mention falls back to the
    built-in table, so a profile collected before these keys existed behaves as it did.
    """
    container, codecs = parse_type(text)
    if not _TYPE.match(container):
        return False
    if not codecs:
        return container in _self_contained(facts)
    families = _families(container, facts)
    if families is None:
        return False
    for codec in codecs:
        family, ambiguous = codec_family(codec)
        if family is None or ambiguous or family not in families:
            return False
    return True


def can_play_type(text, facts=None):
    """``"probably"`` / ``"maybe"`` / ``""``: what ``canPlayType`` answers."""
    container, codecs = parse_type(text)
    answer = PLAY_CONTAINERS.get(container)
    if answer is None:
        return ""
    bare, families = answer
    if not codecs:
        return bare
    ambiguous = False
    for codec in codecs:
        family, is_ambiguous = codec_family(codec)
        if family is None or family not in families:
            return ""
        ambiguous = ambiguous or is_ambiguous
    return "maybe" if ambiguous else "probably"


@interface('MediaSource', 'EventTarget')
class MediaSource(EventTarget):
    @staticmethod
    def fn_isTypeSupported(_realm, type=MISSING, *_extra):
        if type is MISSING:
            raise missing_error('MediaSource', 'isTypeSupported', 1, 0, constructor=False)
        return is_type_supported(dom_string(type), media_facts(_realm))

__all__ = ["MediaSource", "WEBRTC_CONTENT_TYPES", "can_play_type", "codec_family", "device_kinds",
           "families_in", "hardware_codecs", "hardware_encoders", "is_type_supported",
           "media_facts", "parse_type", "probe_candidates", "probe_containers", "probe_families",
           "webrtc_codec", "webrtc_supported"]
