"""Join a two-sided surface reading with the interface baseline, name by name.

    python312 script/baseline_join.py                 # the members whose kind differs
    python312 script/baseline_join.py --holder window # one holder
    python312 script/baseline_join.py --chrome object # every name Chrome reports as an object

``surface_diff.py`` answers "which member differs"; this answers the next question, "what does
the baseline already know about it".  That is what decides where a fix belongs: if the baseline
carries the fact (a flag, a length), the engine is ignoring something it was handed; if it does
not, the fix is data and needs a regeneration, not another condition in the engine.

Reads the JSON ``surface_diff.py`` writes, so it costs nothing and touches nothing.
"""
import json
import pathlib
import sys

from pyv8c.engine.idl import Idl

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent


def load_pair(probe="surface", holder_filter=None, chrome_kind=None, show_all=False,
              interfaces_only=False):
    baseline = Idl.load(profile=None).interfaces
    mine = json.loads((OUT / f"surface-{probe}-pyv8c.json").read_text(encoding="utf-8"))
    theirs = json.loads((OUT / f"surface-{probe}-chrome.json").read_text(encoding="utf-8"))
    rows = []
    for holder in sorted(set(mine) & set(theirs)):
        if holder_filter and holder_filter not in holder:
            continue
        for name in sorted(set(mine[holder]) | set(theirs[holder])):
            left, right = mine[holder].get(name), theirs[holder].get(name)
            if chrome_kind and not (right or "").startswith(chrome_kind):
                continue
            if interfaces_only and name not in baseline:
                continue
            if not show_all and left == right:
                continue
            entry = baseline.get(name) or {}
            constructor = entry.get("constructor") or {}
            template = entry.get("template") or {}
            rows.append((holder, name, left, right, constructor.get("allow_call"),
                         constructor.get("allow_new"), constructor.get("length"),
                         template.get("definition_kind"), entry.get("parent")))
    return rows


def _value_of(argv, flag):
    """The word after *flag*: reading ``words[0]`` twice made both filters take the same one."""
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def main(argv):
    rows = load_pair(_value_of(argv, "--probe") or "surface",
                     _value_of(argv, "--holder"), _value_of(argv, "--chrome"),
                     "--all" in argv, "--interfaces" in argv)
    #: ASCII only: this console is GBK and a report that dies on its own header reports nothing.
    print(f"{'holder.member':42} {'pyv8c':14} {'chrome':16} call new len kind parent")
    for (holder, name, left, right, call, new, length, kind, parent) in rows:
        where = f"{holder}.{name}"
        print(f"{where:42} {str(left):14} {str(right):16} {str(call):5} {str(new):5} "
              f"{str(length):4} {str(kind):16} {parent}")
    print(f"\n{len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
