"""Every member of the main DOM/BOM objects, both sides, side by side.

    python312 script/surface_diff.py                        # the curated holders, summarised
    python312 script/surface_diff.py --probe surface_wide   # every interface the window exposes
    python312 script/surface_diff.py navigator              # only what a holder name contains
    python312 script/surface_diff.py --only-missing
    python312 script/baseline_join.py --probe surface_wide  # what the baseline says about each

The question this answers is the one a challenge asks first: does ``navigator`` here have the
members Chrome's ``navigator`` has, with the same kinds?  It is answered by **enumeration on both
sides** (``probes.surface``) rather than by a hand-written name list -- a list can only confirm
what somebody already thought of, and what a bot wall reads is the member nobody wrote down.

Three difference classes, and they are not equally bad:

* **only Chrome** -- a property or method the page can read there and gets ``undefined`` (or a
  ``TypeError``) here.  This is the class the challenge trace keeps pointing at.
* **only here** -- extra surface, which is its own fingerprint: a real Chrome has no such member.
* **different kind** -- present both sides, but as an accessor where Chrome has a value, as a
  function of a different arity, or enumerable where Chrome hides it.

Read-only: descriptors only, no getter is ever run, and the page is the skill's own offline
fixture, so no third-party site is touched.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parents[2] / "skills" / "pyv8c-challenge-parity" / "scripts"
sys.path.insert(0, str(SKILL))

from harness import FIXTURE_URL, _ask_page  # noqa: E402
from probes import PROBES  # noqa: E402

#: How many names of one class to print per holder; the JSON beside it carries them all.
SHOWN = 14
OUT = HERE.parent


def _stringy(answer):
    """Every name as a string: a storage object hands back ``0``/``1`` as integer keys, and
    sorting or comparing those against real names raises instead of reporting."""
    return {str(holder): {str(name): str(kind) for name, kind in (members or {}).items()}
            for holder, members in (answer or {}).items()}


def collect(probe_name="surface", filter_text=None):
    spec = PROBES[probe_name]
    page_html, settle = spec["fixture"]
    mine, theirs = _ask_page(FIXTURE_URL.format(probe_name), page_html, spec["probe"], settle)
    for label, answer in (("pyv8c", mine), ("chrome", theirs)):
        if isinstance(answer, dict) and "__error" in answer:
            print(f"! {label} could not answer the probe: {answer['__error']}")
            return None
        if not isinstance(answer, dict):
            print(f"! {label} answered {type(answer).__name__}, not an object: {answer!r}")
            return None
    mine, theirs = _stringy(mine), _stringy(theirs)
    (OUT / f"surface-{probe_name}-pyv8c.json").write_text(
        json.dumps(mine, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    (OUT / f"surface-{probe_name}-chrome.json").write_text(
        json.dumps(theirs, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    if filter_text:
        keep = [name for name in sorted(set(mine) | set(theirs)) if filter_text in name.lower()]
        mine = {name: mine.get(name, {}) for name in keep}
        theirs = {name: theirs.get(name, {}) for name in keep}
    return mine, theirs


def _members(holder_map):
    """The member map without the probe's ``__`` diagnostics, which are not members.

    ``__walkError``/``__error`` say the probe could not read the holder and ``__missing`` says
    there is no such object on that side.  Counting those as members made six function-shaped
    namespaces look like a hundred and forty differences, which is a report nobody can act on.
    """
    return {name: kind for name, kind in (holder_map or {}).items() if not name.startswith("__")}


def _classify(left, right):
    """``(only_right, only_left, kind_differs)`` for one holder's member maps."""
    only_right = sorted(set(right) - set(left))
    only_left = sorted(set(left) - set(right))
    kind_differs = sorted(name for name in set(left) & set(right) if left[name] != right[name])
    return only_right, only_left, kind_differs


def _show(title, holder, names, markers, cap=SHOWN):
    if not names:
        return
    print(f"    {title} ({len(names)}):")
    for name in names[:cap]:
        mark = f"  {markers.get(name, '')}" if markers else ""
        print(f"      {holder}.{name}{mark}")
    if len(names) > cap:
        print(f"      ... and {len(names) - cap} more (see surface-*.json)")


