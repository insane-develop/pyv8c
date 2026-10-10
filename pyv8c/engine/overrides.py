"""Host hooks that answer interface members in place of their implementation.

A member is bound to one Python callable when its interface is compiled from the
interface baseline (see :mod:`.assembly`), so a hook replaces that callable: the
member's location, length, flags and read-only state still come from the baseline, and
the implementation classes stay untouched.  That is what lets one Browser answer a
member that is expensive -- or not implemented at all -- with whatever the host wants.

A hook is an ordinary function, which is what keeps its arguments the same as a real
implementation's: the native side decides whether a receiver is passed from the
member's location, and a bound method would silently take that receiver away.  So a
prototype method is called as ``hook(receiver, *args)``, a static method as
``hook(*args)``, a getter as ``hook(receiver)`` and a setter as ``hook(receiver,
value)`` (the receiver is absent where the member sits on the interface itself).
"""
from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["OVERRIDE_SLOTS", "MemberHook", "MemberOverrides"]

#: The slots each kind of member can be answered in.
OVERRIDE_SLOTS: dict[str, tuple[str, ...]] = {
    "operation": ("call",),
    "attribute": ("get", "set"),
}


@dataclass(frozen=True)
class MemberHook:
    """What answers one member: its call, its getter, or its setter."""

    call: Callable | None = None
    get: Callable | None = None
    set: Callable | None = None


class MemberOverrides:
    """One Browser's hooks: interface name -> member name -> :class:`MemberHook`."""

    def __init__(self, hooks: Mapping[str, Mapping[str, MemberHook]] | None = None):
        self._hooks = {name: dict(members) for name, members in (hooks or {}).items()}

    @classmethod
    def parse(cls, config, table) -> "MemberOverrides":
        """Validate *config* against the baseline *table* and freeze it.

        Refused here rather than misbehaving later: an unknown interface, an unknown
        member, a slot the member's kind cannot answer, a setter for a member the
        baseline declares read-only, a bound method and anything not callable.
        """
        if config is None:
            return cls()
        if not isinstance(config, Mapping):
            raise TypeError("interface_overrides must be a mapping of interface name to members")
        hooks: dict[str, dict[str, MemberHook]] = {}
        for interface, members in config.items():
            name = str(interface)
            if not isinstance(members, Mapping):
                raise TypeError(f"interface_overrides[{name!r}] must map member names to hooks")
            for member_name, value in members.items():
                public_name = str(member_name)
                kind = cls._kind(table, name, public_name)
                hooks.setdefault(name, {})[public_name] = cls._hook(
                    table, name, public_name, kind, value)
        return cls(hooks)

    @staticmethod
    def _kind(table, name, public_name) -> str:
        """The kind of member *name* answers for *public_name*, or a refusal."""
        if name not in table.interfaces:
            raise TypeError(f"interface {name!r} is not in the Chromium interface baseline")
        for kind in ("operation", "attribute"):
            if table.member(name, public_name, kind) is not None:
                return kind
        if table.member(name, public_name, "constant") is not None:
            raise TypeError(
                f"{name}.{public_name} is a constant; only methods and attributes can be overridden")
        raise TypeError(f"{name}.{public_name} is not a member of {name} in the interface baseline")

    @staticmethod
    def _hook(table, name, public_name, kind, value) -> MemberHook:
        """One member's hook, checked against what that member can answer."""
        slots = OVERRIDE_SLOTS[kind]
        named = isinstance(value, Mapping)
        chosen: dict[str, Any] = dict(value) if named else {slots[0]: value}
        if named:
            unknown = sorted(set(chosen) - set(slots))
            if unknown:
                raise TypeError(
                    f"{name}.{public_name} is a {kind} and answers {', '.join(slots)},"
                    f" but was given {', '.join(unknown)}")
        if not chosen:
            raise TypeError(f"{name}.{public_name} was given no hook")
        for slot, hook in chosen.items():
            if not callable(hook):
                raise TypeError(f"{name}.{public_name} {slot} hook is not callable")
            if inspect.ismethod(hook):
                raise TypeError(
                    f"{name}.{public_name} {slot} hook is a bound method, which would not"
                    " receive the JS receiver; pass a plain function")
        if "set" in chosen and getattr(table.member(name, public_name, kind), "setter_state", None) is False:
            raise TypeError(f"{name}.{public_name} is read-only in the interface baseline")
        return MemberHook(**chosen)

    @property
    def interfaces(self) -> frozenset[str]:
        """The interfaces at least one hook answers."""
        return frozenset(self._hooks)

    def for_member(self, interface, member_name) -> MemberHook | None:
        """The hook answering one member, or ``None`` when it is not overridden."""
        return self._hooks.get(interface, {}).get(member_name)

    def __bool__(self) -> bool:
        return bool(self._hooks)
