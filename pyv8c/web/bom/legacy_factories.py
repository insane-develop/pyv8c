"""The legacy factory functions Window still carries: ``Image``, ``Option``, ``Audio``.

Chrome keeps each as a function object rather than an interface, and a call builds a
detached element.  One is built per realm -- a factory reaches its own window's document
-- and installed only where the reference surface says the browser has the name.
"""
from ...idl import standalone
from ..dom.html_elements import HTMLImageElement
from ..dom.html_input import HTMLOptionElement
from ..dom.html_media import HTMLAudioElement

FACTORY_TAG = {"Image": "img", "Option": "option", "Audio": "audio"}
FACTORY_TYPE = {
    "Image": HTMLImageElement,
    "Option": HTMLOptionElement,
    "Audio": HTMLAudioElement,
}


def _fill(name, element, arguments):
    if name == "Image":
        for attribute, value in zip(("width", "height"), arguments):
            setter = getattr(element, "set_" + attribute, None)
            if value is not None and setter is not None:
                setter(value)
    elif name == "Option":
        text, value, default_selected, selected = (list(arguments) + [None] * 4)[:4]
        if text is not None:
            element.textContent = text
        if value is not None:
            element.set_value(value)
        if default_selected:
            element.attributes["selected"] = ""
        if selected is not None:
            element.set_selected(selected)
    elif arguments and arguments[0] is not None:
        element.set_src(arguments[0])


def factory_class(name, document):
    """One realm's factory: a class whose instances are that realm's elements."""
    class LegacyFactory(FACTORY_TYPE[name]):
        #: A factory is not an interface the document carries: the name is a function a page
        #: calls, and what it builds is the element class it is named for.
        __idl_overrides__ = {
            "interface": {
                "constructor_behavior": 1,
                "has_constructor": 1,
                "length": 0,
                "to_string_tag": FACTORY_TYPE[name].__name__,
            }
        }

        def __new__(cls, *arguments, **_):
            element = document._new_element(FACTORY_TAG[name])
            _fill(name, element, arguments)
            return element

    LegacyFactory.__name__ = name
    return standalone(LegacyFactory)


def install_legacy_factories(context, *, session, table, document):
    """Declare every legacy factory the reference browser has, built on first read."""
    names = [name for name in FACTORY_TAG if table.installs("Window", name)]
    for name in names:
        session.add_extra_class(name, factory_class(name, document))
    if names:
        context.install(names)

__all__ = ["FACTORY_TAG", "factory_class", "install_legacy_factories"]
