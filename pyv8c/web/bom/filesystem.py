"""Legacy Chromium FileSystem API used by ``webkitRequestFileSystem``.

Deprecated in Chromium but still observable by older pages; the old callback
surface is kept while a per-page temporary directory backs it.  Only ``File`` is
reachable as a global, so this is an Optional Feature.
"""
import os
import shutil
import tempfile
import time
from pathlib import Path

import pyv8

from ...engine.features import FeatureImplementation
from ...idl import interface
from ...foundation.arguments import (
    MISSING,
    callback_value,
    dictionary,
    dom_string,
    integer,
    is_nullish,
    missing_error,
)
from ..page_errors import PageTypeError
from ...foundation.realm import post_or_call
from ..dom.events import Event, EventTarget, _install_handler_slots
from ..fetch.blob import Blob
from ..fetch.events import ProgressEvent


#: The asynchronous entry points below all deliver their callback the same way.
_post = post_or_call


def _callback(value, name, position, optional=False):
    if value is pyv8.Undefined or value is None or value is pyv8.Null:
        if optional:
            return None
        raise PageTypeError(f"Failed to execute '{name}' on 'Entry': parameter {position} is not a function.")
    return callback_value(value)


@interface("File", "Blob")
class File(Blob):
    """The ``File`` global and the ``FileEntry.file()`` view; ``size``/``type``
    come from :class:`Blob`.
    """

    __idl_overrides__ = {
        "size": {
            "side_effect": 1
        },
        "type": {
            "side_effect": 1
        }
    }

    def __init__(self, file_bits=pyv8.Undefined, file_name=pyv8.Undefined, options=None, *_extra, **kw):
        if file_bits is pyv8.Undefined:
            raise missing_error("File", "constructor", 2, 0, constructor=True)
        if file_name is pyv8.Undefined:
            raise missing_error("File", "constructor", 2, 1, constructor=True)
        init = {} if is_nullish(options) else dictionary(options, name="FilePropertyBag")
        super().__init__(file_bits, init, *_extra, **kw)
        self._name = dom_string(file_name)
        last_modified = init.get("lastModified")
        self._last_modified = (int(time.time() * 1000) if last_modified is None
                               else int(integer(last_modified, bits=64)))

    def get_name(self): return self._name
    def get_lastModified(self): return self._last_modified


def _file_from_path(path):
    """The ``FileEntry.file()`` view: parts, name and timestamp come from disk."""
    path = Path(path)
    if not path.exists():
        return File([b""], path.name)
    return File([path.read_bytes()], path.name,
                {"lastModified": int(path.stat().st_mtime * 1000)})


#: FileWriter's six on* handlers, which EventTarget does not declare.
_WRITER_HANDLERS = ("onwritestart", "onprogress", "onwrite", "onabort", "onerror", "onwriteend")


@interface("FileWriter", "EventTarget")
class FileWriter(EventTarget):
    def __init__(self, path, services=None, **_):
        super().__init__(services=services)
        self._path, self._position, self._services = Path(path), 0, services
    def get_position(self): return self._position
    def get_length(self): return self._path.stat().st_size if self._path.exists() else 0
    def fn_seek(self, offset=MISSING, *_extra):
        if offset is MISSING: raise missing_error("FileWriter", "seek", 1, 0)
        self._position = max(0, integer(offset, signed=True))
    def fn_truncate(self, size=MISSING, *_extra):
        if size is MISSING: raise missing_error("FileWriter", "truncate", 1, 0)
        size = max(0, integer(size, bits=64))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("r+b" if self._path.exists() else "wb") as fh: fh.truncate(size)
        self._position = min(self._position, size)
    def fn_write(self, data=MISSING, *_extra):
        if data is MISSING: raise missing_error("FileWriter", "write", 1, 0)
        raw = dom_string(data).encode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        mode = "r+b" if self._path.exists() else "wb"
        with self._path.open(mode) as fh:
            fh.seek(self._position); fh.write(raw); self._position += len(raw)
        self._report_write(len(raw))
    def _report_write(self, length):
        """Queue ``writestart``/``write``/``progress``/``writeend`` behind the
        synchronous write; this writer has no async failure and no ``abort()``.
        """
        events = (
            Event("writestart", {"isTrusted": True}),
            Event("write", {"isTrusted": True}),
            ProgressEvent("progress", {"isTrusted": True, "lengthComputable": True,
                                       "loaded": length, "total": length}),
            Event("writeend", {"isTrusted": True}),
        )
        for event in events:
            _post(self._services, self.fn_dispatchEvent, event)


