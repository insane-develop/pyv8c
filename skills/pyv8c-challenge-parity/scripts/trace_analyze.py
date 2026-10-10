"""Throwaway: what the engine trace says the page touched, and where it got nothing.

A trace line is ``[REALM] kind: NAME(args) -> VALUE``.  A read that answered ``undefined`` is
the sharpest divergence signal there is: a real browser has a value there, and the page is
asking for it on purpose.
"""
import collections
import pathlib
import re
import sys

LINE = re.compile(r"\[([A-Z0-9_]+)\]\s+(getter|method|setter):\s+(.*)$")

#: Methods whose ``undefined`` is correct and therefore dull.
VOID = {
    "addEventListener", "removeEventListener", "dispatchEvent", "observe", "disconnect",
    "appendChild", "removeChild", "append", "prepend", "insertBefore", "replaceChild",
    "setAttribute", "removeAttribute", "add", "remove", "toggle", "replace", "forEach",
    "postMessage", "close", "focus", "blur", "preventDefault", "stopPropagation",
    "stopImmediatePropagation", "clearTimeout", "setTimeout", "setInterval", "clearInterval",
    "then", "catch", "finally", "resolve", "reject", "write", "writeln",
}


def main(path):
    realms = collections.Counter()
    kinds = collections.Counter()
    reads_undefined = collections.Counter()
    calls_undefined = collections.Counter()
    errors = collections.Counter()
    samples = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        match = LINE.search(line)
        if match is None:
            for needle in ("ReferenceError", "TypeError", "SyntaxError", "is not a function",
                           "dispatch error"):
                if needle in line and "engine.trace" not in line:
                    errors[f"{needle}: {line.strip()[-90:]}"] += 1
            continue
        realm, kind, rest = match.groups()
        realms[realm] += 1
        kinds[kind] += 1
        #: Only a line with ``->`` answered anything: a setter line never has one, and reading
        #: its empty value as "undefined" is what made the first pass report 413 fake reads.
        if " -> " not in rest:
            continue
        name, _, value = rest.partition(" -> ")
        value = value.strip()
        if kind == "method":
            name = name.split("(")[0].strip()
        else:
            name = name.split(" = ")[0].strip()
        if value.startswith("undefined"):
            bucket = reads_undefined if kind != "method" else calls_undefined
            bucket[name] += 1
            samples.setdefault(name, line.strip()[-140:])

    print(f"--- {sum(realms.values())} traced accesses")
    print(f"  realms {dict(realms)}")
    print(f"  kinds  {dict(kinds)}")
    print(f"--- reads that answered undefined: {sum(reads_undefined.values())}"
          f" over {len(reads_undefined)} names")
    for name, count in reads_undefined.most_common(30):
        print(f"  {count:5}  {name}")
        print(f"         {samples[name]}")
    #: The trace names a call after its interface too (``DOMTokenList.add``), so the void list
    #: has to be matched against the last segment or every setter looks like a divergence.
    def void(name):
        return name.rsplit(".", 1)[-1] in VOID

    print(f"--- calls that answered undefined (void ones removed): "
          f"{sum(count for name, count in calls_undefined.items() if not void(name))}")
    for name, count in calls_undefined.most_common(40):
        if void(name):
            continue
        print(f"  {count:5}  {name}")
        print(f"         {samples[name]}")
    if errors:
        print(f"--- error-looking lines: {sum(errors.values())}")
        for text, count in errors.most_common(10):
            print(f"  {count:5}  {text}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "5s.log")
