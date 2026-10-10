"""The shared logger, imported on first use.

loguru costs ~75 ms to import and drags asyncio and ssl in, so a browser that never
logs should not pay that at startup; ``logger`` stands in until the first call.
"""

from __future__ import annotations

__all__ = ["logger"]


class _LazyLogger:
    """Forwards to ``loguru.logger``, importing it on first use."""

    def __getattr__(self, name):
        from loguru import logger

        return getattr(logger, name)


logger = _LazyLogger()
