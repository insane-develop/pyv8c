"""Dump every compiled interface as canonical JSON, so a rewrite can be proved inert.

``python script/dump_compiled.py before.json`` writes what the engine decided per member
(by qualified name, so a rebinding shows) and per interface; two equal dumps mean the change
left the engine's answer alone.
"""
import json
import pathlib
import sys

from pyv8c.engine.bindings import Bindings
from pyv8c.engine.idl import Idl
from pyv8c.platform.bootstrap import bootstrap_runtime


#: The spec fields that carry a decision, as ``IdlSpec`` names them.
FIELDS = ("array", "call", "constants_value", "constructor_behavior", "definition_kind",
          "has_constructor", "length", "name", "parent_class", "to_string_tag")


def described(value):
    """A JSON-stable rendering: callbacks by qualified name, flags by number."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (tuple, list)):
        return [described(item) for item in value]
    if callable(value):
        return "callable:" + (getattr(value, "__qualname__", None) or str(value))
    if hasattr(value, "name"):                  # a ConstantSpec, whose repr carries an address
        return f"{type(value).__name__}:{value.name}"
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def member_of(entry, names):
    return {name: described(getattr(entry, name, None)) for name in names} | {
        "stub": bool(getattr(getattr(entry, "callback", None) or getattr(entry, "getter", None),
                             "__v8_idl_stub__", False))}

bindings = Bindings(Idl.load(), runtime=bootstrap_runtime())
report = {}
for interface in bindings.idl.names():
    spec = bindings.class_config_for(interface)
    handlers = {field: described(getattr(spec, field, None))
                for field in ("name_get", "name_set", "name_query", "name_del", "name_enum",
                              "index_get", "index_set", "index_query", "index_del", "index_enum",
                              "cross_origin_check")}
    report[interface] = {
        "methods": [member_of(entry, ("name", "length", "location", "property_flags",
                                      "receiver_check", "side_effect", "cross_origin_check"))
                    for entry in spec.methods],
        "attributes": [member_of(entry, ("name", "location", "property_flags", "receiver_check",
                                         "side_effect", "cross_origin_get_check",
                                         "cross_origin_set_check", "replaceable"))
                       | {"getter": described(entry.getter), "setter": described(entry.setter)}
                       for entry in spec.attributes],
        "handlers": {field: value for field, value in handlers.items() if value is not None},
        "fields": {field: described(getattr(spec, field, None)) for field in FIELDS},
    }

pathlib.Path(sys.argv[1]).write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
print("interfaces", len(report),
      "members", sum(len(item["methods"]) + len(item["attributes"]) for item in report.values()))
