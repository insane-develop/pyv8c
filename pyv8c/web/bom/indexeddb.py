"""``window.indexedDB`` -- Indexed Database API (W3C IndexedDB 3.0).

Data lives in a process-wide in-memory registry keyed by origin, so pages on
the same origin share databases the way a real browser does.

Known deviations: values are cloned with the runtime's ``structured_clone``
(less faithful than Blink's StructuredSerializeForStorage); ``Date`` keys
surface as numeric timestamps; a blocked upgrade retries for
``_BLOCKED_WAIT_SECONDS`` and then proceeds rather than blocking forever.
"""
import threading
from bisect import bisect_left, bisect_right
from datetime import datetime

import pyv8
from pyv8.flag import FlagCrossOriginCheck, FlagLocation, FlagReceiverCheck

from ...engine.features import FeatureImplementation
from ...idl import interface
from ...foundation.arguments import (
    MISSING,
    boolean,
    dictionary,
    dom_string,
    enum_value,
    event_handler,
    integer,
    is_nullish,
    materialize,
    missing_error,
    number,
    sequence,
)
from ...foundation.arguments import IdlArgumentError
from ..page_errors import PageTypeError, dom_exception
from ...foundation.error_policy import is_declared
from ...foundation.logging import logger
from ...foundation.realm import post_or_call
from ..dom.collections import DOMStringList
from ..dom.events import Event, EventTarget
from ..fetch.clone import rebuild_clone, structured_clone, unread

__all__ = [
    "IDBFactory", "IDBDatabase", "IDBTransaction", "IDBObjectStore", "IDBIndex",
    "IDBCursor", "IDBCursorWithValue", "IDBKeyRange", "IDBRequest",
    "IDBOpenDBRequest", "IDBVersionChangeEvent", "IDBRecord",
]


def _fail(name, message):
    return dom_exception(name, message)


def _data_error(message="The data provided does not meet requirements."):
    return _fail("DataError", message)


def _constraint_error(message="A mutation operation failed because a constraint was not satisfied."):
    return _fail("ConstraintError", message)


def _invalid_state_error(message="The object is in an invalid state."):
    return _fail("InvalidStateError", message)


def _transaction_inactive_error(message="The transaction is not active."):
    return _fail("TransactionInactiveError", message)


def _read_only_error():
    return _fail("ReadOnlyError", "The mutating operation was attempted in a read-only transaction.")


def _not_found_error(message="The requested database object could not be found."):
    return _fail("NotFoundError", message)


def _invalid_access_error(message="An invalid operation was performed on the object."):
    return _fail("InvalidAccessError", message)


def _version_error():
    return _fail("VersionError", "The requested version is lower than the existing version.")


def _syntax_error(message="The keyPath argument contains an invalid key path."):
    return _fail("SyntaxError", message)


def _abort_error():
    return _fail("AbortError", "The transaction was aborted.")

_NUMBER, _DATE, _STRING, _BINARY, _ARRAY = 0, 1, 2, 3, 4
_TYPE_ORDER = {_NUMBER: 0, _DATE: 1, _STRING: 2, _BINARY: 3, _ARRAY: 4}

_MAX_KEY_GENERATOR = 2 ** 53


def _string_or_strings(value):
    if isinstance(value, (list, tuple)):
        return sequence(value, dom_string)
    if isinstance(value, pyv8.JSObject):
        iterator = value._iterator_method()
        if not is_nullish(iterator):
            return sequence(value, dom_string, iterator_method=iterator)
    return dom_string(value)


