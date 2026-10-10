"""The family of errors a realm may be shown, and the boundaries that keep the rest in.

The binding owns the hierarchy: a class a realm may be shown inherits one of its error
types (``pyv8.JsError`` and the six beside it), and that inheritance is the whole
declaration -- there is no table to fill, no name to keep in step, and nothing to install
before a realm runs.  What this module keeps is the classification both directions ask
(:func:`is_declared`, :func:`is_host_failure`) and the boundary that keeps a host failure
out of a script's hands (:func:`host_boundary`).
"""
from contextlib import contextmanager

from .logging import logger


def is_declared(error) -> bool:
    """Whether *error* is one a realm may be shown: a member of the family."""
    import pyv8

    return isinstance(error, pyv8.JsError)


def is_host_failure(error) -> bool:
    """Whether *error* is the host's own failure rather than the page's doing.

    The family says what a realm may be shown, so a Python failure outside it is the
    host's -- the binding refuses those at the boundary for the same reason.  A
    JavaScript exception is the page's own, and so is anything that is not a Python
    failure at all: a thrown value, or the text of one.
    """
    import pyv8

    if not isinstance(error, BaseException) or isinstance(error, pyv8.JSException):
        return False
    return not isinstance(error, pyv8.JsError)


def script_on_the_stack() -> bool:
    """Whether a script is on the stack right now.

    pyv8 knows every realm built on a shared ``Context``.  A worker's realm is built
    on its own isolate, which is the one shape it does not answer for, so whoever
    serves such a realm answers for it instead.
    """
    import pyv8

    return pyv8.current_context() is not None


@contextmanager
def host_boundary(what, *, from_script=None):
    """Run host work a script asked for, without throwing host failures at it.

    ``close()`` and ``terminate()`` are the shape this exists for: a browser
    answers ``undefined`` and carries on, because a teardown's own difficulty is
    the host's, not something a script could have caused or handled.  The reason is
    logged where the diagnosis lives, so keeping it out of JavaScript does not keep
    it out of the run.

    JavaScript being on the stack *is* the boundary: the same call made by the host
    itself keeps raising, so a host is never told a teardown succeeded when it did
    not.  Anything that is not a host failure propagates either way.

    *from_script* answers whether a script asked for this, for a caller whose realm
    :func:`script_on_the_stack` cannot speak for; the default is to ask it.
    """
    try:
        yield
    except BaseException as error:
        answered = script_on_the_stack() if from_script is None else from_script
        if not is_host_failure(error) or not answered:
            raise
        logger.warning("{} could not finish: {}", what, error)


__all__ = ["is_declared", "is_host_failure", "script_on_the_stack", "host_boundary"]
