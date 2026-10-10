"""Browser host adapters for the shared pyv8 DevTools service."""

from pyv8.devtools import DevToolsHandle, DevToolsOptions, create_service
from pyv8.devtools.scheduler import Host

__all__ = ["DevToolsHandle", "DevToolsOptions"]


class BrowserDebugging:
    def __init__(self, context, page, options):
        self.context = context
        self.targets = {}
        host = self.page_host(page)
        self.handle = create_service(host, options, on_close=self._did_close)
        self.router = self.handle._router
        self.targets[page] = self.handle._root
        page._devtools_target = self.handle._root
        self._register_descendants(page)

    @staticmethod
    def page_host(page, kind="page"):
        return Host(page.context, page.realm.executor, lambda: page.url, page.close, kind)

    def _register_descendants(self, page):
        for child in tuple(page._owned.frames):
            self.register_page(child, kind="iframe", parent=page, started=True)
            self._register_descendants(child)
        for worker in tuple(page._owned.workers):
            runtime = worker._runtime
            if runtime is not None:
                runtime.executor.post(self.register_worker, runtime, self.targets[page], started=True)

    def register_page(self, page, kind="page", parent=None, *, started=False):
        if page in self.targets:
            return self.targets[page]
        parent_target = self.targets.get(parent)
        if parent_target is None:
            return None
        target = self.router.register(self.page_host(page, kind), parent_target, started=started)
        self.targets[page] = target
        page._devtools_target = target
        return target

    def register_worker(self, runtime, parent, *, started=False):
        if runtime in self.targets:
            return self.targets[runtime]
        if parent is None or parent.closed or runtime._context is None:
            return None
        target = self.router.register(Host(
            runtime._context, runtime.executor, lambda: runtime.script_url,
            runtime.stop, "worker",
        ), parent, started=started)
        self.targets[runtime] = target
        runtime.debug_target = target
        runtime._devtools = self
        return target

    def unregister(self, realm):
        target = self.targets.pop(realm, None)
        if target is None:
            return
        self.router.unregister(target)
        if target.host.executor.is_owner_thread():
            target.mailbox.drain(target)

    def target_info_changed(self, page):
        target = self.targets.get(page)
        if target is not None and not target.closed:
            self.router.event("Target.targetInfoChanged", {"targetInfo": target.descriptor()})

    def _did_close(self):
        if self.context._devtools is self:
            self.context._devtools = None
        self.targets.clear()

    def close(self):
        self.handle.close()


def start_devtools(context, page, *, options=None):
    adapter = BrowserDebugging(context, page, options)
    context._devtools = adapter
    return adapter.handle
