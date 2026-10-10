"""Align the two exchange tables: does the flow match, and where does it stop matching.

    python script/flow_compare.py                    # counts per endpoint kind, both sides
    python script/flow_compare.py --kind pat         # the URLs and headers of one kind
    python script/flow_compare.py --kind ci

The question this answers first is the cheap one -- *how many* requests each side makes, and of
what kind -- because a count mismatch is a fact, while "the flow differs" is a conclusion.  The
second question is which kinds exist on one side only, which is where the divergence starts.

Reads ``run-pyv8c.txt`` and ``run-chrome.txt``, both written by the run scripts in this case.
"""
import argparse
import collections
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: ``    GET  https://… -> 200 1234B 56ms`` and Chrome's ``-> 200 -1B`` variant.
ROW = re.compile(r"\s+(GET|POST|OPTIONS)\s+(\S+)\s+->\s+(\d{3}|-1)\s+(-?\d+)B")
#: ``         hdr : name=value; name=value``
HDR = re.compile(r"\s+hdr\s*:\s*(.*)")

#: One label per endpoint shape, so the two runs can be compared without their tokens.
KINDS = (
    ("orchestrate", lambda url: "/orchestrate/" in url),
    ("turnstile/api.js", lambda url: "/turnstile/v0/" in url),
    ("turnstile/frame", lambda url: "/turnstile/f/" in url),
    ("fo@steamdb", lambda url: "/fo/" in url and "steamdb.info" in url),
    ("fo@cf", lambda url: "/fo/" in url and "challenges.cloudflare.com" in url),
    ("blob", lambda url: url.startswith("blob:")),
    ("pat", lambda url: "/pat/" in url),
    ("ci", lambda url: "/ci/" in url),
    ("i@hagen", lambda url: "/i/" in url),
    ("eb", lambda url: "/eb/" in url),
    ("root", lambda url: url.endswith("steamdb.info/")),
    ("other", lambda url: True),
)


def kind_of(url):
    for name, test in KINDS:
        if test(url):
            return name
    return "other"


def lines_of(path):
    """The file's lines, whatever encoding the shell that wrote it chose.

    PowerShell's ``>`` writes UTF-16LE, not UTF-8: reading one of these reports as UTF-8 finds
    NUL bytes between every character and matches nothing at all -- which is how this tool first
    reported "no rows" for two files that were sitting right there.
    """
    raw = pathlib.Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace").splitlines()
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig", errors="replace").splitlines()
    return raw.decode("utf-8", errors="replace").splitlines()


def read(path):
    """Every exchange row of one run, in order: ``(method, url, status, size, headers)``."""
    rows = []
    current = None
    for line in lines_of(path):
        match = ROW.match(line)
        if match:
            current = [match.group(1), match.group(2), match.group(3), int(match.group(4)), ""]
            rows.append(current)
            continue
        header = HDR.match(line)
        if header and current is not None:
            current[4] = header.group(1)
    return rows


def counts(rows):
    return collections.Counter(kind_of(row[1]) for row in rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kind", help="show the URLs (and headers) of one endpoint kind")
    parser.add_argument("--audit", help="segment lengths and odd characters of one kind")
    parser.add_argument("--pyv8c", default=str(ROOT / "run-pyv8c.txt"))
    parser.add_argument("--chrome", default=str(ROOT / "run-chrome.txt"))
    arguments = parser.parse_args(argv)

    mine = read(arguments.pyv8c)
    theirs = read(arguments.chrome)
    if not mine or not theirs:
        #: Say what was actually seen, not just that nothing matched: a regex that quietly
        #: matches nothing is how a comparison tool reports "no difference" for two files it
        #: never read.
        print(f"! no rows read (pyv8c {len(mine)}, chrome {len(theirs)})")
        for path in (arguments.pyv8c, arguments.chrome):
            target = pathlib.Path(path)
            print(f"  {target.name}: exists={target.is_file()}")
            if target.is_file():
                lines = lines_of(target)
                print(f"    {len(lines)} lines; first three that look like a row:")
                shown = 0
                for line in lines:
                    if "->" in line and ("GET" in line or "POST" in line):
                        #: ``ascii``: the run's output carries decoded page text, and a GBK
                        #: console cannot print what a challenge page happens to contain.
                        print(f"      {ascii(line[:130])}")
                        shown += 1
                        if shown == 3:
                            break
        return 1

    if arguments.audit:
        #: The path a request line carries has to survive the wire unchanged; a character the
        #: server does not expect, or a segment of the wrong shape, is answered 400 before
        #: anything else about the request is looked at.
        safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                   "-._~:/?#[]@!$&'()*+,;=%")
        for label, rows in (("pyv8c", mine), ("chrome", theirs)):
            for method, url, status, size, headers in rows:
                if kind_of(url) != arguments.audit:
                    continue
                path = url.split("challenge-platform/h/g/", 1)[-1]
                segments = path.split("/")
                odd = sorted({char for char in url if char not in safe})
                print(f"--- {label} {method} {status} {size}B len={len(url)}")
                print(f"    odd chars: {odd if odd else 'none'}")
                print(f"    segments : {[len(part) for part in segments]}")
                print(f"    shape    : {'/'.join(part[:12] + ('…' if len(part) > 12 else '') for part in segments)}")
        return 0

    if arguments.kind:
        for label, rows in (("pyv8c", mine), ("chrome", theirs)):
            print(f"--- {label} ({arguments.kind})")
            for method, url, status, size, headers in rows:
                if kind_of(url) != arguments.kind:
                    continue
                print(f"    {method:4} {status} {size}B len={len(url)}")
                print(f"      {url}")
                if headers:
                    print(f"      hdr: {headers}")
        return 0

    mine_counts, theirs_counts = counts(mine), counts(theirs)
    print(f"requests: pyv8c {len(mine)}  /  chrome {len(theirs)}"
          f"   (difference {len(mine) - len(theirs):+d})")
    print(f"  {'kind':16} {'pyv8c':>6} {'chrome':>7}   note")
    for name, _test in KINDS:
        left, right = mine_counts.get(name, 0), theirs_counts.get(name, 0)
        if not left and not right:
            continue
        note = ""
        if left and not right:
            note = "only pyv8c"
        elif right and not left:
            note = "only chrome"
        elif left != right:
            note = "count differs"
        print(f"  {name:16} {left:>6} {right:>7}   {note}")

    print("flow (in order):")
    for label, rows in (("pyv8c ", mine), ("chrome", theirs)):
        sequence = " ".join(kind_of(url) for _m, url, _s, _b, _h in rows)
        print(f"  {label}: {sequence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
