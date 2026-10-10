"""Window's smaller accessory objects; each reports the idle state a headless
page starts in.
"""

import re

import pyv8
from ...idl import interface
from ...foundation.arguments import (MISSING, callback_value, dictionary, dom_string,
                                     missing_error, number)
from ...foundation.environment import environment_of
from ...foundation.arguments import IdlArgumentError
from ..page_errors import PageTypeError, dom_exception
from ..page_errors import js_error_value
from ..dom.events import Event, EventTarget
from ..dom.media_source import (families_in, hardware_codecs, hardware_encoders,
                                is_type_supported, media_facts, webrtc_codec, webrtc_supported)
from .media import evaluate
from .utils import settled


@interface('External', None)
class External:
    #: No provider is registered, so nothing is added and nothing is installed.
    def fn_AddSearchProvider(self, *_extra): return None
    def fn_IsSearchProviderInstalled(self, *_extra): return 0


@interface('StyleMedia', None)
class StyleMedia:
    def __init__(self, window=None, **_):
        self._window = window

    def get_type(self): return "screen"

    def fn_matchMedium(self, query=MISSING, *_extra):
        if query is MISSING:
            raise missing_error('StyleMedia', 'matchMedium', 1, 0, constructor=False)
        return evaluate(dom_string(query), self._window.innerWidth, self._window.innerHeight)


@interface('CrashReportContext', None)
class CrashReportContext:
    #: Nothing here reports a crash.
    def fn_delete(self, *_extra): return None
    def fn_initialize(self, *_extra): return None
    def fn_set(self, *_extra): return None


@interface('LaunchQueue', None)
class LaunchQueue:
    def __init__(self, **_):
        self._consumer = None

    def fn_setConsumer(self, callback=MISSING, *_extra):
        """A consumer is kept, and only called when a launch is consumed."""
        if callback is MISSING:
            raise missing_error('LaunchQueue', 'setConsumer', 1, 0, constructor=False)
        self._consumer = callback_value(callback)


@interface('Viewport', None)
class Viewport:
    #: One segment covering the whole view, split into none.
    def get_segments(self): return []


#: The voices of the machine the profile was collected on; ``speech.voices`` replaces them.
_SPEECH_VOICES = (
    ("Microsoft Huihui - Chinese (Simplified, PRC)", "zh-CN", True, True),
    ("Microsoft Kangkang - Chinese (Simplified, PRC)", "zh-CN", True, False),
    ("Microsoft Yaoyao - Chinese (Simplified, PRC)", "zh-CN", True, False),
)


def _voice_entries(services):
    """``(name, lang, local, default)`` per voice, collected or the table above."""
    environment = environment_of(services)
    collected = getattr(environment, "speech", None) if environment is not None else None
    if collected is None or not collected.voices:
        return _SPEECH_VOICES
    return tuple((entry["name"], entry["lang"], entry["local_service"], entry["default"])
                 for entry in collected.voices)


#: How long Chrome's speech service takes to answer: a page reads the empty list
#: first and hears about the voices after this, which is when Chrome fires them.
_VOICES_READY_SECONDS = 0.27


@interface('SpeechSynthesisVoice', None)
class SpeechSynthesisVoice:
    """One voice the synthesiser can speak with."""

    def __init__(self, name="", lang="", local_service=True, default=False, **_):
        self._name, self._lang = str(name), str(lang)
        self._local_service, self._default = bool(local_service), bool(default)

    #: A voice's URI reads back as its name, which is how Chrome's platform voices look.
    def get_voiceURI(self): return self._name
    def get_name(self): return self._name
    def get_lang(self): return self._lang
    def get_localService(self): return self._local_service
    def get_default(self): return self._default


