"""Which collections are iterable on each side, symbol by symbol.

    python312 script/iterable_probe.py

``surface_diff.py`` compares ``Object.getOwnPropertyNames``, which does not list symbol-keyed
members, so it can say that ``HTMLCollection.prototype`` publishes four names too many but not
whether the collection is *iterable* at all.  That distinction decides the fix: a collection
Chrome makes iterable needs ``Symbol.iterator`` and nothing else, while a legacy array-like it
does not make iterable must lose the whole set.

Both sides get the same page and the same question, so the answer is about the engines.

It is also the acceptance test for the ``array`` vocabulary: every row must read the same on both
sides once ``template.array`` states the word and the profile records the measured one, so a
difference here is a difference in what the two engines publish, not in how it is written down.
"""
import sys
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skills" / "pyv8c-challenge-parity" / "scripts"))

from harness import FIXTURE_URL, _ask_page  # noqa: E402
from probes import RECT_PAGE  # noqa: E402

#: The holders ``surface_diff.py --probe surface_wide`` flags: the first group publishes four
#: names Chrome does not have, the second lacks four Chrome does.
OVER = ("HTMLCollection", "HTMLFormControlsCollection", "HTMLOptionsCollection",
        "MimeTypeArray", "NamedNodeMap", "Plugin", "PluginArray")
UNDER = ("CSSNumericArray", "CSSTransformValue", "CSSUnparsedValue",
         "TimelineTriggerRangeList", "XRInputSourceArray")
#: Iterable collections that really do publish all four names: the refactor that took the
#: iterator flags off ``NodeList``'s siblings must not have touched these.
BOTH = ("NodeList", "RadioNodeList", "DOMTokenList", "Headers", "FormData", "KeyboardLayoutMap")
#: Interfaces whose answer decides which word their spec states -- added when ``array=True``
#: stopped being accepted and each site had to be chosen from a measurement rather than a guess.
CHECK = ("CSSStyleDeclaration", "StylePropertyMapReadOnly", "HTMLAllCollection")

PROBE = """(() => {
  const names = %s;
  const out = {};
  for (const name of names) {
    const prototype = (window[name] || {}).prototype;
    if (!prototype) { out[name] = 'no prototype'; continue; }
    const kinds = [];
    for (const member of ['entries', 'keys', 'values', 'forEach']) {
      if (typeof prototype[member] === 'function') kinds.push(member);
    }
    //: Only the shape is read: constructing the interface is not what is in question, and
    //: most of them are not constructible at all.
    out[name] = { iterator: typeof prototype[Symbol.iterator], named: kinds.join(',') };
  }
  return out;
})()""" % (list(OVER + UNDER + BOTH + CHECK),)


def main():
    mine, theirs = _ask_page(FIXTURE_URL.format("iterable"), RECT_PAGE, PROBE, 0.6)
    for label, answer in (("pyv8c", mine), ("chrome", theirs)):
        if not isinstance(answer, dict):
            print(f"! {label} answered {answer!r}")
            return 1
    print(f"{'holder':32} {'pyv8c':24} {'chrome':24}")
    for name in OVER + UNDER + BOTH + CHECK:
        left, right = mine.get(name), theirs.get(name)
        def show(entry):
            if not isinstance(entry, dict):
                return str(entry)
            return f"{entry['iterator']} [{entry['named']}]"
        flag = "" if show(left) == show(right) else "   <-- differs"
        print(f"{name:32} {show(left):24} {show(right):24}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
