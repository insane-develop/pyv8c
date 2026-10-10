"""Per-Browser interface bindings: one baseline view, one compiler, one plan per realm.

The composition root: reading the baseline is :mod:`.idl`'s business, turning it
into specs is :mod:`.assembly`'s, and this holds what the Browsers share -- the class
each interface name answers with, and the mismatches tolerated once each.
"""

from __future__ import annotations

import threading
from dataclasses import replace

from .binder import Binder
from .idl import (
    DEFAULT_IDL_PATH,
    Idl,
    RealmPlan,
    build_plan,
)
from .features import FeatureRuntime
from ..idl.spec import InterfaceSpec
from .overrides import MemberOverrides
from ..foundation.logging import logger

__all__ = [
    "DEFAULT_IDL_PATH",
    "Idl",
    "Bindings",
]


#: Stub classes for the interfaces with no body, one per document and logging mode: stable
#: identity is what lets a later Browser reuse their specs (``assembly._COMPILED``).
_GENERATED: dict = {}


class Bindings:
    """One IDL collection, the Python implementations, and this Browser's host hooks."""

    def __init__(self, idl: Idl, *, runtime: FeatureRuntime, stub_logging="off",
                 expose_unimplemented=True, baseline_mismatch="warn", interface_overrides=None):
        self.idl = idl
        #: Host hooks that answer IDL members, checked against the document here.
        self.overrides = MemberOverrides.parse(interface_overrides, self.idl)
        self.runtime = runtime
        self.stub_logging = str(stub_logging or "off").lower()
        self.expose_unimplemented = bool(expose_unimplemented)
        if self.stub_logging not in ("on", "verbose", "off"):
            raise ValueError("stub_logging must be 'on', 'verbose' or 'off'")
        self.baseline_mismatch = str(baseline_mismatch or "error").lower()
        if self.baseline_mismatch not in ("error", "warn"):
            raise ValueError("baseline_mismatch must be 'error' or 'warn'")
        #: Reported mismatches, so each difference warns once.
        self._mismatches = set()
        self.staged_interfaces = runtime.catalog.skeleton()
        self._validated = False
        # One re-entrant lock guards every read-modify-write cache so concurrent realms never see a torn cache.
        self._lock = threading.RLock()
        self._classes: dict[str, type] = {}
        self._plans: dict[str, RealmPlan] = {}
        self._binder = Binder(self, stub_logging=self.stub_logging, lock=self._lock)

    def report_mismatch(self, message):
        """Report an implementation/baseline mismatch; true when it is tolerated.

        ``warn`` reports once and lets the caller skip undescribed members; ``error``
        leaves raising to the caller.
        """
        if self.baseline_mismatch != "warn":
            return False
        if message not in self._mismatches:
            self._mismatches.add(message)
            if self.stub_logging != "off":
                logger.warning("pyv8c: {}", message)
        return True

    @property
    def discrepancies(self) -> tuple:
        """Each implementation/baseline difference this bindings tolerated, once."""
        return tuple(sorted(self._mismatches))

    def spec(self, name):
        """The raw baseline spec of one interface."""
        try:
            return self.idl.raw(name)
        except KeyError as exc:
            if self.report_mismatch(f"interface {name!r} is missing from the interface baseline"):
                return {}
            raise TypeError(f"interface {name!r} is missing from Chromium interface baseline") from exc

    def plan(self, context_type="Window"):
        """What a realm of *context_type* installs, computed once."""
        with self._lock:
            cached = self._plans.get(context_type)
            if cached is None:
                cached = build_plan(
                    self.idl, context_type,
                    # An interface somebody hooks is wanted, even with no implementation.
                    implements=set(self.runtime.catalog.interfaces) | self.overrides.interfaces,
                    expose_unimplemented=self.expose_unimplemented,
                    eager=self.staged_interfaces,
                )
                cached = replace(cached, ready=self._startup_names(cached.eager, context_type))
                self._plans[context_type] = cached
            return cached

    def _startup_names(self, eager, context_type):
        """What a realm has built once ready: the skeleton, its groups' ancestors,
        and the alias targets the window declares."""
        seeds = set(eager)
        if context_type == "Window":
            seeds.update(target for target in self.idl.window_aliases().values()
                         if self.describes(target))
        built = set()
        for name in seeds:
            for group_name in self.runtime.interface_group(name):
                built.update(self.idl.ancestors(group_name))
        return tuple(sorted(built))

    def class_for(self, name):
        """Return the one canonical class object per interface name.

        The lock makes creation idempotent across realms: two pages materializing the
        same lazy interface must not build distinct classes, because the native
        template cache keys on the class and cross-realm ``instanceof`` would break.
        """
        with self._lock:
            cls = self._classes.get(name)
            if cls is not None:
                return cls
            spec = self.spec(name)
            parent_name = spec.get("parent")
            parent = self.class_for(parent_name) if parent_name else object
            implementation = self.runtime.interface(name)
            if implementation is None:
                key = (getattr(self.idl, "digest", ""), name, parent_name, self.stub_logging)
                cls = _GENERATED.get(key)
                if cls is None:
                    cls = type(name, (parent,), {
                        "__module__": "pyv8c.engine.generated",
                        "__init__": self._binder.stubs.constructor(name),
                        "__idl_spec__": InterfaceSpec(name, parent_name),
                        "__v8_idl_stub__": True,
                    })
                    _GENERATED[key] = cls
            else:
                cls = implementation
            self._classes[name] = cls
            return cls

    def class_config_for(self, name, realm=None):
        """The spec one interface is built from in one realm."""
        return self._binder.bind(name, realm)

    def describes(self, name):
        """Whether this bindings can commit an overlay for ``name``."""
        if name not in self.idl.interfaces:
            return False
        return getattr(self.class_for(name), "__name__", None) == name

    def validate(self):
        """Compile every interface once, to surface mismatches before a page runs."""
        with self._lock:
            if not self._validated:
                for name in self.idl.interfaces:
                    self._binder.compile(name)
                self._validated = True
