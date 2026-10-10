"""``window.visualViewport``: the layout viewport's twin, unzoomed here."""


from ...idl import interface
from ..dom.events import EventTarget, _install_handler_slots


#: Read away one at a time below; each is initialised to nothing here.
VALUES = ("offsetLeft", "offsetTop", "pageLeft", "pageTop", "width", "height", "scale")
HANDLERS = ("onresize", "onscroll", "onscrollend")


@interface('VisualViewport', 'EventTarget')
class VisualViewport(EventTarget):
    __idl_overrides__ = {
        "members": {
            "height": {
                "side_effect": 1
            },
            "offsetLeft": {
                "side_effect": 1
            },
            "offsetTop": {
                "side_effect": 1
            },
            "pageLeft": {
                "side_effect": 1
            },
            "pageTop": {
                "side_effect": 1
            },
            "scale": {
                "side_effect": 1
            },
            "width": {
                "side_effect": 1
            }
        },
        "interface": {
            "length": 0
        }
    }

    def __init__(self, window=None, **_):
        super().__init__(**_)
        self._window = window

    def get_width(self): return self._window.innerWidth
    def get_height(self): return self._window.innerHeight
    #: Nothing here zooms or scrolls the view, so the page sits at the origin.
    def get_scale(self): return 1
    def get_offsetLeft(self): return 0
    def get_offsetTop(self): return 0
    def get_pageLeft(self): return 0
    def get_pageTop(self): return 0


#: ``onresize`` and friends are this interface's own members, so the convention binds them.
_install_handler_slots(VisualViewport, HANDLERS)


__all__ = ["VisualViewport"]