def _to_key(value):
    """Convert a JS value to ``(kind, value)``; a bad key raises ``DataError``
    as it does in Chrome.
    """
    value = materialize(value)
    if is_nullish(value):
        raise _data_error("The parameter is not a valid key.")
    if isinstance(value, bool):
        raise _data_error("A boolean is not a valid key.")
    if isinstance(value, int):
        return (_NUMBER, float(value))
    if isinstance(value, float):
        if value != value:
            raise _data_error("NaN is not a valid key.")
        return (_NUMBER, value)
    if isinstance(value, str):
        return (_STRING, value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return (_BINARY, bytes(value))
    if isinstance(value, datetime):
        return (_DATE, value.timestamp() * 1000.0)
    if type(value).__name__ in ("TypedArray", "JSTypeArray"):
        try:
            return (_BINARY, bytes(int(value[index]) & 255 for index in range(int(value.length))))
        except Exception as exc:
            raise _data_error("The binary key could not be read.") from exc
    if isinstance(value, (list, tuple)):
        return (_ARRAY, tuple(_to_key(item) for item in value))
    raise _data_error(f"A value of type {type(value).__name__!r} is not a valid key.")


def _cmp(left, right):
    """Compare two keys the way IndexedDB does; returns ``-1``, ``0`` or ``1``."""
    left_kind, right_kind = left[0], right[0]
    if left_kind != right_kind:
        return -1 if _TYPE_ORDER[left_kind] < _TYPE_ORDER[right_kind] else 1
    if left_kind == _ARRAY:
        for left_item, right_item in zip(left[1], right[1]):
            result = _cmp(left_item, right_item)
            if result:
                return result
        left_size, right_size = len(left[1]), len(right[1])
        return (left_size > right_size) - (left_size < right_size)
    left_value, right_value = left[1], right[1]
    return (left_value > right_value) - (left_value < right_value)


def _key_to_value(key):
    """Turn a stored key back into the value scripts observe."""
    kind, value = key
    if kind == _ARRAY:
        return [_key_to_value(item) for item in value]
    return value


def _key_sort_key(key):
    """Total-order helper mirroring :func:`_cmp` for ``sorted``."""
    def encode(item):
        kind, value = item
        if kind == _ARRAY:
            return (_TYPE_ORDER[kind], tuple(encode(part) for part in value))
        return (_TYPE_ORDER[kind], value)
    return encode(key)

_MISSING = object()


def _valid_single_key_path(path):
    if path == "":
        return True
    return all(part.isidentifier() for part in path.split("."))


def _valid_key_path(key_path):
    if isinstance(key_path, (list, tuple)):
        return len(key_path) > 0 and all(
            isinstance(part, str) and _valid_single_key_path(part) for part in key_path
        )
    if isinstance(key_path, str):
        return _valid_single_key_path(key_path)
    return False


def _get_path(value, path):
    if path == "":
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _extract_key(value, key_path):
    """Return a key, ``None`` when the path is absent, or raise ``DataError``."""
    if isinstance(key_path, (list, tuple)):
        parts = []
        for part in key_path:
            found = _get_path(value, part)
            if found is _MISSING:
                return None
            parts.append(found)
        return _to_key(parts)
    found = _get_path(value, key_path)
    if found is _MISSING:
        return None
    return _to_key(found)


def _inject_key(value, key_path, key):
    """Write ``key`` into ``value`` at ``key_path``; return False when impossible."""
    if not isinstance(value, dict):
        return False
    if isinstance(key_path, (list, tuple)):
        return all(
            _inject_key(value, part, key[1][index]) for index, part in enumerate(key_path)
        )
    if key_path == "":
        return False
    parts = key_path.split(".")
    current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            if part in current:
                return False
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = _key_to_value(key)
    return True


class _KeyRange:
    """Internal range used for filtering; mirrors the IDBKeyRange interface."""

    __slots__ = ("lower", "upper", "lower_open", "upper_open", "_lower_sort", "_upper_sort")

    def __init__(self, lower=None, upper=None, lower_open=False, upper_open=False):
        self.lower = lower
        self.upper = upper
        self.lower_open = lower_open
        self.upper_open = upper_open
        self._lower_sort = _key_sort_key(lower) if lower is not None else None
        self._upper_sort = _key_sort_key(upper) if upper is not None else None

    def contains(self, key):
        sort_key = _key_sort_key(key)
        if self._lower_sort is not None:
            if self._lower_sort > sort_key or (self._lower_sort == sort_key and self.lower_open):
                return False
        if self._upper_sort is not None:
            if self._upper_sort < sort_key or (self._upper_sort == sort_key and self.upper_open):
                return False
        return True


def _to_key_range(value, null_disallowed=False):
    """Convert a query argument (key, IDBKeyRange or null) to a :class:`_KeyRange`."""
    if isinstance(value, _KeyRange):
        return value
    if isinstance(value, IDBKeyRange):
        return value._range
    if is_nullish(value):
        if null_disallowed:
            raise _data_error("No key or key range was specified.")
        return _KeyRange()
    key = _to_key(value)
    return _KeyRange(lower=key, upper=key)


def _unpack_query(query, count):
    """Accept either ``(query, count)`` or an ``IDBGetAllOptions`` dictionary."""
    direction = None
    if isinstance(query, dict):
        options = query
        query = options.get("query", None)
        count = options.get("count", count)
        direction = options.get("direction", None)
    return query, count, direction


class _IndexModel:
    __slots__ = ("name", "key_path", "unique", "multi_entry", "entries", "_sort_keys", "_unique_keys")

    def __init__(self, name, key_path, unique=False, multi_entry=False):
        self.name = name
        self.key_path = key_path
        self.unique = unique
        self.multi_entry = multi_entry
        self.entries = []  # [(index_key, primary_key)]
        self._sort_keys = []
        self._unique_keys = set()

    def keys_for(self, value):
        try:
            extracted = _extract_key(value, self.key_path)
        except Exception:
            return []
        if extracted is None:
            return []
        if self.multi_entry and extracted[0] == _ARRAY:
            return list(extracted[1])
        return [extracted]

    def add(self, value, primary_key):
        keys = self.keys_for(value)
        if not keys:
            return
        if self.unique:
            seen = set()
            for key in keys:
                marker = _key_sort_key(key)
                if marker in seen or marker in self._unique_keys:
                    raise _constraint_error(
                        "Unable to add key to index '%s': the key does not satisfy the "
                        "uniqueness requirements." % self.name
                    )
                seen.add(marker)
        for key in keys:
            entry = (key, primary_key)
            sort_key = (_key_sort_key(key), _key_sort_key(primary_key))
            position = bisect_right(self._sort_keys, sort_key)
            self.entries.insert(position, entry)
            self._sort_keys.insert(position, sort_key)
            if self.unique:
                self._unique_keys.add(sort_key[0])

    def remove(self, value, primary_key):
        keys = list(self.keys_for(value))
        if not keys:
            return
        wanted = {_key_sort_key(key) for key in keys}
        kept = []
        kept_sort = []
        for entry, sort_key in zip(self.entries, self._sort_keys):
            if entry[1] == primary_key and sort_key[0] in wanted:
                continue
            kept.append(entry)
            kept_sort.append(sort_key)
        self.entries, self._sort_keys = kept, kept_sort
        if self.unique:
            self._unique_keys = {sort_key[0] for sort_key in kept_sort}

    def clear(self):
        self.entries = []
        self._sort_keys = []
        self._unique_keys.clear()


class _StoreModel:
    __slots__ = ("name", "key_path", "auto_increment", "generator", "indexes", "records", "_sort_keys", "_record_map")

    def __init__(self, name, key_path=None, auto_increment=False):
        self.name = name
        self.key_path = key_path
        self.auto_increment = auto_increment
        self.generator = 1
        self.indexes = {}
        self.records = []  # [(key, value)] kept sorted by key
        self._sort_keys = []
        self._record_map = {}

    def find(self, key):
        return self._record_map.get(key)

    def _sorted_insert(self, key, value):
        entry = (key, value)
        sort_key = _key_sort_key(key)
        position = bisect_left(self._sort_keys, sort_key)
        self.records.insert(position, entry)
        self._sort_keys.insert(position, sort_key)
        self._record_map[key] = entry

    def store(self, key, value, no_overwrite):
        existing = self.find(key)
        if existing is not None:
            if no_overwrite:
                raise _constraint_error("A record with the given key already exists.")
            for index in self.indexes.values():
                index.remove(existing[1], key)
            position = self._record_map.pop(key, None)
            if position is not None:
                index = self.records.index(position)
                self.records.pop(index)
                self._sort_keys.pop(index)
        for index in self.indexes.values():
            index.add(value, key)
        self._sorted_insert(key, value)
        return key

    def delete_range(self, key_range):
        if key_range is None:
            removed, self.records = self.records, []
            self._sort_keys = []
            self._record_map.clear()
        else:
            removed, kept, kept_sort = [], [], []
            for entry, sort_key in zip(self.records, self._sort_keys):
                if key_range.contains(entry[0]):
                    removed.append(entry)
                    self._record_map.pop(entry[0], None)
                else:
                    kept.append(entry)
                    kept_sort.append(sort_key)
            self.records, self._sort_keys = kept, kept_sort
        for key, value in removed:
            for index in self.indexes.values():
                index.remove(value, key)

    def clear(self):
        self.records = []
        self._sort_keys = []
        self._record_map.clear()
        for index in self.indexes.values():
            index.clear()

    def _snapshot(self):
        """Deep snapshot of the store content for transaction rollback.

        Values are re-cloned so a snapshot is immune to later in-place
        mutation of the live objects (scripts can mutate values they read
        back out of the store).  Keys and index entries are immutable
        tuples/scalars and are copied by list.
        """
        return (
            self.name,
            self.key_path,
            self.auto_increment,
            self.generator,
            [(key, _store_clone(value)) for key, value in self.records],
            {
                name: (
                    index.key_path,
                    index.unique,
                    index.multi_entry,
                    list(index.entries),
                )
                for name, index in self.indexes.items()
            },
        )

    def _restore(self, snapshot):
        """Put a store back into the state captured by :meth:`_snapshot`."""
        (name, key_path, auto_increment, generator, records, index_snaps) = snapshot
        self.name = name
        self.key_path = key_path
        self.auto_increment = auto_increment
        self.generator = generator
        self.records = records
        self._sort_keys = [_key_sort_key(key) for key, _value in records]
        self._record_map = {entry[0]: entry for entry in records}
        indexes = {}
        for index_name, (index_path, unique, multi_entry, entries) in index_snaps.items():
            index = self.indexes.get(index_name)
            if index is None:
                index = _IndexModel(index_name, index_path, unique, multi_entry)
            index.entries = entries
            index._sort_keys = [(_key_sort_key(key), _key_sort_key(primary)) for key, primary in entries]
            index._unique_keys = {_key_sort_key(key) for key, _primary in entries} if index.unique else set()
            indexes[index_name] = index
        self.indexes = indexes


class _DatabaseModel:
    __slots__ = ("name", "version", "stores", "connections")

    def __init__(self, name):
        self.name = name
        self.version = 0
        self.stores = {}
        self.connections = []

_LOCK = threading.RLock()
_BLOCKED_WAIT_SECONDS = 5.0
_BLOCKED_POLL_SECONDS = 0.05


def _origin_key(services):
    realm = getattr(services, "realm_services", None)
    origin = getattr(realm, "origin", None)
    if isinstance(origin, tuple):
        try:
            hash(origin)
            return origin
        except TypeError:
            pass
    return ("opaque", "default")


def _databases(services):
    """The origin-scoped databases of the context the realm belongs to."""
    return services.context_services._databases.setdefault(_origin_key(services), {})


def _handed(value):
    """A stored value as the realm reading it gets it: its own copy of any binary."""
    context = pyv8.current_context()
    return rebuild_clone(value) if context is None else rebuild_clone(value, context)


def _store_clone(value):
    #: The caller's own context, so what goes into the store carries its bytes: an
    #: ArrayBuffer has nothing enumerable on it, and used to be stored as ``{}``.
    return structured_clone(value, context=pyv8.current_context())


def _maybe_update_generator(store, key):
    if key[0] != _NUMBER:
        return
    value = float(int(min(key[1], float(_MAX_KEY_GENERATOR))))
    if value >= store.generator:
        store.generator = int(value) + 1


def _limit(values, count):
    if count is None:
        return values
    return values[: max(0, int(count))]


@interface('IDBRecord', None)
class IDBRecord:
    def __init__(self, key=None, primary_key=None, value=None, **_):
        self._key = key
        self._primary_key = primary_key
        self._value = value

    def get_key(self):
        return _key_to_value(self._key) if self._key is not None else pyv8.Undefined

    def get_primaryKey(self):
        return (
            _key_to_value(self._primary_key)
            if self._primary_key is not None
            else pyv8.Undefined
        )

    def get_value(self):
        return _handed(self._value) if self._value is not None else pyv8.Undefined


@interface('IDBVersionChangeEvent', 'Event')
class IDBVersionChangeEvent(Event):
    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        if event_type is MISSING:
            raise missing_error('IDBVersionChangeEvent', 'constructor', 1, 0, constructor=True)
        if event_type is not pyv8.Undefined:
            event_type = dom_string(event_type)
        if options is pyv8.Undefined:
            options = None
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.oldVersion = int(options.get("oldVersion", 0) or 0)
        new_version = options.get("newVersion", None)
        self.newVersion = None if is_nullish(new_version) else int(new_version)

    def get_oldVersion(self):
        return self.oldVersion

    def get_newVersion(self):
        return pyv8.Null if self.newVersion is None else self.newVersion


class _RequestMixin:
    def _init_request(self, source=None, transaction=None):
        self._source = source
        self._transaction = transaction
        self._done = False
        self._result = pyv8.Undefined
        self._result_handed = unread
        self._error = None
        self.readyState = "pending"
        self._blocked_fired = False
        # Open requests expose ``result`` before ``upgradeneeded`` fires, as in
        # Blink, while ``readyState`` stays ``"pending"`` until ``success``.
        self._result_ready = False

    def _dispatch(self, event_type, options=None):
        return self.fn_dispatchEvent(Event(event_type, options or {"isTrusted": True}))

    def _dispatch_version_change(self, event_type, old_version, new_version):
        event = IDBVersionChangeEvent(
            event_type, {"oldVersion": old_version, "newVersion": new_version, "isTrusted": True}
        )
        return self.fn_dispatchEvent(event)

    def _mark_done(self):
        self._done = True
        self.readyState = "done"

    def _succeed(self, result):
        self._result = result
        self._error = None
        self._mark_done()
        return self._dispatch("success")

    def _succeed_version_change(self, old_version, new_version):
        self._result = pyv8.Undefined
        self._error = None
        self._mark_done()
        return self._dispatch_version_change("success", old_version, new_version)

    def _fail_with(self, error):
        self._error = error
        self._result = pyv8.Undefined
        self._mark_done()
        # Error events bubble and are cancelable: preventDefault() stops the
        # surrounding transaction from aborting.
        return self._dispatch(
            "error", {"bubbles": True, "cancelable": True, "isTrusted": True}
        )

    def _reopen(self):
        """Clear the done flag so a cursor can reuse the same request."""
        self._done = False
        self._result_ready = False
        self._result_handed = unread
        self.readyState = "pending"


@interface('IDBRequest', 'EventTarget')
class IDBRequest(EventTarget, _RequestMixin):
    def __init__(self, services=None, source=None, transaction=None, **kw):
        super().__init__(services=services, **kw)
        self._init_request(source=source, transaction=transaction)

    def get_result(self):
        if not self._done and not self._result_ready:
            raise _invalid_state_error("The request has not finished.")
        #: A stored value came out of a clone, so the realm reading it is handed its own
        #: copy of the binary in it; a host reading it through Python gets the bytes.
        context = pyv8.current_context()
        if context is None:
            return rebuild_clone(self._result)
        if self._result_handed is unread:
            self._result_handed = rebuild_clone(self._result, context)
        return self._result_handed

    def get_error(self):
        if not self._done:
            raise _invalid_state_error("The request has not finished.")
        return self._error if self._error is not None else pyv8.Null

    def get_source(self):
        return self._source if self._source is not None else pyv8.Null

    def get_transaction(self):
        return self._transaction if self._transaction is not None else pyv8.Null

    def get_readyState(self):
        return self.readyState

    def get_onsuccess(self):
        return self._get_handler("onsuccess")

    def set_onsuccess(self, value):
        value = event_handler(value)
        self._set_handler("onsuccess", value)

    def get_onerror(self):
        return self._get_handler("onerror")

    def set_onerror(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler("onerror", value)


@interface('IDBOpenDBRequest', 'IDBRequest')
class IDBOpenDBRequest(IDBRequest):
    def get_onblocked(self):
        return self._get_handler("onblocked")

    def set_onblocked(self, value):
        value = event_handler(value)
        self._set_handler("onblocked", value)

    def get_onupgradeneeded(self):
        return self._get_handler("onupgradeneeded")

    def set_onupgradeneeded(self, value):
        value = event_handler(value)
        self._set_handler("onupgradeneeded", value)

_STATIC = dict(
    location=FlagLocation.kInterface,
    receiver_check=FlagReceiverCheck.kDoNotCheck,
    cross_origin_check=FlagCrossOriginCheck.kDoNotCheck,
)


@interface('IDBKeyRange', None)
class IDBKeyRange:
    def __init__(self, range=None, **_):
        self._range = range if range is not None else _KeyRange()

    @staticmethod
    def fn_only(_realm, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('IDBKeyRange', 'only', 1, 0, constructor=False)
        key = _to_key(value)
        return IDBKeyRange(_KeyRange(lower=key, upper=key))

    @staticmethod
    def fn_lowerBound(_realm, lower=MISSING, open=False, *_extra):
        if lower is MISSING:
            raise missing_error('IDBKeyRange', 'lowerBound', 1, 0, constructor=False)
        if open is pyv8.Undefined:
            open = False
        if open is not pyv8.Undefined:
            open = boolean(open)
        return IDBKeyRange(_KeyRange(lower=_to_key(lower), lower_open=bool(open)))

    @staticmethod
    def fn_upperBound(_realm, upper=MISSING, open=False, *_extra):
        if upper is MISSING:
            raise missing_error('IDBKeyRange', 'upperBound', 1, 0, constructor=False)
        if open is pyv8.Undefined:
            open = False
        if open is not pyv8.Undefined:
            open = boolean(open)
        return IDBKeyRange(_KeyRange(upper=_to_key(upper), upper_open=bool(open)))

    @staticmethod
    def fn_bound(_realm, lower=MISSING, upper=MISSING, lower_open=False, upper_open=False, *_extra):
        if lower is MISSING:
            raise missing_error('IDBKeyRange', 'bound', 2, 0, constructor=False)
        if upper is MISSING:
            raise missing_error('IDBKeyRange', 'bound', 2, 1, constructor=False)
        if lower_open is pyv8.Undefined:
            lower_open = False
        if lower_open is not pyv8.Undefined:
            lower_open = boolean(lower_open)
        if upper_open is pyv8.Undefined:
            upper_open = False
        if upper_open is not pyv8.Undefined:
            upper_open = boolean(upper_open)
        lower_key, upper_key = _to_key(lower), _to_key(upper)
        result = _cmp(lower_key, upper_key)
        if result > 0 or (result == 0 and (lower_open or upper_open)):
            raise _data_error("The lower bound is greater than the upper bound.")
        return IDBKeyRange(_KeyRange(lower_key, upper_key, bool(lower_open), bool(upper_open)))

    def get_lower(self):
        return _key_to_value(self._range.lower) if self._range.lower is not None else pyv8.Undefined

    def get_upper(self):
        return _key_to_value(self._range.upper) if self._range.upper is not None else pyv8.Undefined

    def get_lowerOpen(self):
        return self._range.lower_open

    def get_upperOpen(self):
        return self._range.upper_open

    def fn_includes(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('IDBKeyRange', 'includes', 1, 0, constructor=False)
        return self._range.contains(_to_key(value))

_DIRECTIONS = ("next", "nextunique", "prev", "prevunique")


class _CursorBody:
    """Iteration shared by :class:`IDBCursor` and :class:`IDBCursorWithValue`."""

    def _init_cursor(self, source, key_range, direction, key_only, request):
        self._source = source
        self._range = key_range if key_range is not None else _KeyRange()
        self._direction = direction if direction in _DIRECTIONS else "next"
        self._key_only = key_only
        self._request = request
        self._position = None
        self._key = None
        self._primary_key = None
        self._value = None

    def _entries(self):
        return self._source._entries_for(self._range)

    def _transaction(self):
        return self._source._transaction

    def _require_usable(self):
        transaction = self._transaction()
        if transaction is None or transaction._state == "finished":
            raise _invalid_state_error("The cursor's transaction is no longer active.")
        if transaction._state != "active":
            raise _transaction_inactive_error()

    def _ordered(self):
        entries = self._entries()
        return list(reversed(entries)) if self._direction.startswith("prev") else entries

    def _scan(self, cut_key, cut_primary, inclusive):
        for key, primary_key, value in self._ordered():
            result = _cmp(key, cut_key)
            if result == 0 and cut_primary is not None:
                result = _cmp(primary_key, cut_primary)
            if self._direction.startswith("prev"):
                ok = result < 0 or (inclusive and result == 0)
            else:
                ok = result > 0 or (inclusive and result == 0)
            if ok:
                return (key, primary_key, value)
        return None

    def _step(self, cut_key=None, cut_primary=None, inclusive=False, steps=1):
        found = None
        for _ in range(max(1, steps)):
            if cut_key is None and self._position is None:
                entries = self._ordered()
                found = entries[0] if entries else None
            elif cut_key is None:
                found = self._scan(self._position[0], self._position[1], False)
            else:
                found = self._scan(cut_key, cut_primary, inclusive)
            if found is None:
                break
            cut_key, cut_primary, inclusive = found[0], found[1], False
        # "unique" directions skip every record sharing the key just returned.
        while found is not None and self._direction.endswith("unique") and self._key is not None:
            if _cmp(found[0], self._key) != 0:
                break
            found = self._scan(found[0], found[1], False)
        if found is None:
            self._position = None
            self._key = self._primary_key = self._value = None
            return None
        self._position = (found[0], found[1])
        self._key, self._primary_key, self._value = found
        return found

    def _iterate(self, cut_key=None, cut_primary=None, inclusive=False, steps=1):
        request = self._request
        transaction = self._transaction()
        request._reopen()
        transaction._pending += 1

        def run():
            transaction._pending -= 1
            transaction._begin_dispatch()
            try:
                found = self._step(cut_key, cut_primary, inclusive, steps)
                request._succeed(self if found is not None else pyv8.Null)
            finally:
                transaction._end_dispatch()

        post_or_call(getattr(self._source, "_services", None), run)

    def get_source(self):
        return self._source

    def get_direction(self):
        return self._direction

    def get_key(self):
        return _key_to_value(self._key) if self._key is not None else pyv8.Undefined

    def get_primaryKey(self):
        return (
            _key_to_value(self._primary_key)
            if self._primary_key is not None
            else pyv8.Undefined
        )

    def get_request(self):
        return self._request

    def fn_continue(self, key=None, *_extra):
        if key is pyv8.Undefined:
            key = None
        self._require_usable()
        if is_nullish(key):
            self._iterate()
            return None
        self._iterate(cut_key=_to_key(key), inclusive=True)
        return None

    def fn_advance(self, count=MISSING, *_extra):
        if count is MISSING:
            raise missing_error('IDBCursor', 'advance', 1, 0, constructor=False)
        count = integer(count, enforce_range=True)
        self._require_usable()
        steps = int(count)
        if steps <= 0:
            raise PageTypeError("The count parameter must be greater than 0.")
        self._iterate(steps=steps)
        return None

    def fn_continuePrimaryKey(self, key=MISSING, primary_key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('IDBCursor', 'continuePrimaryKey', 2, 0, constructor=False)
        if primary_key is MISSING:
            raise missing_error('IDBCursor', 'continuePrimaryKey', 2, 1, constructor=False)
        self._require_usable()
        if not getattr(self._source, "_is_index", False):
            raise _invalid_access_error("The cursor's source is not an index.")
        self._iterate(cut_key=_to_key(key), cut_primary=_to_key(primary_key), inclusive=True)
        return None

    def fn_update(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('IDBCursor', 'update', 1, 0, constructor=False)
        self._require_usable()
        if self._transaction()._mode == "readonly":
            raise _read_only_error()
        return self._source._cursor_update(self, value)

    def fn_delete(self, *_extra):
        self._require_usable()
        if self._transaction()._mode == "readonly":
            raise _read_only_error()
        return self._source._cursor_delete(self)


@interface('IDBCursor', None)
class IDBCursor(_CursorBody):
    def __init__(self, source=None, key_range=None, direction="next", key_only=True, request=None, **_):
        self._init_cursor(source, key_range, direction, key_only, request)


@interface('IDBCursorWithValue', 'IDBCursor')
class IDBCursorWithValue(IDBCursor):
    def get_value(self):
        return _handed(self._value) if self._value is not None else pyv8.Undefined


@interface('IDBTransaction', 'EventTarget')
class IDBTransaction(EventTarget):
    def __init__(self, services=None, database=None, stores=(), mode="readonly",
                 durability="default", on_finish=None, **kw):
        super().__init__(services=services, **kw)
        self._database = database
        self._store_names = tuple(stores)
        self._mode = mode
        self._durability = durability
        self._state = "active"
        self._error = None
        self._pending = 0
        self._handles = {}
        self._finish_hooks = [on_finish] if on_finish is not None else []
        # Before-image of every store this transaction can write, captured on
        # first touch; ``_abort_now`` restores it and ``_commit_now`` drops it.
        self._snapshot = None

    def _deactivate(self):
        if self._state == "active":
            self._state = "inactive"
        self._maybe_finish()

    def _begin_dispatch(self):
        if self._state != "finished":
            self._state = "active"

    def _end_dispatch(self):
        if self._state != "finished":
            self._state = "inactive"
            self._maybe_finish()

    def _maybe_finish(self):
        if self._state in ("finished", "committing"):
            return
        if self._pending:
            return
        self._state = "committing"
        post_or_call(self._services, self._commit_now)

    def _commit_now(self):
        if self._state == "finished":
            return
        self._state = "finished"
        self._snapshot = None
        database = self._database
        if database is not None:
            if database._upgrade_version is not None:
                database._model.version = database._upgrade_version
                database._upgrade_version = None
            database._version = database._model.version
            database._upgrade_transaction = None
            database._layout_backup = None
            database._release(self)
        self._dispatch("complete")
        self._fire_hooks(None)

    def _ensure_snapshot(self):
        """Snapshot every store this transaction may write, lazily on first
        touch, so the rollback target reflects already-committed transactions.
        """
        if self._snapshot is not None or self._database is None:
            return
        if self._mode not in ("readwrite", "versionchange"):
            self._snapshot = {}
            return
        model = self._database._model
        if self._mode == "versionchange":
            stores = list(model.stores.values())
        else:
            names = set(self._store_names)
            stores = [store for store_name, store in model.stores.items() if store_name in names]
        self._snapshot = {store: store._snapshot() for store in stores}

    def _abort_now(self, error):
        if self._state == "finished":
            return
        self._state = "finished"
        self._error = error if error is not None else _abort_error()
        database = self._database
        if database is not None:
            database._upgrade_version = None
            database._upgrade_transaction = None
            # Undo schema first: for a failed upgrade ``_layout_backup`` is the
            # store map from connection open, so resetting drops stores the
            # upgrade created and restores renamed/deleted ones.
            if self._mode == "versionchange" and database._layout_backup is not None:
                database._model.stores = dict(database._layout_backup)
            if self._snapshot:
                for store, snapshot in self._snapshot.items():
                    store._restore(snapshot)
            self._snapshot = None
            database._layout_backup = None
            database._release(self)
        self._dispatch("abort")
        self._fire_hooks(self._error)

    def _fire_hooks(self, error):
        hooks, self._finish_hooks = self._finish_hooks, []
        for hook in hooks:
            hook(error)

    def _dispatch(self, event_type):
        return self.fn_dispatchEvent(Event(event_type, {"isTrusted": True}))

    def _run(self, request, operation):
        self._pending += 1

        def execute():
            if self._state == "finished":
                # Aborted before this queued request ran; drop it, as browsers
                # drop the request list on abort.
                self._pending -= 1
                return
            self._pending -= 1
            self._ensure_snapshot()
            self._begin_dispatch()
            try:
                try:
                    result = operation()
                except BaseException as exc:
                    #: What the error family contains is what the request may report;
                    #: anything else is a host failure, which the page is told as the
                    #: unknown one and the host keeps the reason for.
                    if is_declared(exc):
                        error = exc
                    else:
                        logger.warning("an IndexedDB operation failed in the host: {}", exc)
                        error = _fail("UnknownError", "The operation failed.")
                    # A canceled error event (``defaultPrevented``) keeps the
                    # surrounding transaction alive.
                    if request._fail_with(error) and self._state != "finished":
                        self._abort_now(error)
                    return
                request._succeed(result)
            finally:
                self._end_dispatch()

        post_or_call(self._services, execute)

    def get_objectStoreNames(self):
        if self._mode == "versionchange" and self._database is not None:
            return DOMStringList(sorted(self._database._model.stores))
        return DOMStringList(self._store_names)

    def get_mode(self):
        return self._mode

    def get_durability(self):
        return self._durability

    def get_db(self):
        return self._database if self._database is not None else pyv8.Null

    def get_error(self):
        return self._error if self._error is not None else pyv8.Null

    def get_onabort(self):
        return self._get_handler("onabort")

    def set_onabort(self, value):
        value = event_handler(value)
        self._set_handler("onabort", value)

    def get_oncomplete(self):
        return self._get_handler("oncomplete")

    def set_oncomplete(self, value):
        value = event_handler(value)
        self._set_handler("oncomplete", value)

    def get_onerror(self):
        return self._get_handler("onerror")

    def set_onerror(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler("onerror", value)

    def fn_objectStore(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('IDBTransaction', 'objectStore', 1, 0, constructor=False)
        name = dom_string(name)
        name = str(name)
        if self._state == "finished":
            raise _invalid_state_error("The transaction has finished.")
        stores = self._database._model.stores
        in_scope = name in self._store_names or (
            self._mode == "versionchange" and name in stores
        )
        if not in_scope:
            raise _not_found_error(
                "The object store '%s' is not in the transaction's scope." % name
            )
        handle = self._handles.get(name)
        if handle is None:
            handle = IDBObjectStore(
                services=self._services, transaction=self, store=stores[name],
            )
            self._handles[name] = handle
        return handle

    def fn_abort(self, *_extra):
        if self._state == "finished":
            raise _invalid_state_error("The transaction has finished.")
        self._abort_now(_abort_error())
        return None

    def fn_commit(self, *_extra):
        if self._state != "active":
            raise _invalid_state_error("The transaction is not active.")
        self._state = "inactive"
        self._maybe_finish()
        return None


def _cursor_arguments(query, direction):
    """The query and direction a cursor call was made with."""
    if query is pyv8.Undefined:
        query = None
    if direction is pyv8.Undefined:
        direction = 'next'
    return query, enum_value(direction, _DIRECTIONS)


def _new_cursor(source, query, direction, key_only):
    """The request a cursor call answers through, and the cursor it walks.

    The caller has already checked that the source is still usable; the store
    and the index differ only in that check.
    """
    if direction not in _DIRECTIONS:
        raise PageTypeError("The direction parameter is not a valid cursor direction.")
    key_range = _to_key_range(query)
    transaction = source._transaction
    request = IDBRequest(services=source._services, source=source, transaction=transaction)
    cursor_type = IDBCursor if key_only else IDBCursorWithValue
    cursor = cursor_type(
        source=source, key_range=key_range, direction=direction, key_only=key_only,
        request=request,
    )
    transaction._pending += 1

    def run():
        transaction._pending -= 1
        if transaction._state == "finished":
            return
        transaction._begin_dispatch()
        try:
            found = cursor._step()
            request._succeed(cursor if found is not None else pyv8.Null)
        finally:
            transaction._end_dispatch()

    post_or_call(transaction._services, run)
    return request


def _require_usable_store(handle):
    transaction = handle._transaction
    if handle._store is None:
        raise _invalid_state_error("The object store has been deleted.")
    if transaction is None:
        raise _invalid_state_error("The object store has no transaction.")
    if transaction._state == "finished":
        raise _transaction_inactive_error("The transaction has finished.")
    if transaction._state != "active":
        raise _transaction_inactive_error()


def _require_writable(handle):
    _require_usable_store(handle)
    if handle._transaction._mode == "readonly":
        raise _read_only_error()


@interface('IDBObjectStore', None)
class IDBObjectStore:
    def __init__(self, services=None, transaction=None, store=None, **_):
        self._services = services
        self._transaction = transaction
        self._store = store
        self._is_index = False
        self._index_handles = {}

    def get_name(self):
        return self._store.name if self._store is not None else ""

    def set_name(self, value):
        value = dom_string(value)
        name = str(value)
        transaction = self._transaction
        if self._store is None:
            raise _invalid_state_error("The object store has been deleted.")
        if transaction._mode != "versionchange":
            raise _invalid_state_error(
                "The object store can only be renamed during a version change transaction."
            )
        if transaction._state != "active":
            raise _transaction_inactive_error()
        if self._store.name == name:
            return
        database = transaction._database._model
        if name in database.stores:
            raise _constraint_error("An object store named '%s' already exists." % name)
        del database.stores[self._store.name]
        self._store.name = name
        database.stores[name] = self._store

    def get_keyPath(self):
        if self._store is None or self._store.key_path is None:
            return pyv8.Null
        key_path = self._store.key_path
        return list(key_path) if isinstance(key_path, (list, tuple)) else key_path

    def get_indexNames(self):
        if self._store is None:
            return DOMStringList(())
        return DOMStringList(sorted(self._store.indexes))

    def get_transaction(self):
        return self._transaction

    def get_autoIncrement(self):
        return bool(self._store.auto_increment) if self._store is not None else False

    def _generate_key(self):
        store = self._store
        if store.generator > _MAX_KEY_GENERATOR:
            raise _constraint_error("The key generator has reached its limit.")
        key = store.generator
        store.generator += 1
        return (_NUMBER, float(key))

    def _resolve_key(self, value, key):
        """Return ``(stored_value, key)`` applying in-line keys and generators."""
        store = self._store
        given = not is_nullish(key)
        if store.key_path is not None:
            if given:
                raise _data_error(
                    "The object store uses in-line keys and the key parameter was provided."
                )
            cloned = _store_clone(value)
            extracted = _extract_key(cloned, store.key_path)
            if extracted is not None:
                return cloned, extracted
            if not store.auto_increment:
                raise _data_error("Evaluating the object store's key path yielded no value.")
            generated = self._generate_key()
            if not _inject_key(cloned, store.key_path, generated):
                raise _data_error("The generated key could not be injected into the value.")
            return cloned, generated
        if given:
            resolved = _to_key(key)
            if store.auto_increment:
                _maybe_update_generator(store, resolved)
            return _store_clone(value), resolved
        if not store.auto_increment:
            raise _data_error(
                "The object store uses out-of-line keys and no key generator."
            )
        return _store_clone(value), self._generate_key()

    def _entries(self, query, count=None, direction=None):
        query, count, option_direction = _unpack_query(query, count)
        direction = direction or option_direction
        key_range = _to_key_range(query)
        entries = [
            (key, key, value) for key, value in self._store.records
            if key_range.contains(key)
        ]
        return entries, count, direction

    def _entries_for(self, key_range):
        return [
            (key, key, value) for key, value in self._store.records
            if key_range.contains(key)
        ]

    def fn_get(self, query=MISSING, *_extra):
        if query is MISSING:
            raise missing_error('IDBObjectStore', 'get', 1, 0, constructor=False)
        return self._request(lambda: self._first(query, "value"))

    def fn_getKey(self, query=MISSING, *_extra):
        if query is MISSING:
            raise missing_error('IDBObjectStore', 'getKey', 1, 0, constructor=False)
        return self._request(lambda: self._first(query, "key"))

    def fn_count(self, query=None, *_extra):
        if query is pyv8.Undefined:
            query = None
        return self._request(lambda: len(self._entries(query)[0]))

    def fn_getAll(self, query=None, count=None, *_extra):
        if query is pyv8.Undefined:
            query = None
        if count is pyv8.Undefined:
            count = None
        if count is not None and count is not pyv8.Undefined:
            count = integer(count, enforce_range=True)
        def run():
            entries, limit, _direction = self._entries(query, count)
            return _limit([item[2] for item in entries], limit)
        return self._request(run)

    def fn_getAllKeys(self, query=None, count=None, *_extra):
        if query is pyv8.Undefined:
            query = None
        if count is pyv8.Undefined:
            count = None
        if count is not None and count is not pyv8.Undefined:
            count = integer(count, enforce_range=True)
        def run():
            entries, limit, _direction = self._entries(query, count)
            return _limit([_key_to_value(item[0]) for item in entries], limit)
        return self._request(run)

    def fn_getAllRecords(self, options=None, *_extra):
        if options is pyv8.Undefined:
            options = None
        options = dictionary(options)
        def run():
            entries, limit, direction = self._entries(options, None)
            if direction == "prev":
                entries = list(reversed(entries))
            return [IDBRecord(key, primary, value) for key, primary, value in _limit(entries, limit)]
        return self._request(run)

    def _first(self, query, wants):
        entries, _limit_value, _direction = self._entries(query)
        if not entries:
            return pyv8.Undefined
        return entries[0][2] if wants == "value" else _key_to_value(entries[0][0])

    def fn_add(self, value=MISSING, key=None, *_extra):
        if value is MISSING:
            raise missing_error('IDBObjectStore', 'add', 1, 0, constructor=False)
        if key is pyv8.Undefined:
            key = None
        return self._write(value, key, no_overwrite=True)

    def fn_put(self, value=MISSING, key=None, *_extra):
        if value is MISSING:
            raise missing_error('IDBObjectStore', 'put', 1, 0, constructor=False)
        if key is pyv8.Undefined:
            key = None
        return self._write(value, key, no_overwrite=False)

    def _write(self, value, key, no_overwrite):
        _require_writable(self)
        cloned, resolved = self._resolve_key(value, key)

        def run():
            _require_writable(self)
            self._store.store(resolved, cloned, no_overwrite)
            return _key_to_value(resolved)

        return self._request(run)

    def fn_delete(self, query=MISSING, *_extra):
        if query is MISSING:
            raise missing_error('IDBObjectStore', 'delete', 1, 0, constructor=False)
        _require_writable(self)
        key_range = _to_key_range(query, null_disallowed=True)

        def run():
            _require_writable(self)
            self._store.delete_range(key_range)
            return pyv8.Undefined

        return self._request(run)

    def fn_clear(self, *_extra):
        _require_writable(self)

        def run():
            _require_writable(self)
            self._store.clear()
            return pyv8.Undefined

        return self._request(run)

    def fn_index(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('IDBObjectStore', 'index', 1, 0, constructor=False)
        name = dom_string(name)
        name = str(name)
        if self._store is None:
            raise _invalid_state_error("The object store has been deleted.")
        if name not in self._store.indexes:
            raise _not_found_error("The index '%s' does not exist." % name)
        handle = self._index_handles.get(name)
        if handle is None:
            handle = IDBIndex(
                services=self._services, store_handle=self, index=self._store.indexes[name]
            )
            self._index_handles[name] = handle
        return handle

    def fn_createIndex(self, name=MISSING, key_path=MISSING, options=None, *_extra):
        if name is MISSING:
            raise missing_error('IDBObjectStore', 'createIndex', 2, 0, constructor=False)
        if key_path is MISSING:
            raise missing_error('IDBObjectStore', 'createIndex', 2, 1, constructor=False)
        name = dom_string(name)
        key_path = _string_or_strings(key_path)
        if options is pyv8.Undefined:
            options = None
        options = dictionary(options)
        name = str(name)
        options = options if isinstance(options, dict) else {}
        multi_entry = boolean(options.get("multiEntry", False))
        unique = boolean(options.get("unique", False))
        transaction = self._transaction
        if self._store is None:
            raise _invalid_state_error("The object store has been deleted.")
        if transaction._mode != "versionchange":
            raise _invalid_state_error(
                "Indexes can only be created during a version change transaction."
            )
        if transaction._state != "active":
            raise _transaction_inactive_error()
        if not _valid_key_path(key_path):
            raise _syntax_error("The keyPath argument contains an invalid key path.")
        if name in self._store.indexes:
            raise _constraint_error("An index named '%s' already exists." % name)
        index = _IndexModel(
            name,
            key_path,
            unique,
            multi_entry,
        )
        try:
            for key, value in list(self._store.records):
                index.add(value, key)
        except Exception:
            raise
        self._store.indexes[name] = index
        return IDBIndex(services=self._services, store_handle=self, index=index)

    def fn_deleteIndex(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('IDBObjectStore', 'deleteIndex', 1, 0, constructor=False)
        name = dom_string(name)
        name = str(name)
        transaction = self._transaction
        if self._store is None:
            raise _invalid_state_error("The object store has been deleted.")
        if transaction._mode != "versionchange":
            raise _invalid_state_error(
                "Indexes can only be deleted during a version change transaction."
            )
        if transaction._state != "active":
            raise _transaction_inactive_error()
        if name not in self._store.indexes:
            raise _not_found_error("The index '%s' does not exist." % name)
        del self._store.indexes[name]
        self._index_handles.pop(name, None)
        return None

    def fn_openCursor(self, query=None, direction='next', *_extra):
        query, direction = _cursor_arguments(query, direction)
        return self._open_cursor(query, direction, key_only=False)

    def fn_openKeyCursor(self, query=None, direction='next', *_extra):
        query, direction = _cursor_arguments(query, direction)
        return self._open_cursor(query, direction, key_only=True)

    def _open_cursor(self, query, direction, key_only):
        _require_usable_store(self)
        return _new_cursor(self, query, direction, key_only)

    def _cursor_update(self, cursor, value):
        primary_key = cursor._primary_key
        cloned = _store_clone(value)
        store = self._store
        if store.key_path is not None:
            extracted = _extract_key(cloned, store.key_path)
            if extracted is not None and _cmp(extracted, primary_key) != 0:
                raise _data_error("The effective key of the cursor cannot be changed.")

        def run():
            _require_writable(self)
            store.store(primary_key, cloned, False)
            return _key_to_value(primary_key)

        return self._request(run)

    def _cursor_delete(self, cursor):
        primary_key = cursor._primary_key

        def run():
            _require_writable(self)
            self._store.delete_range(_KeyRange(lower=primary_key, upper=primary_key))
            return pyv8.Undefined

        return self._request(run)

    def _request(self, operation):
        _require_usable_store(self)
        request = IDBRequest(services=self._services, source=self, transaction=self._transaction)
        self._transaction._run(request, operation)
        return request


@interface('IDBIndex', None)
class IDBIndex:
    def __init__(self, services=None, store_handle=None, index=None, **_):
        self._services = services
        self._store_handle = store_handle
        self._index = index
        self._is_index = True

    @property
    def _transaction(self):
        return self._store_handle._transaction

    def _require_live(self):
        self._store_handle and _require_usable_store(self._store_handle)
        if self._index not in self._store_handle._store.indexes.values():
            raise _invalid_state_error("The index has been deleted.")

    def _records(self, query, count=None, direction=None):
        query, count, option_direction = _unpack_query(query, count)
        direction = direction or option_direction
        key_range = _to_key_range(query)
        store = self._store_handle._store
        results = []
        for index_key, primary_key in self._index.entries:
            if not key_range.contains(index_key):
                continue
            entry = store.find(primary_key)
            if entry is None:
                continue
            results.append((index_key, primary_key, entry[1]))
        return results, count, direction

    def _entries_for(self, key_range):
        store = self._store_handle._store
        results = []
        for index_key, primary_key in self._index.entries:
            if not key_range.contains(index_key):
                continue
            entry = store.find(primary_key)
            if entry is None:
                continue
            results.append((index_key, primary_key, entry[1]))
        return results

    def get_name(self):
        return self._index.name

    def get_objectStore(self):
        return self._store_handle

    def get_keyPath(self):
        key_path = self._index.key_path
        return list(key_path) if isinstance(key_path, (list, tuple)) else key_path

    def get_multiEntry(self):
        return self._index.multi_entry

    def get_unique(self):
        return self._index.unique

    def fn_get(self, query=MISSING, *_extra):
        if query is MISSING:
            raise missing_error('IDBIndex', 'get', 1, 0, constructor=False)
        return self._request(lambda: self._first(query, "value"))

    def fn_getKey(self, query=MISSING, *_extra):
        if query is MISSING:
            raise missing_error('IDBIndex', 'getKey', 1, 0, constructor=False)
        return self._request(lambda: self._first(query, "primary"))

    def fn_count(self, query=None, *_extra):
        if query is pyv8.Undefined:
            query = None
        return self._request(lambda: len(self._records(query)[0]))

    def fn_getAll(self, query=None, count=None, *_extra):
        if query is pyv8.Undefined:
            query = None
        if count is pyv8.Undefined:
            count = None
        if count is not None and count is not pyv8.Undefined:
            count = integer(count, enforce_range=True)
        def run():
            records, limit, _direction = self._records(query, count)
            return _limit([item[2] for item in records], limit)
        return self._request(run)

    def fn_getAllKeys(self, query=None, count=None, *_extra):
        if query is pyv8.Undefined:
            query = None
        if count is pyv8.Undefined:
            count = None
        if count is not None and count is not pyv8.Undefined:
            count = integer(count, enforce_range=True)
        def run():
            records, limit, _direction = self._records(query, count)
            return _limit([_key_to_value(item[0]) for item in records], limit)
        return self._request(run)

    def fn_getAllRecords(self, options=None, *_extra):
        if options is pyv8.Undefined:
            options = None
        options = dictionary(options)
        def run():
            records, limit, direction = self._records(options, None)
            if direction == "prev":
                records = list(reversed(records))
            return [
                IDBRecord(key, primary, value) for key, primary, value in _limit(records, limit)
            ]
        return self._request(run)

    def _first(self, query, wants):
        records, _limit_value, _direction = self._records(query)
        if not records:
            return pyv8.Undefined
        return records[0][2] if wants == "value" else _key_to_value(records[0][1])

    def fn_openCursor(self, query=None, direction='next', *_extra):
        query, direction = _cursor_arguments(query, direction)
        return self._open_cursor(query, direction, key_only=False)

    def fn_openKeyCursor(self, query=None, direction='next', *_extra):
        query, direction = _cursor_arguments(query, direction)
        return self._open_cursor(query, direction, key_only=True)

    def _open_cursor(self, query, direction, key_only):
        self._require_live()
        return _new_cursor(self, query, direction, key_only)

    def _request(self, operation):
        self._require_live()
        request = IDBRequest(services=self._services, source=self, transaction=self._transaction)
        self._transaction._run(request, operation)
        return request


@interface('IDBDatabase', 'EventTarget')
class IDBDatabase(EventTarget):
    def __init__(self, services=None, model=None, version=0, factory=None, **kw):
        super().__init__(services=services, **kw)
        self._model = model
        self._version = version
        self._factory = factory
        self._close_pending = False
        self._upgrade_transaction = None
        self._upgrade_version = None
        self._layout_backup = None
        self._live_transactions = []

    def get_name(self):
        return self._model.name

    def get_version(self):
        return self._version

    def get_objectStoreNames(self):
        return DOMStringList(sorted(self._model.stores))

    def get_onabort(self):
        return self._get_handler("onabort")

    def set_onabort(self, value):
        value = event_handler(value)
        self._set_handler("onabort", value)

    def get_onclose(self):
        return self._get_handler("onclose")

    def set_onclose(self, value):
        value = event_handler(value)
        self._set_handler("onclose", value)

    def get_onerror(self):
        return self._get_handler("onerror")

    def set_onerror(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler("onerror", value)

    def get_onversionchange(self):
        return self._get_handler("onversionchange")

    def set_onversionchange(self, value):
        value = event_handler(value)
        self._set_handler("onversionchange", value)

    def fn_createObjectStore(self, name=MISSING, options=None, *_extra):
        if name is MISSING:
            raise missing_error('IDBDatabase', 'createObjectStore', 1, 0, constructor=False)
        name = dom_string(name)
        if options is pyv8.Undefined:
            options = None
        options = dictionary(options)
        name = str(name)
        options = options if isinstance(options, dict) else {}
        auto_increment = boolean(options.get("autoIncrement", False))
        key_path = options.get("keyPath", None)
        key_path = None if is_nullish(key_path) else _string_or_strings(key_path)
        transaction = self._upgrade_transaction
        if transaction is None:
            raise _invalid_state_error("The database is not running a version change transaction.")
        if transaction._state != "active":
            raise _transaction_inactive_error()
        if key_path is not None and not _valid_key_path(key_path):
            raise _syntax_error("The keyPath argument contains an invalid key path.")
        if auto_increment and (key_path == "" or isinstance(key_path, (list, tuple))):
            raise _invalid_access_error(
                "The autoIncrement flag was set but the keyPath is an empty string or an array."
            )
        if name in self._model.stores:
            raise _constraint_error("An object store named '%s' already exists." % name)
        store = _StoreModel(name, key_path, auto_increment)
        self._model.stores[name] = store
        return IDBObjectStore(services=self._services, transaction=transaction, store=store)

    def fn_deleteObjectStore(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('IDBDatabase', 'deleteObjectStore', 1, 0, constructor=False)
        name = dom_string(name)
        name = str(name)
        transaction = self._upgrade_transaction
        if transaction is None:
            raise _invalid_state_error("The database is not running a version change transaction.")
        if transaction._state != "active":
            raise _transaction_inactive_error()
        if name not in self._model.stores:
            raise _not_found_error("The object store '%s' does not exist." % name)
        del self._model.stores[name]

    def fn_transaction(self, store_names=MISSING, mode='readonly', options=None, *_extra):
        if store_names is MISSING:
            raise missing_error('IDBDatabase', 'transaction', 1, 0, constructor=False)
        store_names = _string_or_strings(store_names)
        if mode is pyv8.Undefined:
            mode = 'readonly'
        mode = enum_value(mode, ('readonly', 'readwrite'))
        if options is pyv8.Undefined:
            options = None
        options = dictionary(options)
        options = options if isinstance(options, dict) else {}
        durability = enum_value(options.get("durability", "default"), ("default", "strict", "relaxed"))
        if self._close_pending:
            raise _invalid_state_error("The database connection is closing.")
        upgrade = self._upgrade_transaction
        if upgrade is not None and upgrade._state != "finished":
            raise _invalid_state_error("A version change transaction is running.")
        if isinstance(store_names, (list, tuple)):
            scope = []
            for name in store_names:
                name = str(name)
                if name not in scope:
                    scope.append(name)
        else:
            scope = [str(store_names)]
        if not scope:
            raise _invalid_access_error("The transaction has no object stores in its scope.")
        for name in scope:
            if name not in self._model.stores:
                raise _not_found_error("The object store '%s' does not exist." % name)
        if mode not in ("readonly", "readwrite"):
            raise PageTypeError("The mode parameter is not 'readonly' or 'readwrite'.")
        transaction = IDBTransaction(
            services=self._services, database=self, stores=scope, mode=mode,
            durability=durability,
        )
        self._live_transactions.append(transaction)
        self._services.task_services.post_task(transaction._deactivate)
        return transaction

    def fn_close(self, *_extra):
        if self._close_pending:
            return None
        self._close_pending = True
        if self in self._model.connections:
            self._model.connections.remove(self)
        return None

    def _release(self, transaction):
        if transaction in self._live_transactions:
            self._live_transactions.remove(transaction)

    def _fire_versionchange(self):
        event = IDBVersionChangeEvent(
            "versionchange", {"oldVersion": self._version, "newVersion": None}
        )
        self.fn_dispatchEvent(event)


@interface('IDBFactory', None)
class IDBFactory:
    def __init__(self, services=None, **_):
        self._services = services

    def _schedule(self, callback):
        if self._services is not None:
            self._services.task_services.schedule(callback, _BLOCKED_POLL_SECONDS, repeat=False)
        else:
            callback()

    def fn_open(self, name=MISSING, version=None, *_extra):
        if name is MISSING:
            raise missing_error('IDBFactory', 'open', 1, 0, constructor=False)
        name = dom_string(name)
        if version is pyv8.Undefined:
            version = None
        if version is not None and version is not pyv8.Undefined:
            version = number(version)
            try:
                version = integer(version, bits=64, enforce_range=True)
            except (PageTypeError, IdlArgumentError) as exc:
                raise PageTypeError("Failed to execute 'open' on 'IDBFactory': Value is outside the 'unsigned long long' value range.") from exc
        name = str(name)
        requested = None
        if not is_nullish(version):
            requested = int(version)
            if requested <= 0:
                raise PageTypeError("The version provided must be greater than 0.")
        request = IDBOpenDBRequest(services=self._services)
        post_or_call(self._services, lambda: self._open_step(request, name, requested, 0.0))
        return request

    def _open_step(self, request, name, requested, waited):
        databases = _databases(self._services)
        with _LOCK:
            model = databases.get(name)
            created = False
            if model is None:
                model = _DatabaseModel(name)
                databases[name] = model
                created = True
            if requested is None:
                target = model.version if model.version else 1
            else:
                target = requested
            if not created and requested is not None and requested < model.version:
                request._fail_with(_version_error())
                return
            needs_upgrade = created or target > model.version
            if needs_upgrade and not self._clear_blockers(request, model, waited,
                                                           lambda delay: self._open_step(
                                                               request, name, requested, delay)):
                return
            connection = IDBDatabase(
                services=self._services, model=model, version=model.version, factory=self,
            )
            model.connections.append(connection)
            if not needs_upgrade:
                request._succeed(connection)
                return
            self._run_upgrade(request, connection, model, target)

    def _clear_blockers(self, request, model, waited, retry):
        """Fire ``versionchange``/``blocked``; return True when free to proceed."""
        others = [connection for connection in model.connections if not connection._close_pending]
        if not others:
            return True
        if not request._blocked_fired:
            request._blocked_fired = True
            request._dispatch("blocked")
        for connection in others:
            connection._fire_versionchange()
        still_open = any(
            not connection._close_pending for connection in model.connections
        )
        if still_open and waited < _BLOCKED_WAIT_SECONDS:
            self._schedule(lambda: retry(waited + _BLOCKED_POLL_SECONDS))
            return False
        return True

    def _run_upgrade(self, request, connection, model, target):
        old_version = model.version
        connection._layout_backup = dict(model.stores)

        def finish(error):
            if error is not None:
                # open() failed, so do not leave the connection registered as a
                # live connection that blocks upgrades or gets versionchange.
                connection._close_pending = True
                if connection in model.connections:
                    model.connections.remove(connection)
                connection._version = old_version
                request._fail_with(error)
                return
            connection._version = model.version
            request._succeed(connection)

        transaction = IDBTransaction(
            services=self._services, database=connection, stores=("*",), mode="versionchange",
            on_finish=finish,
        )
        connection._upgrade_transaction = transaction
        connection._upgrade_version = target
        connection._live_transactions.append(transaction)
        request._transaction = transaction
        # Snapshot before ``upgradeneeded`` so schema edits are rolled back too.
        transaction._ensure_snapshot()

        def dispatch():
            request._result = connection
            request._result_ready = True
            transaction._state = "active"
            request._dispatch_version_change("upgradeneeded", old_version, target)
            # The handler may have aborted or raised, finishing the transaction;
            # don't resurrect it into a commit.
            transaction._end_dispatch()

        post_or_call(self._services, dispatch)

    def fn_deleteDatabase(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('IDBFactory', 'deleteDatabase', 1, 0, constructor=False)
        name = dom_string(name)
        name = str(name)
        request = IDBOpenDBRequest(services=self._services)
        post_or_call(self._services, lambda: self._delete_step(request, name, 0.0))
        return request

    def _delete_step(self, request, name, waited):
        databases = _databases(self._services)
        with _LOCK:
            model = databases.get(name)
            if model is None:
                request._succeed_version_change(0, None)
                return
            if not self._clear_blockers(request, model, waited,
                                        lambda delay: self._delete_step(request, name, delay)):
                return
            old_version = model.version
            for connection in list(model.connections):
                connection._close_pending = True
            model.connections.clear()
            del databases[name]
            request._succeed_version_change(old_version, None)

    def fn_databases(self, *_extra):
        promise, resolve, _reject = self._services.realm_services.make_promise()

        def run():
            with _LOCK:
                snapshot = [
                    {"name": model.name, "version": model.version}
                    for model in _databases(self._services).values()
                    if model.version
                ]
            resolve(snapshot)

        post_or_call(self._services, run)
        return promise

    def fn_cmp(self, first=MISSING, second=MISSING, *_extra):
        if first is MISSING:
            raise missing_error('IDBFactory', 'cmp', 2, 0, constructor=False)
        if second is MISSING:
            raise missing_error('IDBFactory', 'cmp', 2, 1, constructor=False)
        return _cmp(_to_key(first), _to_key(second))

FEATURE = FeatureImplementation(
    {
        "IDBRecord": IDBRecord,
        "IDBVersionChangeEvent": IDBVersionChangeEvent,
        "IDBRequest": IDBRequest,
        "IDBOpenDBRequest": IDBOpenDBRequest,
        "IDBKeyRange": IDBKeyRange,
        "IDBCursor": IDBCursor,
        "IDBCursorWithValue": IDBCursorWithValue,
        "IDBTransaction": IDBTransaction,
        "IDBObjectStore": IDBObjectStore,
        "IDBIndex": IDBIndex,
        "IDBDatabase": IDBDatabase,
        "IDBFactory": IDBFactory,
    },
    {"indexedDB": lambda window: IDBFactory(services=window._services)},
)
