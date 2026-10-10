"""Single-owner execution loop for a V8 realm.

One thread owns the realm, so callers submit work and may wait for it; idle time
turns the realm's event loop, and an asyncio loop on the same thread sleeps until a
command, a delivery or a deadline wakes it.
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError

from .logging import logger

from . import timer_resolution
from .host_errors import RealmBusyError

#: Exceptions that must interrupt the process rather than be logged.
_CONTROL_EXCEPTIONS = (KeyboardInterrupt, SystemExit)

#: A queued command: the optional result Future, then callable, args and kwargs.
Command = tuple[Future[object] | None, Callable[..., object], tuple[object, ...], dict[str, object]]


def _env_flag(name, default=True):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def _default_call_timeout():
    """Seconds a synchronous ``call()`` may wait before raising.

    ``PYV8C_CALL_TIMEOUT`` overrides it; ``0`` or a negative value disables the
    bound.  The default sits far above every documented operation, so it only
    trips on a genuinely wedged realm.
    """
    raw = os.environ.get("PYV8C_CALL_TIMEOUT")
    if raw is None:
        return 60.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 60.0
    return None if value <= 0 else value


_UNSET = object()


def _report_late(future, name, submitted, timeout):
    """What a command that outlived its caller ended with.

    The caller is gone, so the answer has nowhere to go but the log: without this a
    network timeout or a failure inside a command is lost, which is what makes a
    realm that is merely waiting look like one that is silent.

    The caller's own budget is repeated here because the two durations read
    differently: ending just past it means the command only started near the
    deadline, so what took the time was whatever held the owner thread before it.
    """
    seconds = time.perf_counter() - submitted
    note = f" after it was submitted (its caller waited {timeout:g}s)"
    if future.cancelled():
        logger.warning(f"{name!r} was cancelled{note}")
        return
    outcome = future.exception()
    if outcome is None:
        logger.warning(f"{name!r} finished {seconds:.1f}s{note}")
        return
    logger.warning(f"{name!r} failed {seconds:.1f}s{note}: {type(outcome).__name__}: {outcome}")


class RealmExecutor:
    """Run all V8 and realm tasks on one dedicated thread."""

    def __init__(self, name="pyv8c-realm", *, auto_pump=None, default_timeout=_UNSET):
        self._commands: queue.SimpleQueue[Command] = queue.SimpleQueue()
        self._closed = threading.Event()
        # Serialises a submit against close, so a command cannot land in a stopped queue.
        self._submit_lock = threading.Lock()
        # Rouses an idle park; _timed also asks the loop to break a deadline wait.
        self._wake = threading.Event()
        self._timed = False
        self._owner_ident = None
        self._loop = None
        self._task_queue = None
        self.auto_pump = _env_flag("PYV8C_AUTO_PUMP") if auto_pump is None else bool(auto_pump)
        self.default_timeout = _default_call_timeout() if default_timeout is _UNSET else default_timeout
        self._error_sink = None
        self._rejections = None
        self._deadline_source = None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        # Start without waiting for the thread to be scheduled: the command queue
        # is unbounded, while blocking here deadlocks two realms sharing the GIL.
        self._thread.start()

    @property
    def closed(self):
        return self._closed.is_set()

    def is_owner_thread(self):
        return threading.get_ident() == self._owner_ident

    def attach_task_queue(self, task_queue):
        """Attach the queue after services and the Realm exist."""
        self._task_queue = task_queue
        # The queue is the realm's single reporting entry point for exceptions
        # raised by a fire-and-forget callback.
        if self._error_sink is None:
            self._error_sink = task_queue.report_error
        # The queue wakes the driver and sleeps it, instead of it polling.
        task_queue.set_wakeup(self.wakeup)
        task_queue.set_wait(self._sleep)
        self.wakeup()

    def attach_rejection_tracker(self, tracker):
        """Attach the realm's queue of unhandled-rejection candidates.

        V8 reports a rejection before the turn that caused it ends, and revokes
        the report if a handler appears, so ``unhandledrejection`` can only be
        published once the microtask checkpoint is over.
        """
        self._rejections = tracker

    def attach_deadline_source(self, source):
        """Attach the realm's deadline holder (its native Context).

        Arming it for one command is what lets a command already running be
        preempted, so a wedged realm raises :class:`pyv8.JavaScriptTerminated`
        instead of hanging the caller.
        """
        self._deadline_source = source

    def wakeup(self):
        """Rouse a sleeping owner thread: the event for an idle park, the loop for a deadline."""
        self._wake.set()
        if not self._timed:
            # The single-attribute read may be stale, and then the park's own re-check
            # sees the event instead: the flag only avoids the loop hop.
            return
        loop = self._loop
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            # The loop is closed: the thread is gone or on its way out.
            pass

    def call(self, callback, *args, timeout=_UNSET, **kwargs):
        """Run *callback* on the owner thread and return or raise its result.

        Raises :class:`RealmBusyError` when the owner thread does not pick the
        command up within *timeout*: a wedged realm surfaces as an error.
        """
        if self.is_owner_thread():
            return callback(*args, **kwargs)
        if timeout is _UNSET:
            timeout = self.default_timeout
        future: Future[object] = Future()
        with self._submit_lock:
            if self.closed:
                raise RuntimeError("realm executor is closed")
            self._commands.put((future, callback, args, kwargs))
        self.wakeup()
        name = getattr(callback, "__name__", callback)
        submitted = time.perf_counter()
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            if future.done():
                return future.result()
            # A queued command cancels safely; a running one was already preempted
            # by the realm's deadline, and what it ends with is logged, not awaited.
            cancelled = future.cancel()
            if not cancelled:
                future.add_done_callback(
                    lambda done: _report_late(done, name, submitted, timeout))
            raise RealmBusyError(
                f"realm executor did not complete {name!r} within {timeout}s"
                + (" (command cancelled)" if cancelled else
                   " (already running; its outcome is logged when it ends)")
            ) from None

    def post(self, callback, *args, **kwargs):
        """Asynchronously execute a callback on the owner thread."""
        with self._submit_lock:
            if self.closed:
                return False
            self._commands.put((None, callback, args, kwargs))
        self.wakeup()
        return True

    def _next_command(self):
        try:
            return self._commands.get_nowait()
        except queue.Empty:
            return None

    def _execute(self, command):
        future, callback, args, kwargs = command
        if future is not None and not future.set_running_or_notify_cancel():
            # The caller timed out while this command waited in the queue.
            return
        source = self._deadline_source
        budget = self.default_timeout
        armed = source is not None and bool(budget)
        if armed:
            try:
                source.set_deadline(budget)
            except BaseException:
                # The context may already be gone, which leaves nothing to preempt;
                # the command still has work to do, cleanup above all.
                armed = False
        try:
            result = callback(*args, **kwargs)
        except BaseException as exc:
            if future is None:
                # post() carries no Future, so the exception has nowhere to go.
                self._report_exception(exc)
                return
            future.set_exception(exc)
        else:
            if future is not None:
                future.set_result(result)
        finally:
            if armed:
                try:
                    source.clear_deadline()
                except BaseException:
                    # The command may have closed the context it borrowed.
                    pass

    def _report_exception(self, exc):
        """Handle an exception raised by a fire-and-forget command.

        Control exceptions are re-raised so a ``Ctrl-C`` reaching the realm
        interrupts the process instead of being written to a log.
        """
        if isinstance(exc, _CONTROL_EXCEPTIONS):
            raise exc
        reported = False
        sink = self._error_sink
        if sink is not None:
            try:
                sink(exc)
                reported = True
            except BaseException:  # reporting must not break the loop
                reported = False
        if not reported:
            logger.opt(exception=exc).error("realm command failed")

    def _next_deadline(self):
        """Seconds until the realm's next timer is due, if it has one."""
        task_queue = self._task_queue
        if task_queue is None or not self.auto_pump:
            return None
        return task_queue.next_deadline()

    def _run_turn(self):
        if not self.auto_pump:
            return False
        task_queue = self._task_queue
        if task_queue is None:
            return False
        return task_queue.run_turn()

    def _settle_rejections(self):
        """Publish the rejection candidates whose turn has ended."""
        tracker = self._rejections
        if tracker is None:
            return
        try:
            tracker.flush()
        except BaseException:  # reporting must not break the loop
            logger.debug("failed to report unhandled rejections", exc_info=True)

    def _has_work(self):
        """Whether anything the driver would run is already queued."""
        if not self._commands.empty():
            return True
        task_queue = self._task_queue
        if task_queue is None or not self.auto_pump:
            return False
        return task_queue.pending()

    def _sleep(self, seconds=None):
        """Sleep the owner thread until *seconds* elapse or :meth:`wakeup` is called.

        The loop's only callback stops it, so neither user code nor the pump ever
        runs inside ``run_forever``; that is what lets a command wait in here.
        """
        self._wake.clear()
        if self._has_work():
            # A delivery that raced the sleep decision is not slept out.
            return
        if seconds is None:
            # Nothing is due: park on the event instead of paying for the loop.
            self._wake.wait()
            return
        loop = self._loop
        self._timed = True
        handle = loop.call_later(seconds, loop.stop)
        try:
            if not self._wake.is_set():
                # Only a wait towards a deadline asks for the finer platform timer.
                with timer_resolution.hold():
                    loop.run_forever()
        finally:
            handle.cancel()
            self._timed = False

    def _run(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._owner_ident = threading.get_ident()
        try:
            self._drive()
        finally:
            loop.close()
            # A thread that went away for any reason must take its callers with it:
            # a closed executor fails fast instead of blocking them until timeout.
            self._closed.set()
            self._fail_pending()

    def _drive(self):
        while not self._closed.is_set():
            self._pump()

    def _pump(self):
        """Drain the commands, then run one turn, then sleep until something arrives."""
        self._drain_commands()
        if self._closed.is_set():
            return
        if self._run_turn():
            # A turn ends its task, which is where the specification puts the
            # rejection events for anything that task rejected.
            self._settle_rejections()
            return
        self._sleep(self._next_deadline())

    def _drain_commands(self):
        # Commands first: evaluate/goto/close must never queue behind page work.
        while not self._closed.is_set():
            command = self._next_command()
            if command is None:
                return
            try:
                self._execute(command)
            except _CONTROL_EXCEPTIONS:
                # One escaping would leave the realm marked open while its
                # owner thread dies, so close it first and then propagate.
                self._closed.set()
                raise
            # The command ran JavaScript and with it the microtask checkpoint,
            # so its rejections are due before the next one.
            self._settle_rejections()

    def _fail_pending(self):
        """Complete the callers still waiting on commands that will not run."""
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if isinstance(command, tuple) and command and command[0] is not None:
                if not command[0].done():
                    command[0].set_exception(RuntimeError("realm executor is closed"))

    def close(self, timeout=5):
        with self._submit_lock:
            if self.closed:
                return
            self._closed.set()
            self._fail_pending()
        self.wakeup()
        if not self.is_owner_thread():
            self._thread.join(timeout)
            if self._thread.is_alive():
                # A leaked owner thread keeps the isolate alive; say so.
                logger.warning(f"{self._thread.name} did not exit within {timeout}s")


__all__ = ["RealmExecutor"]
