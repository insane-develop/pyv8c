"""The bundles the challenge posts, and what is inside them.

    python script/payload.py capture here --seconds 20   # record pyv8c's requests
    python script/payload.py capture there --seconds 20  # record Chrome's
    python script/payload.py diff                        # how the two line up
    python script/payload.py decode                      # decode every payload

The payload is PX's own chain: base64 text with ``n`` characters of a keyed random
string spliced in, XORed byte by byte. The codec below is what cracking it produced:
``jT = 50`` for the XOR, ``kv(x, 10)`` for the key (the seed's base64 XORed with 10),
and the splice positions from the key's positional product.
"""
import argparse
import base64
import json
import pathlib
import sys
import threading
import time
import urllib.parse

from common import URL as CHALLENGE_URL, launch

OUT = pathlib.Path(__file__).resolve().parent.parent / "records.json"

#: The seed the splice positions come from: the challenge's own uuid.
SEED = CHALLENGE_URL.partition("uuid=")[2].split("&")[0]
SPLICED = 20


def interesting(url, request):
    target = str(url)
    return ("_px3" in target or "/px/" in target or "/captcha" in target
            or request.get("method", "GET").upper() == "POST")


def capture_here(seconds):
    from pyv8c import Browser
    records, lock = [], threading.Lock()

    def intercept(url, request):
        if interesting(url, request):
            with lock:
                records.append({
                    "url": str(url),
                    "method": str(request.get("method", "GET")).upper(),
                    "headers": {str(k).lower(): str(v)
                                for k, v in (request.get("headers") or {}).items()},
                    "body": None if request.get("body") is None else str(request.get("body")),
                })
        return None

    browser = Browser(stub_logging="off", resource_interceptor=intercept,
                      threaded_loaders=True)
    try:
        page = browser.new_context().new_page()
        page.goto(CHALLENGE_URL)
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)
    finally:
        browser.close()
    return records


def capture_there(seconds):
    from playwright.sync_api import sync_playwright
    records = []
    with sync_playwright() as playwright:
        browser = launch(playwright)
        try:
            page = browser.new_page()
            page.on("request", lambda request: records.append({
                "url": request.url, "method": request.method,
                "headers": {str(k).lower(): str(v) for k, v in request.headers.items()},
                "body": request.post_data,
            }) if interesting(request.url, {"method": request.method}) else None)
            page.goto(CHALLENGE_URL, wait_until="load", timeout=60000)
            page.wait_for_timeout(seconds * 1000)
        finally:
            browser.close()
    return records


def store(side, records, path):
    stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    stored[side] = records
    path.write_text(json.dumps(stored, indent=1), encoding="utf-8")
    return stored


def bundles(path):
    """``{side: [(rsc, payload), ...]}`` for the two sides recorded in *path*."""
    path = pathlib.Path(path)
    if not path.exists():
        print(f"{path} not found: record both sides first, "
              f"`payload.py capture here` and `capture there`")
        return {"here": [], "there": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for side in ("here", "there"):
        found = []
        for item in data.get(side, []):
            body = item.get("body") or ""
            if not body.startswith("payload="):
                continue
            fields = dict(part.split("=", 1) for part in body.split("&") if "=" in part)
            found.append((fields.get("rsc"), fields.get("payload", "")))
        out[side] = found
    return out


def diff(path):
    data = bundles(path)
    for index in range(max(len(data["here"]), len(data["there"]))):
        mine = data["here"][index] if index < len(data["here"]) else None
        theirs = data["there"][index] if index < len(data["there"]) else None
        if mine is None or theirs is None:
            print(f"  #{index + 1}: here={mine is not None} chrome={theirs is not None}")
            continue
        left, right = mine[1], theirs[1]
        at = 0
        while at < min(len(left), len(right)) and left[at] == right[at]:
            at += 1
        delta = len(right) - len(left)
        print(f"  #{index + 1}: rsc={mine[0]}/{theirs[0]} here={len(left)} chrome={len(right)} "
              f"delta={delta} ({100 * delta / len(right):.1f}%) prefix={at}")


def key_of(seed):
    return [ord(char) ^ 10 for char in base64.b64encode(str(seed).encode()).decode()]


def spots_of(key, count, length):
    def product(index):
        step = index // len(key) + 1
        return None if step >= len(key) else key[index % len(key)] * key[step]

    top = -1
    for index in range(count):
        value = product(index)
        if value is None:
            return None
        top = max(top, value)
    out = []
    for index in range(count):
        value = product(index)
        if value >= length:
            value = int(value / top * (length - 1))
        while value in out:
            value += 1
        out.append(value)
    return sorted(out)


def decode(token, seed=SEED, count=SPLICED):
    if (len(token) - count) % 4:
        return None
    spots = spots_of(key_of(seed), count, len(token) - count)
    if spots is None:
        return None
    drop = {spot - 1 for spot in spots}
    text = "".join(char for at, char in enumerate(token) if at not in drop)
    raw = base64.b64decode(text + "=" * (-len(text) % 4))
    return bytes(byte ^ 50 for byte in raw)


def show_decoded(path):
    data = bundles(path)
    for side in ("here", "there"):
        print(f"=== {side}")
        for rsc, token in data[side]:
            plain = decode(token)
            if plain is None:
                print(f"    rsc={rsc} token={len(token)}: undecodable")
                continue
            text = plain.decode("utf-8", "replace")
            try:
                records = json.loads(text)
                shape = [(record.get("t"), len(record.get("d") or {})) for record in records][:6]
            except Exception as error:  # noqa: BLE001
                shape = f"not json ({str(error)[:40]})"
            print(f"    rsc={rsc} token={len(token)} bytes={len(plain)} records={shape}")
            print(f"        {text[:200]}")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("capture", "diff", "decode"))
    parser.add_argument("side", nargs="?", choices=("here", "there"), default="here")
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--file", type=pathlib.Path, default=OUT)
    arguments = parser.parse_args(argv)

    if arguments.mode == "capture":
        started = time.time()
        records = (capture_here if arguments.side == "here" else capture_there)(arguments.seconds)
        store(arguments.side, records, arguments.file)
        print(f"{arguments.side}: {len(records)} records in {time.time() - started:.1f}s "
              f"-> {arguments.file}")
        for record in records:
            body = record["body"] or ""
            print(f"   {record['method']:4} {record['url'][:92]}")
            if body:
                print(f"        body={len(body)} :: {body[:120]}")
        return 0
    if arguments.mode == "diff":
        diff(arguments.file)
        return 0
    show_decoded(arguments.file)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
