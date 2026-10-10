"""One shared sink per realm; browser classes carry no trace methods."""

from ..foundation.logging import logger
from pyv8.trace import format_trace


def log_trace(event, receiver, name, arguments, value, error, is_construct, ctx_type):
    message = format_trace(event, receiver, name, arguments, value, error, is_construct, ctx_type)
    if message is not None:
        logger.info(message)


__all__ = ["log_trace"]