@interface('SpeechSynthesis', 'EventTarget')
class SpeechSynthesis(EventTarget):
    def __init__(self, services=None, **_):
        super().__init__(services=services)
        #: None until the service answers, then the voices every later read hands out.
        self._voices = None
        if services is not None:
            services.task_services.schedule(self._voices_ready, _VOICES_READY_SECONDS)

    #: Nothing is spoken: the voices are there to be read, never to be used.
    def get_pending(self): return False
    def get_speaking(self): return False
    def get_paused(self): return False
    def get_onvoiceschanged(self): return self._get_handler("onvoiceschanged")
    def set_onvoiceschanged(self, value): self._set_handler("onvoiceschanged", value)
    def fn_cancel(self, *_extra): return None
    def _voices_ready(self):
        self._voices = [SpeechSynthesisVoice(name=name, lang=lang, local_service=local,
                                             default=default, services=self._services)
                        for name, lang, local, default in _voice_entries(self._services)]
        self.fn_dispatchEvent(Event("voiceschanged"))
    def fn_getVoices(self, *_extra):
        return [] if self._voices is None else list(self._voices)
    def fn_pause(self, *_extra): return None
    def fn_resume(self, *_extra): return None
    def fn_speak(self, utterance=MISSING, *_extra):
        if utterance is MISSING:
            raise missing_error('SpeechSynthesis', 'speak', 1, 0, constructor=False)
        return None


@interface('Notification', 'EventTarget')
class Notification(EventTarget):
    #: No user is here to answer the prompt, so the page keeps the initial state.
    @staticmethod
    def get_permission(*_args): return "default"

    #: What Chrome allows a notification to carry.
    @staticmethod
    def get_maxActions(*_args): return 2


@interface('Scheduling', None)
class Scheduling:
    def fn_isInputPending(self, *_extra): return False


@interface('Scheduler', None)
class Scheduler:
    def __init__(self, services=None, **_):
        self._services = services

    def fn_postTask(self, callback=MISSING, options=None, *_extra):
        if callback is MISSING:
            raise missing_error('Scheduler', 'postTask', 1, 0, constructor=False)
        callback = callback_value(callback)
        return self._on_task(callback)

    def fn_yield(self, *_extra):
        return self._on_task(None)

    def _on_task(self, callback):
        services = self._services
        promise, resolve, reject = services.realm_services.make_promise()

        def run():
            try:
                resolve(callback() if callback is not None else None)
            except BaseException as error:
                reject(js_error_value(error))

        services.task_services.schedule(run, 0, repeat=False)
        return promise


def _empty_rect(owner):
    """The ``0,0,0,0`` DOMRect Chrome reports for a box it is not drawing."""
    value = getattr(owner, "_empty_rect_value", None)
    if value is None:
        from ..dom.rect import DOMRect
        value = owner._empty_rect_value = DOMRect()
    return value


@interface('UserActivation', None)
class UserActivation:
    """A freshly loaded page has had no gesture, so both flags are false."""

    def get_isActive(self): return False
    def get_hasBeenActive(self): return False


@interface('DevicePosture', 'EventTarget')
class DevicePosture(EventTarget):
    """One flat display: the posture is continuous."""

    def __init__(self, services=None, **_):
        super().__init__(services=services)

    def get_type(self): return "continuous"
    def get_onchange(self): return self._get_handler("onchange")
    def set_onchange(self, value): self._set_handler("onchange", value)


@interface('ProtectedAudience', None)
class ProtectedAudience:
    """No feature table of our own, so every query answers undefined."""

    def fn_queryFeatureSupport(self, feature=MISSING, *_extra):
        if feature is MISSING:
            raise missing_error('ProtectedAudience', 'queryFeatureSupport', 1, 0,
                                constructor=False)
        return pyv8.Undefined


@interface('Presentation', None)
class Presentation:
    """Nothing was ever presented, so both members read null."""

    def get_defaultRequest(self): return pyv8.Null
    def get_receiver(self): return pyv8.Null


@interface('VirtualKeyboard', 'EventTarget')
class VirtualKeyboard(EventTarget):
    """No on-screen keyboard: an empty box, never overlaid, with nothing to show."""

    def __init__(self, services=None, **_):
        super().__init__(services=services)
        self._overlays = False

    def get_boundingRect(self): return _empty_rect(self)
    def get_overlaysContent(self): return self._overlays
    def set_overlaysContent(self, value): self._overlays = bool(value)
    def get_ongeometrychange(self): return self._get_handler("ongeometrychange")
    def set_ongeometrychange(self, value): self._set_handler("ongeometrychange", value)
    def fn_show(self, *_extra): return pyv8.Undefined
    def fn_hide(self, *_extra): return pyv8.Undefined


