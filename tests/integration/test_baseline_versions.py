import copy
import json
from pathlib import Path

import pytest
import pyv8

from pyv8c import Browser
from pyv8c.engine.idl import IDL_VERSION, Idl


def _document():
    """The bundled document as plain data, whole: these tests mutate it and write it back."""
    idl = Idl.load(profile=None)
    return {"idl": dict(idl.metadata), "interfaces": copy.deepcopy(dict(idl.interfaces))}


def _baseline_document():
    """The interfaces of the bundled document: what most of these tests mutate."""
    return _document()["interfaces"]


def _custom_baseline(path):
    document = _document()
    interfaces = document["interfaces"]
    interfaces["ReadableStream"]["parent"] = "EventTarget"
    interfaces["ReadableStream"]["tag"] = "VersionedReadableStream"
    interfaces["ReadableStream"]["constructor"]["length"] = 2
    members = interfaces["ReadableStream"]["members"]["prototype"]
    reader = next(member for member in members if member["name"] == "getReader")
    members.remove(reader)
    replacement = copy.deepcopy(reader)
    replacement["name"] = "alternateGetReader"
    members.append(replacement)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return document


def test_default_baseline_is_bundled_and_describes_snapshot():
    browser = Browser(stub_logging="off")
    try:
        assert browser.idl_path == "pyv8c:idl/idl.zip"
        info = browser.idl_info
        assert info["interface_count"] > 1000
        assert len(info["sha256"]) == 64
        assert info["idl"]["version"] == IDL_VERSION
    finally:
        browser.close()


def test_custom_baseline_path_is_absolute_and_snapshot_is_frozen(tmp_path, monkeypatch):
    baseline = tmp_path / "custom.json"
    document = _custom_baseline(baseline)
    monkeypatch.chdir(tmp_path)
    browser = Browser(idl_path="custom.json", stub_logging="off")
    original_hash = browser.idl_info["sha256"]
    try:
        assert browser.idl_path == str(baseline.resolve())
        document["idl"]["note"] = "changed-after-browser-creation"
        baseline.write_text(json.dumps(document), encoding="utf-8")
        assert browser.idl_info["sha256"] == original_hash
        assert "alternateGetReader" in {
            member["name"]
            for member in browser.bindings.idl.interfaces["ReadableStream"]["members"]["prototype"]
        }
        newer = Browser(idl_path=baseline, stub_logging="off")
        try:
            assert newer.idl_info["sha256"] != original_hash
        finally:
            newer.close()
    finally:
        browser.close()


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("[]", "root must be an object"),
        ("{}", "must state an 'idl' object and an 'interfaces' object"),
        ('{"idl": {"version": %d}, "interfaces": {}}' % IDL_VERSION,
         "must contain interfaces"),
    ],
)
def test_invalid_baseline_is_rejected_when_browser_is_created(tmp_path, content, error):
    path = tmp_path / "invalid.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        Browser(idl_path=path)


