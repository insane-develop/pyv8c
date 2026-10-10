"""How much of a class's ``__idl_overrides__`` says only what the IDL already says.


The baseline owns the shape and the ``fn_``/``get_``/``set_`` convention answers what a
class implements, so a declaration earns its line only by differing.  ``--list`` names the
rows, ``--fields`` histograms the fields a declaration states, ``--json`` writes the report
a guard test freezes.
"""


from __future__ import annotations


import argparse
import collections
import inspect
import json
from pathlib import Path


from ..engine.binder import cross_origin_check, member_flags
from ..idl import idl_spec, members_of


__all__ = ["BUCKETS", "audit", "field_histogram", "main", "summarise"]


#: Every way one declaration can relate to the baseline.  ``restating`` and ``flags`` name the
#: callback the convention would (so the callback can go either way), ``renamed`` names another.
#: ``unresolved`` is a member the engine cannot install at all -- the one kind that can never
#: fire -- and ``resolved`` one it finds through an ancestor or a descendant, which the
#: declaration then shadows or duplicates.  ``no_interface`` is a class the baseline lacks.
BUCKETS = ("restating", "flags", "renamed", "unresolved", "resolved", "no_interface")


def _file_of(cls):
    try:
        return inspect.getsourcefile(cls) or ""
    except (TypeError, OSError):
        return ""


def _baseline_fields(member, kind):
    """What the baseline states about one member, in the fields the engine reads from it.

    Only two of a declaration's own flags ever reach the binding: ``replaceable``, and ``length``
    where the baseline states none -- the member loop hands the baseline's location, property
    flags, side effect and cross-origin checks to both kinds, so a declaration stating *different*
    ones says nothing (measured: ``AbortController.signal`` declares ``side_effect=1`` and compiles
    to the baseline's ``0``).
    """
    if kind == "operation":
        return {} if member.length is not None else {"length": 0}
    return {"replaceable": False}


def _delta(kind, declared, member):
    """``{field: (declared, baseline)}`` where one declaration departs from the baseline.

    Empty means the line says nothing the baseline does not, so deleting it changes nothing.
    """
    delta = {field: (getattr(declared, field, None), value)
             for field, value in _baseline_fields(member, kind).items()
             if getattr(declared, field, None) != value}
    if kind == "attribute" and member.setter_state is None:
        # The baseline says nothing about writes, so the declaration is what makes the member
        # writable: deleting it would leave a read-only member behind.
        if getattr(declared, "setter", None) is not None:
            delta["setter"] = (getattr(declared, "setter", None), None)
    return delta


def _row(interface, cls, kind, declared, delta):
    return {"interface": interface, "member": declared.name, "kind": kind,
            "file": _file_of(cls).rsplit("pyv8c", 1)[-1],
            "delta": {field: [_json_value(declared_value), _json_value(baseline_value)]
                      for field, (declared_value, baseline_value) in delta.items()}}


def _json_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _classify(kind, declared, member):
    """(bucket, delta) for a declaration whose member the baseline does carry."""
    if kind == "operation":
        conventional = getattr(declared, "callback", None) == f"fn_{member.name}"
    else:
        conventional = (getattr(declared, "getter", None) == f"get_{member.name}"
                        and getattr(declared, "setter", None) in (None, f"set_{member.name}"))
    delta = _delta(kind, declared, member)
    if not conventional:
        return "renamed", delta
    return ("restating" if not delta else "flags"), delta


def _declarations(spec):
    """``((kind, member), ...)`` for one hand-written spec."""
    return tuple(("operation", entry) for entry in spec.methods) + tuple(
        ("attribute", entry) for entry in spec.attributes)


def audit(bindings, *, limit=None):
    """Classify every declaration in the bindings; ``limit`` keeps the first rows of each bucket."""
    table = bindings.idl
    rows = {bucket: [] for bucket in BUCKETS}
    specs = 0
    total = 0
    for name in table.names():
        cls = bindings.class_for(name)
        spec = idl_spec(cls, table, name)
        if not (spec.methods or spec.attributes):
            continue
        specs += 1
        total += len(spec.methods) + len(spec.attributes)
        # A member a base class also declares is not deletable on its own: with the subclass's
        # declaration gone, ``_members`` walks on to that one, and it may answer differently --
        # a parent naming a getter and no setter turns a writer into the logging shell.
        inherited = {entry.name for base in cls.__mro__[1:]
                     for pair in (members_of(base, table, name),)
                     for entry in tuple(pair[0]) + tuple(pair[1])}
        members = {(member.kind, member.name): member
                   for _location, member in table.own_members(name)}
        for kind, declared in _declarations(spec):
            member = members.get((kind, declared.name))
            if member is not None:
                bucket, delta = _classify(kind, declared, member)
                if bucket == "restating" and declared.name in inherited:
                    bucket, delta = "flags", {"inherited": [True, False]}
                rows[bucket].append(_row(name, cls, kind, declared, delta))
            elif table.member(name, declared.name, kind) is None:
                # The engine's own skip test in ``_compile``: nothing can resolve this member,
                # so the line can never reach a page.
                rows["unresolved"].append(_row(name, cls, kind, declared, {}))
            else:
                # An ancestor or a descendant carries it: the declaration is what puts it on
                # *this* interface, which a page sees as a shadowing override or a duplicate.
                rows["resolved"].append(_row(name, cls, kind, declared, {}))
    for name in sorted(set(bindings.runtime.catalog.interfaces) - set(table.names())):
        cls = bindings.runtime.interface(name)
        spec = idl_spec(cls, table, name)
        for kind, declared in _declarations(spec):
            rows["no_interface"].append(_row(name, cls, kind, declared, {}))
    counts = {bucket: len(found) for bucket, found in rows.items()}
    if limit is not None:
        rows = {bucket: found[:limit] for bucket, found in rows.items()}
    return {"counts": counts, "rows": rows, "specs": specs, "declared": total}


def field_histogram(report):
    """How often each field is the reason a declaration is not a pure restatement."""
    found = collections.Counter()
    for bucket, rows in report["rows"].items():
        if bucket in ("unresolved", "resolved", "no_interface"):
            continue
        for row in rows:
            found.update(row["delta"].keys())
    return found.most_common()


def summarise(report):
    """One line per bucket, for a CLI or a failure message."""
    return "\n".join(f"  {bucket:<12} {count}"
                     for bucket, count in sorted(report["counts"].items()))


def _shipped_bindings():
    """The bindings the audit inspects, imported here so importing this module stays light."""
    from ..engine.bindings import Bindings
    from ..engine.idl import Idl
    from ..platform.bootstrap import bootstrap_runtime

    return Bindings(Idl.load(), runtime=bootstrap_runtime())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", dest="listed", nargs="*", action="extend", default=[],
                        metavar="BUCKET",
                        help=f"print these buckets' rows (any of {', '.join(BUCKETS)})")
    parser.add_argument("--fields", action="store_true", help="histogram the fields that differ")
    parser.add_argument("--json", metavar="PATH", help="write the report for a guard test to freeze")
    parser.add_argument("--limit", type=int, default=None, help="keep the first N rows per bucket")
    args = parser.parse_args(argv)

    report = audit(_shipped_bindings(), limit=args.limit)
    print(summarise(report))
    print(f"  {'declared':<12} {report['declared']} across {report['specs']} specs")
    if args.fields:
        for field, count in field_histogram(report):
            print(f"  {field:<26} {count}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    for bucket in (args.listed or []):
        for row in report["rows"].get(bucket, []):
            print(f"  {bucket:<10} {row['interface']}.{row['member']} [{row['kind']}]"
                  f" {row['file']} {row['delta']}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
