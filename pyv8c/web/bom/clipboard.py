"""``Clipboard``: the asynchronous clipboard.

Chrome waits for a permission prompt here; without one a page's call ends in
``NotAllowedError``, which is what this reports rather than hanging.
"""


from ...idl import interface
from ...foundation.arguments import MISSING, missing_error
from ..page_errors import dom_exception
from ..dom.events import EventTarget
from .utils import settled

__all__ = ["Clipboard"]


@interface('Clipboard', 'EventTarget')
class Clipboard(EventTarget):
    def __init__(self, services=None, **_):
        super().__init__(services=services)

    def get_onclipboardchange(self): return self._get_handler("onclipboardchange")
    def set_onclipboardchange(self, value): self._set_handler("onclipboardchange", value)
    def _denied(self, method):
        return settled(self._services, error=dom_exception(
            "NotAllowedError",
            "Failed to execute '%s' on 'Clipboard': Write permission denied." % method))
    def fn_read(self, *_extra): return self._denied("read")
    def fn_readText(self, *_extra): return self._denied("readText")
    def fn_write(self, data=MISSING, *_extra):
        if data is MISSING:
            raise missing_error('Clipboard', 'write', 1, 0, constructor=False)
        return self._denied("write")
    def fn_writeText(self, data=MISSING, *_extra):
        if data is MISSING:
            raise missing_error('Clipboard', 'writeText', 1, 0, constructor=False)
        return self._denied("writeText")
