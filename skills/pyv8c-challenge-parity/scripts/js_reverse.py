"""Read a page's own JavaScript without a parser: tables, call sites, constants, diffs.

Obfuscated bundles take their property names through a lookup (``obj[lm(p2.J)]``), so the
useful question is never "what does the AST look like" but "what do those keys mean".  The
table and its decoder are self-contained, so the reliable way to answer is to hand them to a
real engine and let it compute -- this script finds the pieces and, for ``run``, executes them
in pyv8c's own V8.

    python js_reverse.py tables  bundle.js
    python js_reverse.py calls   bundle.js lm
    python js_reverse.py strings bundle.js --min 12
    python js_reverse.py diff    a.js b.js
    python js_reverse.py run     bundle.js --prelude table.js --snippet "JSON.stringify(names)"

Every command is read-only and bounded; ``run`` needs pyv8c on ``PYTHONPATH`` and never touches
the network.
"""
import argparse
import collections
import pathlib
import re
import sys

#: A short string that could be a name rather than a payload.
SHORT = 40

#: Both quote styles: a bundle full of double quotes was invisible to the single-quoted-only
#: first version, which then reported "no candidate table" for a file that is mostly one.
_QUOTED = r"(?:'(?:[^'\\]|\\.){1,%d}'|\"(?:[^\"\\]|\\.){1,%d}\")" % (SHORT, SHORT)

#: ``['a','b','c','d','e','f']`` -- a table big enough to be worth reading.
ARRAY_TABLE = re.compile(r"\[\s*(?:%s\s*,\s*){6,}(?:%s)?\s*,?\s*\]" % (_QUOTED, _QUOTED))
OBJECT_TABLE = re.compile(r"\{\s*(?:[A-Za-z_$][\w$]*\s*:\s*%s\s*,\s*){6,}"
                          r"(?:[A-Za-z_$][\w$]*\s*:\s*%s)?\s*,?\s*\}" % (_QUOTED, _QUOTED))

#: Both quote styles, no lower bound here: the caller decides how long is interesting.  (A
#: ``%d`` left unapplied in the pattern made the first version match nothing at all.)
STRING_LITERAL = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"")
IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]{2,}")


def _read(path):
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


def _literal_strings(text):
    for match in STRING_LITERAL.finditer(text):
        yield match.start(), match.group(1) or match.group(2)


def cmd_tables(arguments):
    """The candidate lookup tables, longest first: the raw material for a name map."""
    text = _read(arguments.path)
    found = []
    for label, pattern in (("array", ARRAY_TABLE), ("object", OBJECT_TABLE)):
        for match in pattern.finditer(text):
            body = match.group(0)
            members = [part for _offset, part in _literal_strings(body)]
            found.append((len(members), label, match.start(), members))
    if not found:
        print("no candidate table found (try a smaller --min, or the table may be computed)")
        return 0
    for count, label, offset, members in sorted(found, reverse=True)[: arguments.limit]:
        print(f"--- {label} table at {offset} with {count} members")
        print("    " + ", ".join(repr(name) for name in members[:24]))
    return 0


