"""The two engine calls the widget catches and reports: what do they actually raise?

The fresh trace of the 2026-10-06 run has four raised calls; two of them are plain
``AttributeError`` -- a Python-level defect rather than a DOM error the page expects:

    [TOP] method: GPU.requestAdapter() -> undefined, error: <AttributeError>
    [TOP] method: RTCPeerConnection.createOffer({...}) -> undefined, error: <AttributeError>

The page stores our error object in one of its own variables and reports it, which is how a
missing adapter turns into a failed "environment check".  The trace only names the exception
type, so this probe wraps the two callbacks (read-only: the originals still run) and prints the
traceback that says which attribute is missing.

    python script/gpu_webrtc.py
"""
import json
import os
import pathlib
import sys
import threading
import time
import traceback

SCRIPT = r"""
(function () {
  var out = {};
  try {
    var gpu = navigator.gpu;
    out.gpu = gpu ? gpu.constructor.name : String(gpu);
    var adapter = gpu.requestAdapter();
    out.adapter = adapter && typeof adapter.then === 'function' ? 'promise' : String(adapter);
    if (adapter && adapter.then) {
      adapter.then(function (value) { out.adapter_value = String(value && value.constructor.name); },
                   function (error) { out.adapter_rejected = error.name; });
    }
  } catch (error) { out.gpu_threw = error.name + ': ' + String(error.message).slice(0, 80); }
  try {
    var pc = new RTCPeerConnection();
    var offer = pc.createOffer();
    out.offer = offer && typeof offer.then === 'function' ? 'promise' : String(offer);
    if (offer && offer.then) {
      offer.then(function (value) { out.offer_value = String(value && value.constructor.name); },
                 function (error) { out.offer_rejected = error.name; });
    }
  } catch (error) { out.offer_threw = error.name + ': ' + String(error.message).slice(0, 80); }
  setTimeout(function () { window.__done = true; }, 200);
  return JSON.stringify(out);
})()
"""


def watched(owner, name, label):
    """Wrap one callback so its traceback is visible; the original still runs."""
    original = getattr(owner, name)

    def wrapper(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        except BaseException:
            print(f"--- {label} raised from {type(self).__name__}.{name}:")
            for entry in traceback.format_exc().splitlines()[-6:]:
                print(f"      {entry.strip()[:150]}")
            raise

    setattr(owner, name, wrapper)


def main():
    from loguru import logger
    from pyv8c import Browser
    from pyv8c.web.bom.navigator import GPU
    from pyv8c.web.bom.webrtc import RTCPeerConnection

    logger.remove()
    watched(GPU, "fn_requestAdapter", "navigator.gpu.requestAdapter()")
    watched(RTCPeerConnection, "fn_createOffer", "RTCPeerConnection.createOffer()")

    def load(url, request):
        if not str(url).startswith(("http://", "https://")):
            return None
        return {"body": "<!doctype html><body>probe",
                "headers": {"content-type": "text/html"}}

    browser = Browser(stub_logging="off", threaded_loaders=True, real_network=False,
                      resource_loader=load)
    try:
        page = browser.new_context().new_page()
        page.set_content("<!doctype html><body>probe", url="https://probe.test/calls.html")
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except BaseException:  # noqa: BLE001
                pass
            time.sleep(0.02)
        print(f"  pyv8c : {page.evaluate(SCRIPT)}")
    finally:
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(10.0)
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