@interface('WindowControlsOverlay', 'EventTarget')
class WindowControlsOverlay(EventTarget):
    """No titlebar overlay is drawn, so it is hidden and its area is empty."""

    def __init__(self, services=None, **_):
        super().__init__(services=services)

    def get_visible(self): return False
    def fn_getTitlebarAreaRect(self, *_extra): return _empty_rect(self)
    def get_ongeometrychange(self): return self._get_handler("ongeometrychange")
    def set_ongeometrychange(self, value): self._set_handler("ongeometrychange", value)


def _in_hardware(content_type, services):
    """Whether the collected facts decode this codec in hardware; nothing does without them."""
    families = tuple(hardware_codecs(services))
    names = families_in(content_type)
    return bool(names) and all(name in families for name in names)


#: The ``type`` values each method accepts; ``record`` is refused, which is why ``encodingInfo``
#: answers for WebRTC configurations only.
_ALLOWED_TYPES = {"decodingInfo": ("file", "media-source", "webrtc"),
                  "encodingInfo": ("webrtc",)}
#: A video configuration must carry all of these, an audio one only a content type.
_VIDEO_MEMBERS = ("contentType", "width", "height", "bitrate", "framerate")
_AUDIO_MEMBERS = ("contentType",)
#: ``type/subtype`` with the parameters a MIME type may carry; Chromium parses this member, so
#: anything else is a ``TypeError``.
_MIME_TYPE = re.compile(r"^[A-Za-z0-9!#$%^&*_+.-]+/[A-Za-z0-9!#$%^&*_+.-]+(?:\s*;.*)?$")
_CONFIG_NAMES = {"decodingInfo": "MediaDecodingConfiguration",
                 "encodingInfo": "MediaEncodingConfiguration"}
_MEDIA_NAMES = {"video": "VideoConfiguration", "audio": "AudioConfiguration"}


def _refused(method, member):
    """The ``TypeError`` Chromium answers a media-capability call it will not read with."""
    return PageTypeError(f"Failed to execute '{method}' on 'MediaCapabilities': "
                       f"the configuration has no valid {member}.")


def _media_configuration(configuration, method):
    """``(type, [(kind, contentType, box_drawn)])`` as Chromium reads a configuration.

    Raises the ``TypeError`` Chromium answers with: a missing argument, a ``type`` the method
    does not take, a media dictionary without its required members, a ``contentType`` that is
    not a MIME type, a ``framerate`` that is not positive.  ``box_drawn`` is false for a video
    box of no width or height, which Chromium reports as a software path.
    """
    config = dictionary(configuration, name=_CONFIG_NAMES[method])
    type_name = config.get("type")
    type_name = "" if type_name is None else str(dom_string(type_name))
    if type_name not in _ALLOWED_TYPES[method]:
        raise _refused(method, "type")
    media = []
    for kind, required in (("video", _VIDEO_MEMBERS), ("audio", _AUDIO_MEMBERS)):
        fields = config.get(kind)
        if fields is None:
            continue
        fields = dictionary(fields, name=_MEDIA_NAMES[kind])
        for member in required:
            if member not in fields:
                raise _refused(method, f"{kind} {member}")
        content_type = str(dom_string(fields.get("contentType")))
        if not _MIME_TYPE.match(content_type):
            raise _refused(method, f"{kind} contentType")
        drawn = True
        if kind == "video":
            if number(fields.get("framerate")) <= 0:
                raise _refused(method, "video framerate")
            drawn = number(fields.get("width")) > 0 and number(fields.get("height")) > 0
        media.append((kind, content_type, drawn))
    if not media:
        raise _refused(method, "video or audio configuration")
    return type_name, media


def _efficient(content_type, drawn, hardware):
    """Whether Chromium calls this WebRTC codec's path power efficient.

    Audio always -- the software encoder counts -- and video only in hardware, and only when
    the box it is asked about has a size.
    """
    codec = webrtc_codec(content_type)
    if codec is None or not drawn:
        return False
    kind, family = codec
    return True if kind == "audio" else family in tuple(hardware)


