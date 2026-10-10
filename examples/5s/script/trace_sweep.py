"""Sweep a trace for the symptoms of an incomplete engine, not just for fingerprints.

    python script/trace_sweep.py 5s.log                 # the four buckets
    python script/trace_sweep.py 5s.log --before eb 12  # what happened before each /eb/ call

Four buckets, in the order that decides a challenge:

* ``, error:`` -- an engine API that raised where Chrome does not.  The sharpest signal, and
  usually a handful of lines;
* ``dispatch error`` -- a listener the engine ran *did* raise: the page's own code, reached
  through our dispatch;
* ``not implemented`` -- the "this interface is a stub" marker;
* page-side uncaught errors.

``--before`` is for the "the site caught something and reported it" question: it prints the
lines leading up to every transport call whose URL contains the fragment, so the exception that
made the page pick up the phone is visible directly before the call that carried it.

Nothing is judged here: this only says where to look.  Every line is cheap-filtered first --
the traces of this case reach hundreds of megabytes.
"""
import collections
import pathlib
import re
import sys

BUCKETS = collections.OrderedDict([
    ("engine raised (', error:')", (", error: ", re.compile(r".*, error: "))),
    ("dispatch error", ("dispatch error on", re.compile(r"dispatch error on"))),
    ("not implemented", ("not implemented", re.compile(r"not implemented|notimplemented", re.I))),
    ("page-side uncaught", ("uncaught", re.compile(r"uncaught|TypeError|ReferenceError", re.I))),
])


def sweep(path, samples=6):
    counts = {name: 0 for name in BUCKETS}
    shown = {name: [] for name in BUCKETS}
    with pathlib.Path(path).open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            for name, (needle, pattern) in BUCKETS.items():
                if needle in line and pattern.search(line):
                    counts[name] += 1
                    if len(shown[name]) < samples:
                        shown[name].append(line.rstrip()[:170])
    return counts, shown


def before(path, fragment, lines):
    """The trace lines leading up to each transport call whose URL contains *fragment*."""
    window = collections.deque(maxlen=lines)
    with pathlib.Path(path).open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.rstrip()
            if "network download:" in text and fragment in text:
                print(f"--- before {text[text.index('network download:'):][:120]}")
                for entry in window:
                    print(f"      {entry[:170]}")
            window.append(text)


def errors(path, following=4):
    """Every raised call in full, with the lines that come after it.

    What follows matters as much as the raise: a page listener that the failure reaches
    throws on the next line, and that is the exception the site goes on to report.
    """
    with pathlib.Path(path).open(encoding="utf-8", errors="replace") as handle:
        window = collections.deque(maxlen=following)
        pending = 0
        for line in handle:
            text = line.rstrip()
            if ", error: " in text:
                if pending:
                    _drain(window)
                #: Drop the log prefix: the realm kind and the call itself are the finding.
                body = text[text.index("] "):] if "] " in text else text
                print(f"--- {body[:400]}")
                pending = following
            elif pending:
                window.append(text)
                pending -= 1
                if not pending:
                    _drain(window)


def _drain(window):
    print("    then:")
    for entry in list(window):
        print(f"      {entry[:170]}")
    window.clear()


def around(path, fragment, span):
    """The lines around every trace line containing *fragment*.

    This is how a raise gets a story: what the realm was doing immediately before it, and
    which of the page's own calls came next, are what say whether the call was late, doomed,
    or simply the wrong one.
    """
    lines = pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    for index, text in enumerate(lines):
        if fragment in text:
            print(f"--- line {index + 1}")
            for offset in range(max(0, index - span), min(len(lines), index + span + 1)):
                marker = ">" if offset == index else " "
                print(f"   {marker} {lines[offset].strip()[:165]}")


def after(path, fragment, span):
    """The lines following the *last* line containing *fragment*.

    Where a flow stops is a question about what came next: the response that should have started
    the next step, and the widget's own reaction to it, are the lines right after it.
    """
    lines = pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    last = None
    for index, text in enumerate(lines):
        if fragment in text:
            last = index
    if last is None:
        print(f"! {fragment!r} never appears in {path}")
        return
    print(f"--- after line {last + 1} ({fragment!r})")
    for offset in range(last + 1, min(len(lines), last + 1 + span)):
        print(f"      {lines[offset].strip()[:180]}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__.splitlines()[2].strip())
        return 2
    path, rest = argv[0], argv[1:]
    if rest and rest[0] == "--after":
        fragment = rest[1] if len(rest) > 1 else ""
        count = int(rest[2]) if len(rest) > 2 else 30
        after(path, fragment, count)
        return 0
    if rest and rest[0] == "--before":
        fragment = rest[1] if len(rest) > 1 else "eb/"
        count = int(rest[2]) if len(rest) > 2 else 10
        before(path, fragment, count)
        return 0
    if rest and rest[0] == "--around":
        fragment = rest[1] if len(rest) > 1 else "AttributeError"
        span = int(rest[2]) if len(rest) > 2 else 12
        around(path, fragment, span)
        return 0
    if rest and rest[0] == "--errors":
        errors(path, int(rest[1]) if len(rest) > 1 else 4)
        return 0
    counts, shown = sweep(path, int(rest[0]) if rest else 6)
    for name in BUCKETS:
        print(f"  {name}: {counts[name]}")
        for sample in shown[name]:
            print(f"      {sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