@interface("Entry", None)
class Entry:
    def __init__(self, fs, path, **_): self._fs, self._path = fs, Path(path)
    def get_isFile(self): return self._path.is_file()
    def get_isDirectory(self): return self._path.is_dir()
    def get_name(self): return "" if self._path == self._fs._root else self._path.name
    def get_fullPath(self):
        if self._path == self._fs._root: return "/"
        return "/" + str(self._path.relative_to(self._fs._root)).replace(os.sep, "/")
    def get_filesystem(self): return self._fs
    def fn_toURL(self, *_extra): return "filesystem:" + self._fs._origin + "/temporary" + self.get_fullPath()
    def fn_getMetadata(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("Entry", "getMetadata", 1, 0)
        _post(self._fs._services, _callback(success, "getMetadata", 1), {
            "modificationTime": self._path.stat().st_mtime if self._path.exists() else 0,
            "size": self._path.stat().st_size if self._path.is_file() else 0,
        })
    def fn_getParent(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("Entry", "getParent", 1, 0)
        _post(self._fs._services, _callback(success, "getParent", 1), Entry(self._fs, self._path.parent))
    def fn_remove(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("Entry", "remove", 1, 0)
        cb = _callback(success, "remove", 1)
        try:
            if self._path.is_dir(): self._path.rmdir()
            elif self._path.exists(): self._path.unlink()
            _post(self._fs._services, cb)
        except Exception as exc:  # the failure is reported through the callback
            _post(self._fs._services, _callback(error, "remove", 2, True), exc)


@interface("FileEntry", "Entry")
class FileEntry(Entry):
    def fn_file(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("FileEntry", "file", 1, 0)
        _post(self._fs._services, _callback(success, "file", 1), _file_from_path(self._path))
    def fn_createWriter(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("FileEntry", "createWriter", 1, 0)
        _post(self._fs._services, _callback(success, "createWriter", 1), FileWriter(self._path, self._fs._services))


@interface("DirectoryReader", None)
class DirectoryReader:
    def __init__(self, fs, path, **_): self._fs, self._path, self._done = fs, Path(path), False
    def fn_readEntries(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("DirectoryReader", "readEntries", 1, 0)
        result = [] if self._done else [self._fs._entry(p) for p in sorted(self._path.iterdir()) if p.exists()]
        self._done = True
        _post(self._fs._services, _callback(success, "readEntries", 1), result)


@interface("DirectoryEntry", "Entry")
class DirectoryEntry(Entry):
    def fn_createReader(self, *_extra): return DirectoryReader(self._fs, self._path)
    def _child(self, name):
        name = dom_string(name)
        if not name or "/" in str(name) or str(name) in (".", ".."): raise PageTypeError("Invalid path")
        return self._path / str(name)
    def fn_getFile(self, path=MISSING, options=None, success=MISSING, error=None, *_extra):
        if path is MISSING: raise missing_error("DirectoryEntry", "getFile", 1, 0)
        if success is MISSING and callable(options): success, options = options, None
        target = self._child(path); target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists(): target.touch()
        _post(self._fs._services, _callback(success, "getFile", 3), FileEntry(self._fs, target))
    def fn_getDirectory(self, path=MISSING, options=None, success=MISSING, error=None, *_extra):
        if path is MISSING: raise missing_error("DirectoryEntry", "getDirectory", 1, 0)
        if success is MISSING and callable(options): success, options = options, None
        target = self._child(path); target.mkdir(parents=True, exist_ok=True)
        _post(self._fs._services, _callback(success, "getDirectory", 3), DirectoryEntry(self._fs, target))
    def fn_removeRecursively(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("DirectoryEntry", "removeRecursively", 1, 0)
        cb = _callback(success, "removeRecursively", 1)
        try: shutil.rmtree(self._path); _post(self._fs._services, cb)
        except Exception as exc:  # the failure is reported through the callback
            _post(self._fs._services, _callback(error, "removeRecursively", 2, True), exc)


@interface("DOMFileSystem", None)
class DOMFileSystem:
    def __init__(self, name, root, services=None, origin="null", **_):
        self._name, self._root, self._services, self._origin = name, Path(root), services, origin
        self._root.mkdir(parents=True, exist_ok=True)
    def get_name(self): return self._name
    def get_root(self): return DirectoryEntry(self, self._root)
    def _entry(self, path): return DirectoryEntry(self, path) if Path(path).is_dir() else FileEntry(self, path)

FileSystem = DOMFileSystem


def request_file_system(window, kind, size, success, error):
    kind = integer(kind, bits=16)
    if kind not in (0, 1): raise PageTypeError("Invalid file system type")
    size = integer(size, bits=64)
    if size < 0: raise PageTypeError("Invalid file system size")
    success = callback_value(success)
    if error is not pyv8.Undefined and error is not None and error is not pyv8.Null: error = callback_value(error)
    root = tempfile.mkdtemp(prefix="pyv8-fs-")
    origin = getattr(getattr(window._services, "realm_services", None), "url", "null")
    fs = FileSystem("temporary" if kind == 0 else "persistent", root, services=window._services, origin=origin)
    _post(window._services, success, fs)


#: Accessors only: the baseline says which of these FileWriter publishes.
_install_handler_slots(FileWriter, _WRITER_HANDLERS)


__all__ = ["FileSystem", "Entry", "DirectoryEntry", "FileEntry", "DirectoryReader", "FileWriter", "File"]

FEATURE = FeatureImplementation({
    "DOMFileSystem": DOMFileSystem,
    "Entry": Entry,
    "DirectoryEntry": DirectoryEntry,
    "FileEntry": FileEntry,
    "DirectoryReader": DirectoryReader,
    "FileWriter": FileWriter,
    "File": File,
})
