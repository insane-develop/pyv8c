"""``<audio>`` and ``<video>``, with the lists their playback state exposes.
Nothing decodes here, so the classes report what a fresh element reports."""
import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, boolean, dom_string, missing_error, number
from ..page_errors import dom_exception
from .events import EventTarget
from .html_elements import HTMLElement, _set_url_value, _url_value
from .media_source import can_play_type, media_facts


#: The loose table and the codec parser live with the strict one, so the two questions cannot
#: drift apart; see :mod:`media_source`.


@interface('TimeRanges', None)
class TimeRanges:
    """A media element's ranges: nothing has loaded, so there are none."""

    def get_length(self): return 0
    def fn_start(self, index=MISSING, *_extra): return self._refuse('start', index)
    def fn_end(self, index=MISSING, *_extra): return self._refuse('end', index)

    @staticmethod
    def _refuse(member, index):
        if index is MISSING:
            raise missing_error('TimeRanges', member, 1, 0, constructor=False)
        raise dom_exception("IndexSizeError",
                            f"Failed to execute '{member}' on 'TimeRanges': There is no range"
                            " at that index.")


@interface('TextTrackList', 'EventTarget')
class TextTrackList(EventTarget):
    """A media element's text tracks: with nothing loaded there are none."""

    def get_length(self): return 0


@interface('HTMLMediaElement', 'HTMLElement')
class HTMLMediaElement(HTMLElement):
    """``<audio>`` and ``<video>``: the playback state a page reads.  Nothing decodes,
    so it is what a fresh element reports; ``canPlayType`` matches Chrome's answers."""

    def _media(self, name, default):
        values = getattr(self, "_media_values", None)
        if values is None:
            values = self._media_values = {}
        return values.get(name, default)

    def _set_media(self, name, value):
        self._media(name, None)
        self._media_values[name] = value

    def _flag(self, name): return name in self.attributes
    def _set_flag(self, name, value):
        if boolean(value): self.attributes[name] = ""
        else: self.attributes.pop(name, None)

    def get_src(self): return _url_value(self, "src")
    def set_src(self, value): _set_url_value(self, "src", value)
    def get_currentSrc(self): return ""          # nothing was ever loaded
    def get_crossOrigin(self): return self.attributes.get("crossorigin") or pyv8.Null
    def get_paused(self): return True
    def get_ended(self): return False
    def get_currentTime(self): return float(self._media("currentTime", 0.0))
    def set_currentTime(self, value): self._set_media("currentTime", float(number(value)))
    def get_duration(self): return float("nan")
    def get_readyState(self): return 0
    def get_networkState(self): return 0
    def get_volume(self): return float(self._media("volume", 1.0))
    def set_volume(self, value): self._set_media("volume", float(number(value)))
    def get_muted(self): return self._flag("muted")
    def set_muted(self, value): self._set_flag("muted", value)
    def get_loop(self): return self._flag("loop")
    def set_loop(self, value): self._set_flag("loop", value)
    def get_autoplay(self): return self._flag("autoplay")
    def set_autoplay(self, value): self._set_flag("autoplay", value)
    def get_controls(self): return self._flag("controls")
    def set_controls(self, value): self._set_flag("controls", value)
    def get_defaultPlaybackRate(self): return 1.0
    def get_playbackRate(self): return float(self._media("playbackRate", 1.0))
    def set_playbackRate(self, value): self._set_media("playbackRate", float(number(value)))
    def get_seeking(self): return False
    def get_error(self): return pyv8.Null
    def get_preload(self): return str(self.attributes.get("preload") or "metadata")

    def _empty(self, name, factory):
        value = getattr(self, name, None)
        if value is None:
            value = setattr(self, name, factory())
            value = getattr(self, name)
        return value

    def get_textTracks(self): return self._empty("_text_tracks", TextTrackList)
    def get_buffered(self): return self._empty("_buffered", TimeRanges)
    def get_played(self): return self._empty("_played", TimeRanges)
    def get_seekable(self): return self._empty("_seekable", TimeRanges)

    def fn_canPlayType(self, type=MISSING, *_extra):
        if type is MISSING:
            raise missing_error('HTMLMediaElement', 'canPlayType', 1, 0, constructor=False)
        return can_play_type(dom_string(type), media_facts(self._services))

    def fn_play(self, *_extra):
        realm = getattr(self._services, "realm_services", None)
        return realm.promise_resolve(None) if realm is not None else None
    def fn_pause(self, *_extra): return None
    def fn_load(self, *_extra): return None


@interface('HTMLAudioElement', 'HTMLMediaElement')
class HTMLAudioElement(HTMLMediaElement):
    pass


@interface('HTMLVideoElement', 'HTMLMediaElement')
class HTMLVideoElement(HTMLMediaElement):
    pass

__all__ = ["TimeRanges", "TextTrackList", "HTMLMediaElement", "HTMLAudioElement", "HTMLVideoElement"]
