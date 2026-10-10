"""Trusted Types.

A policy calls the page's own callback and wraps the string it returns; the
type-inspection queries answer nothing because the spec's type tables are not
modelled.
"""

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, dom_string, is_nullish, missing_error
from ..page_errors import PageTypeError


class _TrustedValue:
    """What a policy hands back: the string, wrapped and readable."""

    def __init__(self, value="", **_):
        self._value = str(value)

    def fn_toString(self, *_extra): return self._value
    def fn_toJSON(self, *_extra): return self._value
    def __str__(self): return self._value


#: No members declared: ``fn_toString``/``fn_toJSON`` are what the convention binds.


@interface('TrustedHTML', None)
class TrustedHTML(_TrustedValue):
    __idl_overrides__ = {
        "interface": {
            "length": 0
        }
    }


@interface('TrustedScript', None)
class TrustedScript(_TrustedValue):
    __idl_overrides__ = {
        "interface": {
            #: Code-like: ``eval`` and the Function constructor stringify this value instead of
            #: handing it straight back, which is what lets a page run its own policy's source.
            "code_like": True,
            "length": 0,
        }
    }


@interface('TrustedScriptURL', None)
class TrustedScriptURL(_TrustedValue):
    __idl_overrides__ = {
        "interface": {
            "length": 0
        }
    }


#: Which wrapper each policy operation produces.
_WRAPPERS = {"createHTML": TrustedHTML, "createScript": TrustedScript,
             "createScriptURL": TrustedScriptURL}


@interface('TrustedTypePolicy', None)
class TrustedTypePolicy:
    def __init__(self, name="", rules=None, **_):
        self._name = str(name)
        self._rules = dict(rules or {})

    def get_name(self): return self._name
    def fn_createHTML(self, value=MISSING, *_extra): return self._create("createHTML", value)
    def fn_createScript(self, value=MISSING, *_extra): return self._create("createScript", value)
    def fn_createScriptURL(self, value=MISSING, *_extra):
        return self._create("createScriptURL", value)

    def _create(self, operation, value):
        if value is MISSING:
            raise missing_error('TrustedTypePolicy', operation, 1, 0, constructor=False)
        callback = self._rules.get(operation)
        if callback is None:
            raise PageTypeError(
                f"Failed to execute '{operation}' on 'TrustedTypePolicy':"
                f" Policy \"{self._name}\" does not implement {operation}.")
        return _WRAPPERS[operation](callback(dom_string(value)))


@interface('TrustedTypePolicyFactory', None)
class TrustedTypePolicyFactory:
    def __init__(self, **_):
        self._policies = {}

    #: [SameObject], and empty: the two read-only values a page may reuse.
    def get_emptyHTML(self): return self._reuse("html", TrustedHTML)
    def get_emptyScript(self): return self._reuse("script", TrustedScript)

    def _reuse(self, slot, wrapper):
        value = getattr(self, "_empty", None)
        if value is None:
            value = self._empty = {}
        if slot not in value:
            value[slot] = wrapper("")
        return value[slot]

    def get_defaultPolicy(self): return pyv8.Null

    def fn_createPolicy(self, name=MISSING, rules=None, *_extra):
        if name is MISSING:
            raise missing_error('TrustedTypePolicyFactory', 'createPolicy', 2, 0, constructor=False)
        name = dom_string(name)
        # Read by name: a dictionary conversion would drop the callbacks.
        settings = {}
        if rules is not None and not is_nullish(rules):
            for operation in ("createHTML", "createScript", "createScriptURL"):
                callback = getattr(rules, operation, None)
                if callable(callback):
                    settings[operation] = callback
        policy = self._policies.get(name)
        if policy is None:
            policy = self._policies[name] = TrustedTypePolicy(name, settings)
        return policy

    def fn_isHTML(self, value=MISSING, *_extra): return isinstance(value, TrustedHTML)
    def fn_isScript(self, value=MISSING, *_extra): return isinstance(value, TrustedScript)
    def fn_isScriptURL(self, value=MISSING, *_extra): return isinstance(value, TrustedScriptURL)

    #: The spec's attribute and property tables are not modelled, so these answer nothing.
    def fn_getAttributeType(self, *_extra): return ""
    def fn_getPropertyType(self, *_extra): return pyv8.Null
    def fn_getTypeMapping(self, *_extra): return pyv8.Undefined

__all__ = [
    "TrustedHTML", "TrustedScript", "TrustedScriptURL", "TrustedTypePolicy",
    "TrustedTypePolicyFactory",
]