def test_missing_baseline_is_rejected_when_browser_is_created(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        Browser(idl_path=tmp_path / "missing.json")


def test_a_document_of_another_version_is_refused(tmp_path):
    document = _document()
    document["idl"]["version"] = 2
    path = tmp_path / "version-2.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match=f"expected {IDL_VERSION}"):
        Browser(idl_path=path)


def test_baseline_parent_cycle_is_rejected_when_browser_is_created(tmp_path):
    entry = lambda parent, tag: {
        "parent": parent, "members": {}, "constructor": {}, "tag": tag,
        "template": {}, "constants": [],
    }
    path = tmp_path / "cycle.json"
    path.write_text(json.dumps({
        "idl": {"version": IDL_VERSION},
        "interfaces": {"First": entry("Second", "First"), "Second": entry("First", "Second")},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="parent cycle"):
        Browser(idl_path=path)


def test_two_browsers_keep_generated_classes_and_templates_isolated(tmp_path):
    custom = tmp_path / "custom.json"
    _custom_baseline(custom)
    default_browser = Browser(stub_logging="off")
    custom_browser = Browser(idl_path=custom, stub_logging="off")
    try:
        assert default_browser.bindings.class_for("ReadableStream") is not custom_browser.bindings.class_for("ReadableStream")
        default_page = default_browser.new_context().new_page()
        custom_page = custom_browser.new_context().new_page()
        assert default_page.evaluate("[typeof ReadableStream.prototype.getReader, typeof ReadableStream.prototype.alternateGetReader]") == ["function", "undefined"]
        assert custom_page.evaluate("[typeof ReadableStream.prototype.getReader, typeof ReadableStream.prototype.alternateGetReader]") == ["undefined", "function"]
        assert default_page.evaluate("Object.getPrototypeOf(ReadableStream.prototype) === Object.prototype") is True
        assert custom_page.evaluate("Object.getPrototypeOf(ReadableStream.prototype) === EventTarget.prototype") is True
        assert default_page.evaluate("[ReadableStream.length, Object.prototype.toString.call(new ReadableStream())]") == [0, "[object ReadableStream]"]
        assert custom_page.evaluate("[ReadableStream.length, Object.prototype.toString.call(new ReadableStream())]") == [2, "[object VersionedReadableStream]"]

        custom_page.set_content("<main><div id='value'></div></main>")
        assert custom_page.evaluate("(() => { const node=document.querySelector('#value'); return [node instanceof Node, node instanceof Element, document instanceof Document, document.querySelector('#value') === node]; })()") == [True, True, True, True]

        default_browser.close()
        assert custom_page.evaluate("new ReadableStream().alternateGetReader()") is pyv8.Undefined
        assert custom_page.evaluate("typeof URL === 'function'") is True
    finally:
        default_browser.close()
        custom_browser.close()


def test_baseline_signatures_do_not_control_implementation_arguments(tmp_path):
    document = _document()
    contains = next(
        member for member in document["interfaces"]["DOMTokenList"]["members"]["prototype"]
        if member["name"] == "contains"
    )
    contains.setdefault("signature", {})["required"] = 2
    path = tmp_path / "strict-contains.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    default_browser = Browser(stub_logging="off")
    strict_browser = Browser(idl_path=path, stub_logging="off")
    try:
        default_page = default_browser.new_context().new_page()
        strict_page = strict_browser.new_context().new_page()
        default_page.set_content("<body></body>")
        strict_page.set_content("<body></body>")
        assert default_page.evaluate("document.body.classList.contains('x')") is False
        assert strict_page.evaluate("document.body.classList.contains('x')") is False
        assert strict_page.evaluate("document.body.classList.contains('x', 'ignored')") is False
    finally:
        default_browser.close()
        strict_browser.close()


def test_unresolved_signatures_do_not_prevent_structurally_valid_bindings(tmp_path):
    document = _document()
    contains = next(
        member for member in document["interfaces"]["DOMTokenList"]["members"]["prototype"]
        if member["name"] == "contains"
    )
    contains.setdefault("signature", {})["status"] = "unresolved"
    path = tmp_path / "unresolved-real.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    browser = Browser(idl_path=path, stub_logging="off")
    try:
        page = browser.new_context().new_page()
        assert page.evaluate("document.createElement('div').classList.contains('x')") is False
    finally:
        browser.close()

    document = _document()
    parse = next(
        member for member in document["interfaces"]["ReadableStream"]["members"]["prototype"]
        if member["name"] == "getReader"
    )
    parse.setdefault("signature", {})["status"] = "unresolved"
    path = tmp_path / "unresolved-stub.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    browser = Browser(idl_path=path, stub_logging="off")
    try:
        page = browser.new_context().new_page()
        assert page.evaluate("new ReadableStream().getReader(1, 2, 3)") is pyv8.Undefined
    finally:
        browser.close()


def test_worker_and_iframe_reuse_their_browser_bindings(tmp_path):
    custom = tmp_path / "custom.json"
    _custom_baseline(custom)
    browser = Browser(
        idl_path=custom,
        resource_loader=lambda _url, _request: {
            "body": "", "headers": {"access-control-allow-origin": "*"},
        },
        stub_logging="off",
    )
    try:
        page = browser.new_context().new_page()
        page.set_content("<iframe src='about:blank'></iframe>", url="http://example.test/")
        page.run_until_idle(timeout=1)
        frame_page = next(iter(page._owned.frames))
        assert frame_page.context_owner.browser.bindings is browser.bindings
        assert frame_page.evaluate("[typeof ReadableStream.prototype.getReader, typeof ReadableStream.prototype.alternateGetReader]") == ["undefined", "function"]

        page.evaluate("""(() => {
          const source = new Blob([
            "postMessage([typeof ReadableStream.prototype.getReader, typeof ReadableStream.prototype.alternateGetReader]);"
          ], {type: "text/javascript"});
          const worker = new Worker(URL.createObjectURL(source));
          worker.onmessage = event => window.__baselineFromWorker = event.data;
        })()""")
        page.run_until_idle(timeout=2)
        assert page.evaluate("__baselineFromWorker") == ["undefined", "function"]
    finally:
        browser.close()
