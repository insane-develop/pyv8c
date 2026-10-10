import pyv8
from pyv8 import AttributeSpec, V8Attribute

"""The ARIA reflection: an attribute on one side, a property on the other.

Every pairing here was measured on Chrome 154 rather than derived, because the ARIA
content attribute names are not the dashed form of the IDL names: ariaAutoComplete is
aria-autocomplete, ariaColCount is aria-colcount, and the nine idref members drop the
Element suffix. The values themselves are not checked -- Chrome writes whatever it is
given -- and a nullish value takes the attribute away.
"""
#: The ARIA attributes that reflect a content attribute, paired with the name
#: Chrome writes. Every one of them was measured rather than derived: the ARIA
#: names are not the dashed form of the IDL names (ariaAutoComplete is
#: aria-autocomplete, ariaColCount is aria-colcount), so the table is data.
ATTRIBUTES = (
    ("ariaAtomic", "aria-atomic"),
    ("ariaAutoComplete", "aria-autocomplete"),
    ("ariaBrailleLabel", "aria-braillelabel"),
    ("ariaBrailleRoleDescription", "aria-brailleroledescription"),
    ("ariaBusy", "aria-busy"),
    ("ariaChecked", "aria-checked"),
    ("ariaColCount", "aria-colcount"),
    ("ariaColIndex", "aria-colindex"),
    ("ariaColIndexText", "aria-colindextext"),
    ("ariaColSpan", "aria-colspan"),
    ("ariaCurrent", "aria-current"),
    ("ariaDescription", "aria-description"),
    ("ariaDisabled", "aria-disabled"),
    ("ariaExpanded", "aria-expanded"),
    ("ariaHasPopup", "aria-haspopup"),
    ("ariaHidden", "aria-hidden"),
    ("ariaInvalid", "aria-invalid"),
    ("ariaKeyShortcuts", "aria-keyshortcuts"),
    ("ariaLabel", "aria-label"),
    ("ariaLevel", "aria-level"),
    ("ariaLive", "aria-live"),
    ("ariaModal", "aria-modal"),
    ("ariaMultiLine", "aria-multiline"),
    ("ariaMultiSelectable", "aria-multiselectable"),
    ("ariaOrientation", "aria-orientation"),
    ("ariaPlaceholder", "aria-placeholder"),
    ("ariaPosInSet", "aria-posinset"),
    ("ariaPressed", "aria-pressed"),
    ("ariaReadOnly", "aria-readonly"),
    ("ariaRelevant", "aria-relevant"),
    ("ariaRequired", "aria-required"),
    ("ariaRoleDescription", "aria-roledescription"),
    ("ariaRowCount", "aria-rowcount"),
    ("ariaRowIndex", "aria-rowindex"),
    ("ariaRowIndexText", "aria-rowindextext"),
    ("ariaRowSpan", "aria-rowspan"),
    ("ariaSelected", "aria-selected"),
    ("ariaSetSize", "aria-setsize"),
    ("ariaSort", "aria-sort"),
    ("ariaValueMax", "aria-valuemax"),
    ("ariaValueMin", "aria-valuemin"),
    ("ariaValueNow", "aria-valuenow"),
    ("ariaValueText", "aria-valuetext"),
)


#: The nine members that answer with elements rather than a string. Eight
#: of them are readonly; ariaOwnsElements takes a setter and ignores
#: every value, which is what Chrome does with it.
IDREF = (
    ("ariaActiveDescendantElement", "aria-activedescendant", False),
    ("ariaActionsElements", "aria-actions", False),
    ("ariaControlsElements", "aria-controls", False),
    ("ariaDescribedByElements", "aria-describedby", False),
    ("ariaDetailsElements", "aria-details", False),
    ("ariaErrorMessageElements", "aria-errormessage", False),
    ("ariaFlowToElements", "aria-flowto", False),
    ("ariaLabelledByElements", "aria-labelledby", False),
    ("ariaOwnsElements", "aria-owns", True),
)


#: Implemented but not declared: the convention answers them where a profile carries them.
UNDECLARED = frozenset({"ariaOwnsElements"})


def install(cls):
    """Give *cls* the ARIA members, and return the specs to declare them with."""
    from ...foundation.arguments import dom_string, is_nullish

    specs = []
    for name, attribute in ATTRIBUTES:
        def getter(self, _attribute=attribute):
            value = self.attributes.get(_attribute)
            return pyv8.Null if value is None else str(value)

        def setter(self, value, _attribute=attribute):
            if is_nullish(value):
                self.attributes.pop(_attribute, None)
            else:
                self.attributes[_attribute] = str(dom_string(value))

        _name_getter, _name_setter = "get_" + name, "set_" + name
        getattr(cls, _name_getter, None)
        setattr(cls, _name_getter, getter)
        setattr(cls, _name_setter, setter)
        specs.append(AttributeSpec(name=name, getter=_name_getter,
                                   setter=_name_setter, side_effect=1))

    for name, attribute, writable in IDREF:
        is_list = name.endswith("Elements")
        if name in UNDECLARED:
            continue

        def getter(self, _attribute=attribute, _is_list=is_list):
            # Absent is null rather than an empty list, and a tree outside a document
            # resolves nothing: both are what Chrome answers.
            if _attribute not in self.attributes:
                return pyv8.Null
            raw = self.attributes.get(_attribute)
            found = self._resolve_ids(str(raw).split())
            if _is_list:
                return found
            return found[0] if found else pyv8.Null

        _name_getter = "get_" + name
        setattr(cls, _name_getter, getter)
        spec = {"name": name, "getter": _name_getter, "side_effect": 1}
        if not writable:
            # A readonly attribute answers an assignment with a TypeError rather than
            # taking it, which is what the flag buys.
            spec["property_flags"] = V8Attribute.ReadOnly
        if writable:
            # Chrome ignores every value here, a list of elements included, so the
            # setter is here to swallow the assignment rather than to write.
            def setter(self, value, _attribute=attribute):
                return None

            setattr(cls, "set_" + name, setter)
            spec["setter"] = "set_" + name
        specs.append(AttributeSpec(**spec))
    return tuple(specs)