@interface('MediaCapabilities', None)
class MediaCapabilities:
    """``decodingInfo.supported`` reads the same codec table ``MediaSource.isTypeSupported``
    reads, so two answers about one codec cannot contradict each other.  A configuration
    Chromium refuses -- a ``type`` the method does not take, a member it needs -- is refused
    here too, and as a rejected promise rather than a throw, which is what Chrome does.
    """

    def __init__(self, services=None, **_):
        self._services = services

    def _answers(self, type_name, media, method):
        """``{supported, smooth, powerEfficient}`` for a configuration Chromium accepted."""
        if type_name == "webrtc":
            direction = "encode" if method == "encodingInfo" else "decode"
            hardware = (hardware_encoders(self._services) if direction == "encode"
                        else hardware_codecs(self._services))
            supported = all(webrtc_supported(content_type, direction, self._services)
                            for _kind, content_type, _drawn in media)
            efficient = supported and all(
                _efficient(content_type, drawn, hardware)
                for _kind, content_type, drawn in media)
        else:
            supported = all(is_type_supported(content_type, media_facts(self._services))
                            for _kind, content_type, _drawn in media)
            efficient = supported and all(
                drawn and _in_hardware(content_type, self._services)
                for _kind, content_type, drawn in media)
        return {"supported": supported, "smooth": supported, "powerEfficient": efficient}

    def _capability(self, configuration, method):
        try:
            type_name, media = _media_configuration(configuration, method)
        except (PageTypeError, IdlArgumentError) as refused:
            return settled(self._services, error=refused)
        return settled(self._services, value=self._answers(type_name, media, method))

    def fn_decodingInfo(self, configuration=MISSING, *_extra):
        return self._capability(configuration, "decodingInfo")
    def fn_encodingInfo(self, configuration=MISSING, *_extra):
        return self._capability(configuration, "encodingInfo")


@interface('NavigatorLogin', None)
class NavigatorLogin:
    _STATUSES = ("logged-in", "logged-out")

    def __init__(self, services=None, **_):
        self._services = services

    def fn_setStatus(self, status=MISSING, *_extra):
        if status is MISSING:
            raise missing_error('NavigatorLogin', 'setStatus', 1, 0, constructor=False)
        if dom_string(status) not in self._STATUSES:
            # Chrome rejects rather than throwing, which is what the probe measured.
            return settled(self._services, error=PageTypeError(
                "Failed to execute 'setStatus' on 'NavigatorLogin': "
                "The provided value is not a valid LoginStatus."))
        return settled(self._services, value=None)


@interface('NavigatorManagedData', 'EventTarget')
class NavigatorManagedData(EventTarget):
    """No management is in force, so a configuration read is refused as in Chrome."""

    def __init__(self, services=None, **_):
        super().__init__(services=services)

    def get_onmanagedconfigurationchange(self):
        return self._get_handler("onmanagedconfigurationchange")
    def set_onmanagedconfigurationchange(self, value):
        self._set_handler("onmanagedconfigurationchange", value)
    def fn_getManagedConfiguration(self, keys=MISSING, *_extra):
        if keys is MISSING:
            raise missing_error('NavigatorManagedData', 'getManagedConfiguration', 1, 0,
                                constructor=False)
        return settled(self._services, error=dom_exception(
            "NotAllowedError", "Failed to execute 'getManagedConfiguration' on "
            "'NavigatorManagedData': Cannot obtain managed configuration without management."))


@interface('DelegatedInkTrailPresenter', None)
class DelegatedInkTrailPresenter:
    """A handle a page can hold; with no compositor it draws nothing."""

    def __init__(self, services=None, **_):
        self._services = services
        self._area = pyv8.Null

    def get_presentationArea(self): return self._area
    def fn_updateInkTrailStartPoint(self, event=MISSING, style=pyv8.Undefined, *_extra):
        if event is MISSING:
            raise missing_error('DelegatedInkTrailPresenter', 'updateInkTrailStartPoint', 1, 0,
                                constructor=False)
        return pyv8.Undefined


@interface('Ink', None)
class Ink:
    def __init__(self, services=None, **_):
        self._services = services

    def fn_requestPresenter(self, param=pyv8.Undefined, *_extra):
        """Resolves a presenter; with no compositor there is no area to draw one in."""
        return settled(self._services,
                       value=DelegatedInkTrailPresenter(services=self._services))

__all__ = [
    "CrashReportContext", "DelegatedInkTrailPresenter", "DevicePosture", "External", "Ink",
    "LaunchQueue", "MediaCapabilities", "NavigatorLogin", "NavigatorManagedData",
    "Presentation", "ProtectedAudience", "Scheduler", "SpeechSynthesis", "StyleMedia",
    "UserActivation", "Viewport", "VirtualKeyboard", "WindowControlsOverlay",
]
