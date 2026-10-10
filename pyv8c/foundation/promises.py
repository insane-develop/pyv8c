"""Turning engine work into promises, for whichever realm the work belongs to.

Three functions and one rule: a promise is built by ``v8::Promise::Resolver`` through
``pyv8.PromiseResolver`` -- never by evaluating JavaScript -- and a realm that has closed
answers instead of raising.  That rule used to live in a per-realm ``RealmScripts`` object,
which existed for the JavaScript helpers it cached; with the native resolver in place there is
nothing to cache, so the policy is stateless and the realm keeps only its own ``context``.

A closed realm is a real case, not a defensive one: replacing a document closes the old realm
while work queued by the old document is still delivered (see ``Page._renew_document_realm`` and
``navigation`` cancelling only timers).  Settling a promise nobody can read is not an error the
page should ever see, and the native resolver's own error -- "the realm this PromiseResolver
belongs to is closed" -- is kept for the calls that ask for one directly.
"""

from __future__ import annotations

import pyv8


def _no_op(*_args, **_kwargs):
    """Settle a promise nobody can observe: say nothing, change nothing."""


#: What the binding answers, in either of its two wordings, when the realm a settle
#: function was built for is gone: the resolver was asked after its realm closed, or the
#: context the resolver lives in is no longer active.  Both mean the same thing -- nobody
#: is left to read the answer -- which is what this module promises to meet with silence.
_GONE_REALM = (
    "belongs to is closed",
    "no active V8 isolate",
)


def _settler(settle):
    """*settle*, answered with silence once the realm it settles in is gone."""

    def settled(value):
        try:
            return settle(value)
        except RuntimeError as error:
            if not any(phrase in str(error) for phrase in _GONE_REALM):
                raise
        #: A realm can close before its promise does -- a worker that answered and
        #: exited, a page that navigated away.  The answer had nowhere to arrive.

    return settled


def make_promise(context):
    """A pending promise and the two callables that settle it, for *context*'s realm.

    The settle functions hold the native resolver alive until they are called or dropped, so a
    fetch, a timer or a worker reply can settle whenever it finishes.  A realm can close before
    its promise does, and the settle functions of one that did answer with silence -- that is
    the promise this module makes above, and what a task queue would otherwise turn into a
    host-failure warning about an isolate nobody was using any more.
    """
    if context is None:
        return None, _no_op, _no_op
    resolver = pyv8.PromiseResolver(context)
    return resolver.promise(), _settler(resolver.resolve), _settler(resolver.reject)


def promise_resolve(context, value):
    """*value* as a resolved promise, or *value* itself once the realm is gone."""
    if context is None:
        return value
    resolver = pyv8.PromiseResolver(context)
    resolver.resolve(value)
    return resolver.promise()


def enqueue_microtask(context, callback):
    """Queue *callback* on the realm's own microtask queue, or drop it if the realm is gone."""
    if context is None:
        return False
    return context.enqueue_microtask(callback)


__all__ = ["make_promise", "promise_resolve", "enqueue_microtask"]