def _value_of(argv, flag):
    """The word after *flag*, or ``None``."""
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def main(argv):
    only_missing = "--only-missing" in argv
    #: ``surface`` is the curated list of holders, ``surface_wide`` every interface the window
    #: exposes.  The second is the one that finds what the first never thought to ask about.
    probe_name = _value_of(argv, "--probe") or "surface"
    if probe_name not in PROBES:
        print(f"! no probe named {probe_name!r}; try one of {', '.join(sorted(PROBES))}")
        return 1
    flags = {"--probe", "--holder"}
    words = [word for word in argv
             if not word.startswith("-") and word not in flags
             and word != _value_of(argv, "--probe") and word != _value_of(argv, "--holder")]
    filter_text = words[0].lower() if words else None
    collected = collect(probe_name=probe_name, filter_text=filter_text)
    if collected is None:
        return 1
    mine, theirs = collected

    totals = {"only_chrome": 0, "only_here": 0, "kind": 0}
    holders = sorted(set(mine) | set(theirs))
    print(f"holders: {len(holders)}   (pyv8c {len(mine)}, chrome {len(theirs)})")
    print(f"{'holder':28} {'pyv8c':>6} {'chrome':>7}  {'only-chrome':>11} {'only-here':>10} "
          f"{'kind':>5}")
    rows = {}
    missing_holder = {}
    frame_window = {}
    for holder in holders:
        raw_left, raw_right = mine.get(holder) or {}, theirs.get(holder) or {}
        #: A holder reached through a frame slot is another browsing context seen from outside:
        #: both sides answer with a facade there, so its own names are not that window's surface.
        if any("__frameWindow" in raw for raw in (raw_left, raw_right)):
            frame_window[holder] = True
            rows[holder] = ([], [], [])
            print(f"{holder:28} {'-':>6} {'-':>7}  frame window, not comparable")
            continue
        absent = [label for label, raw in (("pyv8c", raw_left), ("chrome", raw_right))
                  if "__missing" in raw]
        if absent:
            #: One side has no such object at all (a namespace's ``.prototype`` is ``undefined``
            #: in Chrome).  Reporting its members as differences would be true but useless.
            missing_holder[holder] = absent
            rows[holder] = ([], [], [])
            print(f"{holder:28} {'-':>6} {'-':>7}  holder absent on {', '.join(absent)}")
            continue
        left, right = _members(raw_left), _members(raw_right)
        only_right, only_left, kind_differs = _classify(left, right)
        rows[holder] = (only_right, only_left, kind_differs)
        totals["only_chrome"] += len(only_right)
        totals["only_here"] += len(only_left)
        totals["kind"] += len(kind_differs)
        if only_right or only_left or kind_differs:
            print(f"{holder:28} {len(left):6} {len(right):7}  {len(only_right):11} "
                  f"{len(only_left):10} {len(kind_differs):5}")

    print(f"\ntotals: only-chrome {totals['only_chrome']}  only-here {totals['only_here']}  "
          f"kind {totals['kind']}")
    if frame_window:
        #: Counted apart, not held against the engines: the holders are listed so a reading can
        #: never lose one silently.
        print(f"not counted: {len(frame_window)} frame window(s) "
              f"({', '.join(sorted(frame_window))})")
    if missing_holder:
        print(f"not counted: {len(missing_holder)} holder(s) absent on one side")

    #: ASCII labels on purpose: this runs into a GBK console, and a report that dies on its own
    #: section header reports nothing (the JSON beside it keeps every name either way).
    print("\n--- only-chrome: the page can read it there, undefined here")
    for holder in holders:
        only_right, _only_left, _kind = rows[holder]
        _show("members", holder, only_right, {name: f"= {theirs[holder][name]}" for name in only_right})

    if not only_missing:
        print("\n--- only-here: extra surface, which is a fingerprint of its own")
        for holder in holders:
            _only_right, only_left, _kind = rows[holder]
            _show("members", holder, only_left,
                  {name: f"= {mine[holder][name]}" for name in only_left})

    print("\n--- both sides, different kind or arity")
    for holder in holders:
        _only_right, _only_left, kind_differs = rows[holder]
        markers = {name: f"pyv8c {mine[holder][name]}  |  chrome {theirs[holder][name]}"
                   for name in kind_differs}
        _show("members", holder, kind_differs, markers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
