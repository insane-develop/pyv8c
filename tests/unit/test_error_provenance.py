"""An error a realm can be shown is built where a page could be the cause of it.

The rule: only ``pyv8c/web/**`` builds the errors a realm may be shown -- the ones
``pyv8c.web.page_errors`` declares in its ``VISIBLE`` -- because those are the failures
a browser tells a page about.  What is built deeper -- a load the host refused, a
transport that failed, a capability that is not there -- belongs to the host, and
reaches the host or the log instead of a realm.

The registry holds the exceptions to that, each with the reason a realm cannot be shown
it anyway.  It may only fall: a new place that builds a visible error has to be reviewed
and written down here rather than slipping in.  The judgement is by module identity --
which module a name comes from -- never by how a module happens to be spelled.
"""
import ast
from pathlib import Path

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

import pyv8

from pyv8c import foundation
from pyv8c.web import page_errors as vocabulary

PACKAGE = Path(foundation.__file__).resolve().parent.parent

#: The page's own word for the module, and the name it is imported by when a module
#: takes the module itself rather than names out of it.
VOCABULARY_MODULE = "page_errors"

#: What the vocabulary declares visible, as it states it: nothing here is sniffed out
#: of ``__all__`` by the shape of the objects found there.
VISIBLE = set(vocabulary.VISIBLE)

#: The one class of the family a layer below ``web`` owns: the conversions raise it where
#: they are called, which is a web member's own call site.
VISIBLE |= {"IdlArgumentError"}

#: ``module -> (how many places build one, why a realm cannot be shown it anyway)``.
ALLOWED = {
    # The IDL conversion layer: it owns its error, which inherits the binding's own
    # ``TypeError``, so a bad argument reads in a realm as a browser reads it.  It
    # cannot move up into the web layer: the conversions run where a web member calls
    # them, and a visible error raised there is the member's own doing.
    "pyv8c/foundation/arguments.py":
        (17, "the conversions and arity errors a web member raises at its own call site"),
}


def _modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        if relative.startswith(("web/", "data/")):
            continue
        yield path, f"pyv8c/{relative}"


def _from_vocabulary(node):
    """Whether an ``import from`` names the page vocabulary."""
    module = node.module or ""
    return module == VOCABULARY_MODULE or module.endswith("." + VOCABULARY_MODULE)


def _visible_imports(tree):
    """Names a module builds a visible error with.

    Bound from the page vocabulary, an alias for that module itself, and -- for the
    module that declares one -- the class it defines.
    """
    names, module_alias = set(), None
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in VISIBLE:
            names.add(node.name)
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if _from_vocabulary(node):
                names.add(alias.asname or alias.name)
            elif not (node.module or "") and alias.name == VOCABULARY_MODULE:
                # ``from .. import page_errors as errors``: the module, under an alias.
                module_alias = alias.asname or alias.name
            elif alias.name in VISIBLE:
                # A visible name bound from the module that declares it.
                names.add(alias.asname or alias.name)
    return names, module_alias


def _built(path):
    """The lines of *path* that build a visible error, as the module imports it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names, module_alias = _visible_imports(tree)
    lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        holder = node.func
        if isinstance(holder, ast.Attribute):
            on_vocabulary = (isinstance(holder.value, ast.Name)
                             and module_alias is not None and holder.value.id == module_alias)
            if on_vocabulary and holder.attr in VISIBLE:
                lines.add(node.lineno)
            continue
        if getattr(holder, "id", None) in names and holder.id in VISIBLE:
            lines.add(node.lineno)
    return lines


#: The classes this layer exports that a realm must never be shown: the host's own.
HOST_ONLY = {"DOMError", "HostFailure"}


def test_the_layer_exports_no_error_that_could_never_cross():
    """What counts as a page-visible error is inherited, so a class that neither inherits
    the family nor is one of the host's own could never be shown to a realm -- exporting
    one would be a declaration that silently does nothing.
    """
    strays = []
    for name in vocabulary.__all__:
        value = getattr(vocabulary, name, None)
        if not isinstance(value, type) or name in HOST_ONLY:
            continue
        if not issubclass(value, pyv8.JsError):
            strays.append(name)
    assert not strays, strays


def test_the_visible_declaration_names_what_is_exported():
    """``VISIBLE`` is a statement about this module's own exports, so every name it
    lists has to be one the module actually offers.
    """
    missing = sorted(set(vocabulary.VISIBLE) - set(vocabulary.__all__))
    assert not missing, missing


def test_the_root_only_exports_what_the_vocabulary_declares_visible():
    """The package root hands applications errors, so it may only hand over ones the
    vocabulary itself declares a realm may be shown: the fourth copy of the list is
    derived, not maintained.
    """
    import pyv8c

    handed = {name for name, module in pyv8c._EXPORTS.items()
              if module.endswith("." + VOCABULARY_MODULE)}
    assert handed <= VISIBLE, sorted(handed - VISIBLE)


def test_only_the_web_layer_builds_what_a_realm_can_be_shown():
    built = {}
    for path, name in _modules():
        found = _built(path)
        if found:
            built[name] = len(found)
    unregistered = {name: count for name, count in built.items() if name not in ALLOWED}
    assert not unregistered, (
        "these build an error a realm may be shown, outside the web layer: "
        f"{sorted(unregistered.items())}; either produce it where a page can cause it, "
        "or register the place here with the reason a realm cannot see it"
    )
    off = {name: (allowed, built.get(name, 0)) for name, (allowed, _why) in ALLOWED.items()
           if built.get(name, 0) != allowed}
    assert not off, (f"the allowance is exact (allowed, found): {off}; "
                     "lower it when a place falls away")


def test_only_the_web_layer_names_the_vocabulary():
    """Nothing below ``web`` may reach for an error a realm can be shown."""
    offenders = {}
    for path, name in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not _from_vocabulary(node):
                continue
            allowed = set()
            extra = sorted({alias.name for alias in node.names} - allowed)
            if extra:
                offenders[name] = extra
    assert not offenders, f"these name the error vocabulary outside web: {offenders}"