def cmd_calls(arguments):
    """Every use of *symbol* with its surroundings: a call, or the member access round it.

    A name like ``p2`` never appears as a call -- it is ``lm(p2.J)`` -- so matching calls only
    would report "no call site" for the very symbol that needs reading.
    """
    text = _read(arguments.path)
    pattern = re.compile(r"\b" + re.escape(arguments.symbol) + r"\b")
    seen = 0
    for match in pattern.finditer(text):
        start = max(0, match.start() - arguments.context // 2)
        end = min(len(text), match.end() + arguments.context)
        snippet = " ".join(text[start:end].split())
        print(f"--- at {match.start()}")
        print(f"    {snippet[:arguments.context * 2]}")
        seen += 1
        if seen >= arguments.limit:
            break
    if not seen:
        print(f"{arguments.symbol!r} never appears literally -- it may be computed "
              f"(see 'tables') or spelled differently")
    return 0


def cmd_pool(arguments):
    """Packed string pools: one literal holding many names, split on a separator at run time.

    The obfuscator seen here packs a whole string table into a single literal delimited by
    ``!`` (``!qZtAM!tuQqD!matches!WEyFJ!…``), which no array/object pattern can find -- the
    separator is what makes it a pool, so look for that instead.
    """
    text = _read(arguments.path)
    pools = []
    for _offset, value in _literal_strings(text):
        for separator in ("!", "|", "\x01", "~"):
            parts = [part for part in value.split(separator) if part]
            if len(parts) >= arguments.min_members and \
                    len(parts) >= 0.6 * (value.count(separator) + 1):
                pools.append((len(parts), separator, parts))
                break
    if not pools:
        print("no packed pool found (the table may be built by arithmetic instead)")
        return 0
    for count, separator, parts in sorted(pools, reverse=True)[: arguments.limit]:
        print(f"--- pool of {count} parts on {separator!r}")
        print("    " + " ".join(parts[:40]))
    return 0


def cmd_strings(arguments):
    """Long unique literals: hosts, tokens, and the names an obfuscator left readable."""
    text = _read(arguments.path)
    seen, kept = set(), []
    for _offset, value in _literal_strings(text):
        if len(value) < arguments.min or value in seen:
            continue
        seen.add(value)
        kept.append(value)
    for value in kept[: arguments.limit]:
        print(f"  {len(value):6}  {value[:150]}")
    print(f"--- {len(kept)} unique literals of {arguments.min}+ chars")
    return 0


def cmd_diff(arguments):
    """What strings and identifiers one bundle has that the other does not."""
    left, right = _read(arguments.left), _read(arguments.right)
    for label, pattern in (("strings", None), ("identifiers", IDENTIFIER)):
        def collect(text):
            if pattern is None:
                return {value for _offset, value in _literal_strings(text)
                        if len(value) >= arguments.min}
            return set(pattern.findall(text))

        only_left = sorted(collect(left) - collect(right))
        only_right = sorted(collect(right) - collect(left))
        print(f"--- {label}: {len(only_left)} only in {pathlib.Path(arguments.left).name}, "
              f"{len(only_right)} only in {pathlib.Path(arguments.right).name}")
        for value in only_left[:20]:
            print(f"    - {value[:110]}")
        for value in only_right[:20]:
            print(f"    + {value[:110]}")
    return 0


def cmd_run(arguments):
    """Let a real engine compute: a prelude (the table and its decoder) plus your snippet."""
    import os
    import threading
    import time

    from pyv8c import Browser

    #: A prelude is either a file with the table and its decoder, or that code inline.  Testing
    #: for the file keeps both readings of the same flag without a second one to remember.
    prelude = ""
    if arguments.prelude == "-":
        prelude = sys.stdin.read()
    elif arguments.prelude:
        candidate = pathlib.Path(arguments.prelude)
        prelude = candidate.read_text(encoding="utf-8") if candidate.is_file() \
            else arguments.prelude

    def load(url, request):
        if not str(url).startswith(("http://", "https://")):
            return None
        return {"body": "<!doctype html><body>", "headers": {"content-type": "text/html"}}

    browser = Browser(stub_logging="off", threaded_loaders=True, real_network=False,
                      resource_loader=load)
    try:
        page = browser.new_context().new_page()
        page.set_content("<!doctype html><body>", url="https://reverse.test/")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except BaseException:  # noqa: BLE001
                pass
            time.sleep(0.02)
        result = page.evaluate(
            "(function () { " + prelude + "\n; return (" + arguments.snippet + "); })()")
        print(f"  result: {result}")
    finally:
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(10.0)
        sys.stdout.flush()
        os._exit(0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    tables = sub.add_parser("tables", help="candidate lookup tables (arrays/objects of names)")
    tables.add_argument("path")
    tables.add_argument("--limit", type=int, default=4)
    tables.set_defaults(handler=cmd_tables)

    calls = sub.add_parser("calls", help="call sites of a symbol, with context")
    calls.add_argument("path")
    calls.add_argument("symbol")
    calls.add_argument("--context", type=int, default=200)
    calls.add_argument("--limit", type=int, default=8)
    calls.set_defaults(handler=cmd_calls)

    pool = sub.add_parser("pool", help="packed string pools (one literal, split at run time)")
    pool.add_argument("path")
    pool.add_argument("--min-members", type=int, default=8)
    pool.add_argument("--limit", type=int, default=3)
    pool.set_defaults(handler=cmd_pool)

    strings = sub.add_parser("strings", help="unique long literals")
    strings.add_argument("path")
    strings.add_argument("--min", type=int, default=8)
    strings.add_argument("--limit", type=int, default=40)
    strings.set_defaults(handler=cmd_strings)

    diff = sub.add_parser("diff", help="strings/identifiers only one bundle has")
    diff.add_argument("left")
    diff.add_argument("right")
    diff.add_argument("--min", type=int, default=8)
    diff.set_defaults(handler=cmd_diff)

    run = sub.add_parser("run", help="evaluate a snippet in pyv8c's V8, with a prelude")
    #: The path is only used by the offline commands; ``run`` may take everything it needs
    #: from ``--prelude``/``--snippet``, so requiring it would make the engine path unusable.
    run.add_argument("path", nargs="?", default="")
    run.add_argument("--prelude", default="")
    run.add_argument("--snippet", required=True)
    run.set_defaults(handler=cmd_run)

    arguments = parser.parse_args(argv)
    return arguments.handler(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
