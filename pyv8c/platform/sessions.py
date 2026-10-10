"""One realm's binding state: the classes it materialised, and the specs behind them.

The native realm asks its ``lazy_factory`` about a name it has no template for, and the
answer carries the specs that name needs -- staged before the template that reads them.
"""

from __future__ import annotations

from types import MappingProxyType

from pyv8 import IdlSpec


class BindingSession:
    """Context-local registration state for one V8 realm."""

    def __init__(self, bindings, realm=None):
        self.bindings = bindings
        self.realm = realm
        self._configs: dict[type, IdlSpec] = {}
        self._materialized: set[str] = set()

    def detach(self):
        """Let go of everything this realm built, so a closed realm keeps nothing."""
        self._configs.clear()
        self._materialized.clear()
        self.realm = None

    def interface_names(self, context_type="Window"):
        return self.bindings.plan(context_type).installs

    def class_for(self, name):
        return self.bindings.class_for(name)

    @property
    def materialized_interfaces(self):
        return tuple(sorted(self._materialized))

    @property
    def configs(self):
        """Read-only view of the specs this realm materialized."""
        return MappingProxyType(self._configs)

    def prepare(self, context_type="Window"):
        """Stage the interfaces a realm builds before its first script runs.

        The plan calls it staged, so this keeps that work off the first script's path.
        """
        self._stage(self.bindings.plan(context_type).eager)

    def materialize(self, name):
        """Stage what *name* needs, and answer the class behind it."""
        self._stage((name,))
        return self.class_for(name)

    def class_config_for(self, name):
        self.materialize(name)
        return self._configs[self.class_for(name)]

    def add_extra_class(self, name, cls):
        """Register a class this realm answers under a name the baseline does not describe."""
        if getattr(self, "_extras", None) is None:
            self._extras = {}
        self._extras[str(name)] = cls

    def lazy_factory(self, context_type="Window", on_materialize=None):
        """The realm's one source of specs, in the shape the native Context calls.

        A name it cannot describe answers ``None``, which the native side remembers, so a
        refusal costs one call; a name it can answers the class plus its chain's specs.
        ``on_materialize`` hears each name the realm built something for.
        """

        def answer(name):
            name = str(name)
            extra = getattr(self, "_extras", {}).get(name)
            if extra is not None:
                if on_materialize is not None:
                    on_materialize(name)
                return extra
            if not self.bindings.describes(name):
                return None
            cls = self.class_for(name)
            chain = self._stage((name,))
            if on_materialize is not None:
                on_materialize(name)
            return cls, {interface_name: self._configs[self.class_for(interface_name)]
                         for interface_name in chain}

        answer.__name__ = "__pyv8c_lazy_interface"
        return answer

    def _stage(self, names):
        """Stage the specs for *names* and their ancestors, innermost first.

        Answers the chain it covered: an overlay has to reach the native realm before the
        template that reads it, however long ago the chain was staged.
        """
        staged = {}
        chain = []
        materialized = set()
        for name in names:
            for group_name in self.bindings.runtime.interface_group(name):
                for interface_name in reversed(tuple(self.bindings.idl.ancestors(group_name))):
                    cls = self.class_for(interface_name)
                    if cls not in self._configs and cls not in staged:
                        staged[cls] = self.bindings.class_config_for(interface_name, self.realm)
                    chain.append(interface_name)
                    materialized.add(interface_name)
        self._configs.update(staged)
        self._materialized.update(materialized)
        return chain
