import pyv8
from pyv8 import AttributeSpec


"""The HTMLElement attributes that are not plain reflections.

Three shapes live here, all of them measured on Chrome 154 rather than described:

* a keyword the getter validates and the setter writes through untouched, so a value
  outside the set stays in the attribute and reads back as the fallback;
* a boolean that spells itself -- translate writes yes or no, autocorrect writes on or
  off -- and whose getter answers from the set of words it accepts;
* a plain reflection, which is here only because the attribute name is not the dashed
  form of the property: accessKey is accesskey, focusGroup is focusgroup.
"""


#: (property, attribute, default, words the getter accepts, true word, false word)
BOOLEAN_WORDS = (
    ("autocorrect", "autocorrect", True, ("true", "false", "on", "off"), "on", "off"),
    ("translate", "translate", True, ("true", "false", "yes", "no"), "yes", "no"),
    ("spellcheck", "spellcheck", True, ("true", "false", ""), "true", "false"),
    ("draggable", "draggable", False, ("true", "false"), "true", "false"),
)


#: The two that take a nullish value as a removal rather than stringifying it.
REMOVES_ON_NULL = ("popover",)


#: (property, attribute, default when absent, default when the value is unknown, words)
KEYWORDS = (
    ("autocapitalize", "autocapitalize", "", "sentences",
     ("sentences", "words", "characters", "none")),
    ("dir", "dir", "", "", ("ltr", "rtl", "auto")),
    ("enterKeyHint", "enterkeyhint", "", "",
     ("enter", "done", "go", "next", "previous", "search", "send")),
    ("inputMode", "inputmode", "", "",
     ("decimal", "email", "none", "numeric", "search", "tel", "text", "url")),
    ("virtualKeyboardPolicy", "virtualkeyboardpolicy", "", "", ("auto", "manual")),
    ("popover", "popover", None, "manual", ("auto", "hint", "manual")),
    # writingSuggestions reads as a word rather than as a boolean, which is the
    # one place Chrome spells a boolean member as a string.
    ("writingSuggestions", "writingsuggestions", "true", "true", ("true", "false")),
)


#: (property, attribute)
PLAIN = (
    ("lang", "lang"),
    ("accessKey", "accesskey"),
)


#: The boolean attributes, whose names are the ones Chrome measures the same way.
FLAGS = ("autofocus", "inert", "focusGroupStart")


def install(cls):
    """Give *cls* these members, and return the specs to declare them with."""
    from ...foundation.arguments import boolean, dom_string, is_nullish
    from ..page_errors import PageTypeError

    specs = []

    def declare(name, getter, setter=None):
        _getter, _setter = "get_" + name, "set_" + name
        setattr(cls, _getter, getter)
        if setter is not None:
            setattr(cls, _setter, setter)
        specs.append(AttributeSpec(name=name, getter=_getter,
                                   setter=_setter if setter is not None else None,
                                   side_effect=1))

    for name, attribute in PLAIN:
        # A nullish value is written as the string it stringifies to, which is what
        # Chrome does: the attribute stays and reads as "null".
        declare(name,
                lambda self, _a=attribute: str(self.attributes.get(_a, "")),
                lambda self, value, _a=attribute: self.attributes.__setitem__(
                    _a, str(dom_string(value))))

    for name, attribute, absent, unknown, words in KEYWORDS:
        def getter(self, _a=attribute, _absent=absent, _unknown=unknown, _words=words):
            value = self.attributes.get(_a)
            if value is None:
                # A value the page gave and then had taken away is still what the
                # member answers, which is how Chrome reads popover back.
                remembered = self._reflected.get(_a)
                if remembered is not None:
                    return remembered
                return pyv8.Null if _absent is None else _absent
            return str(value).lower() if str(value).lower() in _words else _unknown

        def setter(self, value, _a=attribute, _removes=name in REMOVES_ON_NULL):
            # A nullish value is stringified like any other, so the attribute stays
            # and the getter falls back to the words it knows -- except for popover,
            # which takes it as a removal and remembers what it was given.
            if _removes and is_nullish(value):
                self.attributes.pop(_a, None)
                self._reflected[_a] = str(dom_string(value))
                return
            self.attributes[_a] = str(dom_string(value))

        declare(name, getter, setter)

    for name, attribute, default, accepted, true_word, false_word in BOOLEAN_WORDS:
        def getter(self, _a=attribute, _default=default, _ok=accepted,
                   _true=true_word, _false=false_word):
            # A boolean member answers a boolean, whichever of its own words the
            # attribute happens to hold; only the empty spelling of spellcheck
            # answers a string, and an unknown word answers the default.
            value = self.attributes.get(_a)
            if value is None:
                remembered = self._reflected.get(_a)
                return remembered if remembered is not None else _default
            text = str(value).lower()
            if text in ("true", _true):
                return True
            if text in ("false", _false):
                return False
            return text if text in _ok else _default

        def setter(self, value, _a=attribute, _true=true_word, _false=false_word,
                   _removes=name in REMOVES_ON_NULL):
            # A nullish value is the false end, which for writingSuggestions means
            # taking the attribute away and remembering the word it stood for.
            if is_nullish(value):
                if _removes:
                    self.attributes.pop(_a, None)
                    self._reflected[_a] = _true
                else:
                    self.attributes[_a] = _false
                return
            truthy = value is not False and value != 0
            self.attributes[_a] = _true if truthy else _false

        declare(name, getter, setter)

    for name in FLAGS:
        attribute = name.lower()
        declare(name,
                lambda self, _a=attribute: _a in self.attributes,
                lambda self, value, _a=attribute: self._write(_a, value, boolean(value)))

    def get_focusGroup(self):
        """The attribute's words as a sequence, which is what this member is: an
        element may name more than one focus group."""
        return str(self.attributes.get("focusgroup", "")).split()

    def set_focusGroup(self, value):
        # The setter is not a sequence conversion: it stores what the value stringifies
        # to, so a nullish value lands in the attribute as the word null.
        self.attributes["focusgroup"] = str(dom_string(value))

    declare("focusGroup", get_focusGroup, set_focusGroup)

    def get_editContext(self):
        return pyv8.Null

    def set_editContext(self, value):
        if is_nullish(value):
            return None
        raise PageTypeError("Failed to set the 'editContext' property on 'HTMLElement':"
                          " Failed to convert value to 'EditContext'.")

    declare("editContext", get_editContext, set_editContext)

    def get_outerText(self):
        return self.get_innerText()

    def set_outerText(self, value):
        self.set_innerText(value)
        parent = self.parentNode
        if parent is not None:
            self._replace_with_text(value)

    declare("outerText", get_outerText, set_outerText)
    return tuple(specs)
