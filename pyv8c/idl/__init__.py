"""The declaration surface: what a web class says about the interface it implements.

A class states its identity with ``@interface("Element", "Node")``, answers its members with
``fn_*``/``get_*``/``set_*`` methods, and states ``__idl_overrides__`` only where the IDL
document cannot decide.  Everything else -- which members exist, where they live, their arity,
their flags -- is the document's, and this package is where that contract is checked.
"""
from __future__ import annotations

from .spec import (InterfaceSpec, idl_spec, interface, members_of, standalone, stated_fields,
                   stated_key, unresolved_members)

__all__ = ["InterfaceSpec", "idl_spec", "interface", "members_of", "standalone", "stated_fields",
           "stated_key", "unresolved_members"]
