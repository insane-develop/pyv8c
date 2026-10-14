"""Structured-clone support for the message and storage channels.

The Window, Worker and IndexedDB channels all move values across a realm
boundary, so all three share this implementation.  A clone is a snapshot taken where
the value is sent and data built where it is read.  Each helper the reading realm runs
is compiled once per realm (``_helper``), not once per value.  Binary crosses whole:
``_bytes`` reads a buffer or a view in one piece, and Python ``bytes`` arrive in a realm
as a fresh ``ArrayBuffer`` -- never element by element, which is how a multi-byte
element once lost all but one of its bytes.

One rule governs a realm that cannot answer: the clone falls back to the plain data
behind the value and the reason goes to the log; what would lose data outright -- a
function, a cyclic structure -- is refused with ``DataCloneError``, as a browser
refuses it.
"""

import weakref
from dataclasses import dataclass

import pyv8

from ...foundation.arguments import MISSING, dictionary, is_nullish, materialize, sequence
from ...foundation.logging import logger
from ..page_errors import DataCloneError


#: The compiled helpers, one per realm: the sources below say nothing about the realm
#: they run in, so a realm compiles each once and every clone after the first reuses
#: it.  A realm that cannot be referenced is compiled per call, which costs time and
#: nothing else.
_HELPERS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _helper(context, source):
    """*source* compiled in *context*, built once per realm."""
    try:
        helpers = _HELPERS[context]
    except TypeError:
        return context.exec_js(source)
    except KeyError:
        helpers = _HELPERS[context] = {}
    compiled = helpers.get(source)
    if compiled is None:
        compiled = helpers[source] = context.exec_js(source)
    return compiled


def message_transfer(value):
    if is_nullish(value):
        return ()
    iterator = value._iterator_method() if isinstance(value, pyv8.JSObject) else MISSING
    if isinstance(value, (list, tuple)) or (iterator is not MISSING and not is_nullish(iterator)):
        return sequence(value, iterator_method=iterator)
    options = dictionary(value, name="PostMessageOptions")
    return sequence(options.get("transfer", ()))


#: What a transfer list may name besides a port, asked of the realm that holds the
#: value: a detached buffer has nothing left in it, and the realm's own ``detached``
#: is what tells that from a buffer that was always empty.
_BINARY_KIND = """value => {
  if (Object.prototype.toString.call(value) === '[object ArrayBuffer]')
    return value.detached ? 'detached' : 'buffer';
  if (ArrayBuffer.isView(value)) return 'view';
  return null;
}"""

#: Handing a buffer over leaves nothing behind: ``transfer()`` moves the bytes out and
#: the buffer that stays answers ``byteLength 0``, which is what Chrome shows too.
_DETACH = """buffer => {
  if (typeof buffer.transfer === 'function') buffer.transfer();
  else structuredClone(buffer, {transfer: [buffer]});
}"""


def _binary_kind(value, context):
    """``'buffer'``/``'view'``/``'detached'`` for a value shaped like a transferable."""
    if context is None:
        return None
    try:
        answer = _helper(context, _BINARY_KIND).call(None, value)
    except BaseException as exc:  # a value the realm cannot describe is simply not one
        logger.debug("a transfer list value could not be classified: {}", exc)
        return None
    #: The realm answers in a string, and answers ``null`` for everything else.
    return answer if isinstance(answer, str) else None


def _detach_buffer(value, context):
    if context is not None:
        _helper(context, _DETACH).call(None, value)


def transfer_ports(transfer=(), services=None, caller="Window"):
    """The ports a transfer list moves, once everything it named has been checked.

    A ``MessagePort`` moves: the reference left behind is neutered while the port
    itself arrives in the message event's ``ports``.  An ``ArrayBuffer`` is handed over
    as well -- its bytes travel in the message, since a message is cloned before the
    list is applied, and the sender's buffer is detached.  A view of a buffer is not
    transferable, and neither is a buffer that was already detached; Chrome names the
    index it refuses, so the wording says which index and which way it failed.
    """
    from ..bom.messaging import MessagePort

    if is_nullish(transfer):
        return []
    listed = list(transfer)
    context = pyv8.current_context()
    planned = []
    for index, raw in enumerate(listed):
        kind = _binary_kind(raw, context)
        if kind == "buffer":
            planned.append(("buffer", raw))
            continue
        if kind == "detached":
            raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}':"
                                 f" ArrayBuffer at index {index} is already detached.")
        if kind == "view":
            raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}':"
                                 f" Value at index {index} does not have a transferable type.")
        item = materialize(raw)
        if not isinstance(item, MessagePort):
            raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}':"
                                 f" Value at index {index} does not have a transferable type.")
        if item._detached:
            raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}':"
                                 f" Port at index {index} is already neutered.")
        planned.append(("port", item))

    moved = []
    for kind, item in planned:
        if kind == "port":
            moved.append(item._moved_view(services))
        else:
            _detach_buffer(item, context)
    return moved


#: What a reader has not been handed yet: a marker is built once, for the realm that
#: reads it, and the value that realm got is what it keeps.
unread = object()


@dataclass(frozen=True)
class ClonedBuffer:
    """A buffer on its way to another realm, built once where it is read.

    A message keeps one of these for each buffer that was sent, so every view of that
    buffer arrives looking through the same one -- which is what a browser's clone does.
    """

    data: bytes


@dataclass(frozen=True)
class ClonedView:
    """A view of a buffer, and the place in that buffer it looks through."""

    name: str
    buffer: ClonedBuffer
    offset: int = 0
    length: int = 0


@dataclass(frozen=True)
class ClonedFile:
    """A ``File`` on its way to another realm: its bytes, its name and its properties."""

    data: bytes
    name: str
    type: str
    last_modified: int


@dataclass(frozen=True)
class ClonedError:
    """An ``Error`` -- or one of the native errors -- on its way to another realm.

    What a page gets is an error of the same kind with the same name and message, and a
    stack of its own where it was built, which is what a browser's clone leaves.
    """

    name: str
    message: str = ""


@dataclass(frozen=True)
class ClonedStructured:
    """A ``Map``, ``Set``, ``Date`` or ``RegExp`` on its way to another realm.

    ``args`` are what its constructor takes, cloned already, so whatever is nested in
    them is built in the reading realm first.
    """

    name: str
    args: tuple = ()


#: Builds the buffer a marker holds, in the realm that is reading it: Python ``bytes``
#: arrive as a fresh ``ArrayBuffer``, and the view wrap is what hands the realm its own
#: buffer object, so every view of one buffer can be built over the same one.
_BUILD_BUFFER = "(bytes) => new Uint8Array(bytes).buffer"

#: Builds a view over a buffer that realm already built.
_BUILD_VIEW = """(name, buffer, offset, length) => {
  if (name === 'ArrayBuffer') return buffer;
  if (name === 'DataView') return new DataView(buffer, offset, length);
  return new globalThis[name](buffer, offset, length);
}"""

#: Builds a ``Map``, ``Set``, ``Date`` or ``RegExp`` out of arguments already built.
_BUILD_STRUCTURED = "(name, args) => new globalThis[name](...args)"

#: Builds a ``File`` from the bytes it held, its name and its properties.  The bytes go
#: in as a view over the buffer they crossed as, which is what a file part is.
_BUILD_FILE = "(bytes, name, options) => new File([new Uint8Array(bytes)], name, options)"

#: Builds an error of the same kind, named as it was where it came from.
_BUILD_ERROR = """(name, message) => {
  const named = globalThis[name];
  const error = (typeof named === 'function' && named.prototype instanceof Error)
    ? new named(message) : new Error(message);
  if (error.name !== name) error.name = name;
  return error;
}"""


def rebuild_clone(value, context=None):
    """Hand *value* what it describes, built in *context*.

    A realm reads a message's ``data`` -- or a request's ``result`` -- through this, so
    what it gets is its own: one buffer for each one that was sent, the same kind of view
    over it, and the same kind of ``Map``, ``Set``, ``Date`` or ``RegExp``.  Views of one
    buffer are built over the same buffer, as a browser's clone leaves them.  Without a
    realm to build in there is nothing to build in, so binary reads as the bytes it holds
    and the rest stays as what it was read from, which is what a host reading it through
    Python has.
    """
    return _rebuild(value, context, {})


def _rebuild(value, context, built):
    if isinstance(value, ClonedBuffer):
        if context is None:
            return value.data
        buffer = built.get(id(value))
        if buffer is None:
            try:
                buffer = _helper(context, _BUILD_BUFFER).call(None, value.data)
            except BaseException as exc:
                logger.debug("a buffer could not be built in the receiving realm: {}", exc)
                return value.data
            built[id(value)] = buffer
        return buffer
    if isinstance(value, ClonedView):
        buffer = _rebuild(value.buffer, context, built)
        if context is None:
            return buffer
        try:
            return _helper(context, _BUILD_VIEW).call(
                None, value.name, buffer, value.offset, value.length)
        except BaseException as exc:
            logger.debug("a view could not be built in the receiving realm: {}", exc)
            return buffer
    if isinstance(value, ClonedFile):
        if context is None:
            return value
        try:
            return _helper(context, _BUILD_FILE).call(
                None, value.data, value.name,
                {"type": value.type, "lastModified": value.last_modified})
        except BaseException as exc:
            logger.debug("a file could not be built in the receiving realm: {}", exc)
            return value
    if isinstance(value, ClonedError):
        if context is None:
            return value
        try:
            return _helper(context, _BUILD_ERROR).call(None, value.name, value.message)
        except BaseException as exc:
            logger.debug("an error could not be built in the receiving realm: {}", exc)
            return value

    if isinstance(value, ClonedStructured):
        args = tuple(_rebuild(argument, context, built) for argument in value.args)
        if context is None:
            return value
        try:
            return _helper(context, _BUILD_STRUCTURED).call(None, value.name, args)
        except BaseException as exc:
            logger.debug("a structured value could not be built: {}", exc)
            return value
    if isinstance(value, dict):
        return {key: _rebuild(item, context, built) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebuild(item, context, built) for item in value]
    return value


#: The buffers one message is made of.  A buffer that was already read keeps the place it
#: was given, so the realm builds one buffer for it and every view of it looks through
#: that one -- which is what a browser's clone leaves behind.
_SESSION = """() => ({
  next: 1,
  seen: new WeakMap(),
  of(buffer) {
    let entry = this.seen.get(buffer);
    if (entry === undefined) {
      entry = {index: this.next++};
      this.seen.set(buffer, entry);
      return [entry.index, true];
    }
    return [entry.index, false];
  },
})"""

#: What a value is made of, read in the realm that holds it.  A buffer or a view is
#: named by the place it holds in its message's session -- the bytes themselves are read
#: whole through ``_bytes``, once per buffer, by the first view that names it -- with
#: the place in the buffer the view looks through; a ``Map``, ``Set``, ``Date`` or
#: ``RegExp`` keeps the contents a browser keeps.
_STRUCTURED_VALUE = """(session, value) => {
  const tag = Object.prototype.toString.call(value);
  if (tag === '[object ArrayBuffer]') {
    const [index, first] = session.of(value);
    return ['buffer', index, 0, value.byteLength, 'ArrayBuffer', first];
  }
  if (ArrayBuffer.isView(value)) {
    const [index, first] = session.of(value.buffer);
    return ['buffer', index, value.byteOffset,
            value.length === undefined ? value.byteLength : value.length,
            value.constructor.name, first];
  }
  if (tag === '[object DOMException]') return ['DOMException', value.name, value.message];
  if (value instanceof Error) return ['Error', value.name, value.message];
  if (tag === '[object Map]') return ['Map', Array.from(value.entries())];
  if (tag === '[object Set]') return ['Set', Array.from(value)];
  if (tag === '[object Date]') return ['Date', value.getTime()];
  if (tag === '[object RegExp]') return ['RegExp', value.source, value.flags];
  if (typeof value === 'function')
    return ['unclonable', Function.prototype.toString.call(value)];
  if (tag === '[object WeakMap]' || tag === '[object WeakSet]'
      || tag === '[object Promise]')
    return ['unclonable', '#<' + tag.slice(8, -1) + '>'];
  return null;
}"""


def _structured_value(value, session, buffers, context, clone, active):
    """A marker for a value that has to be built where it is read, or ``None``.

    What is inside a ``Map`` or a ``Set`` is cloned the same way, and one that holds
    itself is the cycle the rest of the clone refuses.
    """
    if context is None or session is None:
        return None
    try:
        answer = _helper(context, _STRUCTURED_VALUE).call(None, session, value)
    except BaseException as exc:  # a value this reads nothing out of is cloned as itself
        logger.debug("a value could not be read as a structured value: {}", exc)
        return None
    #: ``null`` is the realm's answer for everything that is not a structured kind --
    #: the plain data most of a message is made of -- and an answer is not a failure.
    #: The structured kinds come back as arrays.
    answer = answer.to_python() if isinstance(answer, pyv8.JSObject) else None
    if not isinstance(answer, list) or not answer:
        return None
    kind = answer[0]
    if kind == "buffer":
        _, index, offset, length, name, first = answer
        buffer = buffers.get(index)
        if buffer is None:
            if not first:  # the bytes were read with an earlier view of it
                return None
            holder = value if name == "ArrayBuffer" else _buffer_of(value)
            data = _whole_bytes(holder) if holder is not None else None
            if data is None:
                return None
            buffer = buffers[index] = ClonedBuffer(data)
        return ClonedView(str(name), buffer, int(offset), int(length))
    if kind == "Error":
        return ClonedError(str(answer[1]), str(answer[2]))
    if kind == "DOMException":
        #: A DOMException is built from its message and its name; the legacy ``code``
        #: follows from the name, so it arrives with it.  ``new DOMException()`` leaves no
        #: stack, which is what a browser's clone leaves too.
        return ClonedStructured("DOMException", (answer[2], answer[1]))
    if kind == "unclonable":
        raise DataCloneError(f"{answer[1]} could not be cloned.")
    if kind == "Date":
        return ClonedStructured("Date", (answer[1],))
    if kind == "RegExp":
        return ClonedStructured("RegExp", (answer[1], answer[2]))
    marker = id(value)
    if marker in active:
        raise DataCloneError("Cyclic message data is not supported")
    active.add(marker)
    try:
        if kind == "Map":
            return ClonedStructured(
                "Map", ([[clone(entry[0]), clone(entry[1])] for entry in answer[1]],))
        if kind == "Set":
            return ClonedStructured("Set", ([clone(item) for item in answer[1]],))
    finally:
        active.remove(marker)
    return None


#: The buffer a view looks through, as the realm holds it.
def _buffer_of(view):
    try:
        return view._get("buffer")
    except BaseException as exc:
        logger.debug("the buffer behind a view could not be read: {}", exc)
        return None


#: The bytes a buffer or a view holds, read in one piece.
def _whole_bytes(holder):
    try:
        return holder._bytes()
    except BaseException as exc:
        logger.debug("the bytes of a buffer could not be read whole: {}", exc)
        return None


def _view_name(item):
    """The constructor name of a typed array, as its own realm spells it."""
    constructor = getattr(item, "constructor", None)
    return str(constructor).split("(")[0].split()[-1] if constructor is not None else ""


def _held_view(item):
    """A typed array read out of a realm already, as the bytes it holds.

    There is no session here to share a buffer through -- this is the no-realm
    fallback -- so the range the view looks at crosses as its own buffer, and the
    constructor name is what says whether the numbers are integers; a name the realm
    does not spell reads as a plain buffer of those bytes.
    """
    data = _whole_bytes(item)
    if data is None:
        return None
    return ClonedView(_view_name(item) or "ArrayBuffer", ClonedBuffer(data), 0, len(data))


def _session(context):
    """The buffer identities of one message, or ``None`` where there is no realm to ask."""
    if context is None:
        return None
    try:
        return _helper(context, _SESSION).call(None)
    except BaseException as exc:
        logger.debug("buffers cannot be tracked for this clone: {}", exc)
        return None


def structured_clone(value, context=None):
    active = set()
    #: One message's buffers, so a view of a buffer already read keeps that buffer.
    buffers = {}
    session = _session(context)

    def clone(item):
        from ..bom.filesystem import File
        from .blob import Blob

        marker = _structured_value(item, session, buffers, context, clone, active)
        item = materialize(item)

        if item is None or item is pyv8.Null or item is pyv8.Undefined:
            return item
        if isinstance(item, (bool, int, float, str)):
            return item
        if isinstance(item, File):
            #: A ``File`` is a ``Blob`` that remembers its name and timestamp, so it is
            #: built back as one instead of as the blob it is made of.
            return ClonedFile(bytes(item._data), item.get_name(), item.get_type(),
                              int(item.get_lastModified()))
        if isinstance(item, Blob):
            return Blob([bytes(item._data)], {"type": item.get_type()})
        if isinstance(item, (bytes, bytearray, memoryview)):
            return bytes(item)
        if marker is not None:
            return marker
        if type(item).__name__ in ("TypedArray", "JSTypeArray"):
            view = _held_view(item)
            if view is not None:
                return view
        if isinstance(item, (dict, list, tuple)):
            marker = id(item)
            if marker in active:
                raise DataCloneError("Cyclic message data is not supported")
            active.add(marker)
            try:
                if isinstance(item, dict):
                    return {str(key): clone(child) for key, child in item.items()}
                return [clone(child) for child in item]
            finally:
                active.remove(marker)
        raise DataCloneError(f"The object of type {type(item).__name__!r} could not be cloned")

    return clone(value)


_CLONE_API = r'''(value, transfer) => {
  const seen=new Map(), transferred=new Set();
  const fail=()=>{throw new DOMException('The object could not be cloned.','DataCloneError')};
  for(const buffer of transfer){
    if(Object.prototype.toString.call(buffer)!=='[object ArrayBuffer]' || buffer.detached || transferred.has(buffer))fail();
    transferred.add(buffer);
  }
  function copy(value){
    if(typeof value==='function' || typeof value==='symbol')fail();
    if(value===null || typeof value!=='object')return value;
    if(seen.has(value))return seen.get(value);
    const tag=Object.prototype.toString.call(value);
    let result;
    if(tag==='[object ArrayBuffer]'){
      if(value.detached)fail();result=ArrayBuffer.prototype.slice.call(value,0);
    }else if(ArrayBuffer.isView(value)){
      const buffer=copy(value.buffer);
      result=tag==='[object DataView]'?new DataView(buffer,value.byteOffset,value.byteLength):
        new globalThis[tag.slice(8,-1)](buffer,value.byteOffset,value.length);
    }else if(tag==='[object Date]')result=new Date(Date.prototype.getTime.call(value));
    else if(tag==='[object RegExp]')result=new RegExp(value.source,value.flags);
    else if(tag==='[object Map]'){
      result=new Map();seen.set(value,result);
      for(const [key,item] of Map.prototype.entries.call(value))result.set(copy(key),copy(item));return result;
    }else if(tag==='[object Set]'){
      result=new Set();seen.set(value,result);
      for(const item of Set.prototype.values.call(value))result.add(copy(item));return result;
    }else if(tag==='[object Object]' || Array.isArray(value)){
      result=Array.isArray(value)?new Array(value.length):{};seen.set(value,result);
      for(const key of Object.keys(value))Object.defineProperty(result,key,{value:copy(value[key]),writable:true,enumerable:true,configurable:true});
      return result;
    }else if(tag==='[object Error]'){
      const Constructor=['Error','EvalError','RangeError','ReferenceError','SyntaxError','TypeError','URIError'].includes(value.name)?globalThis[value.name]:Error;
      result=new Constructor(value.message);seen.set(value,result);
      if(typeof value.stack==='string')result.stack=value.stack;
      if(Object.hasOwn(value,'cause'))result.cause=copy(value.cause);
    }else if(tag==='[object DOMException]')result=new DOMException(value.message,value.name);
    else if(tag==='[object Blob]')result=value.slice(0,value.size,value.type);
    else if(tag==='[object File]')result=new File([value],value.name,{type:value.type,lastModified:value.lastModified});
    else if(['[object Boolean]','[object Number]','[object String]','[object BigInt]'].includes(tag))result=Object(value.valueOf());
    else fail();
    seen.set(value,result);return result;
  }
  const result=copy(value);
  for(const buffer of transferred)buffer.transfer();
  return result;
}'''


def clone_api(scope, interface_name, value, options):
    """Clone in the calling global's own realm, retaining cycles and buffer aliases."""
    from ...foundation.arguments import missing_error
    if value is MISSING:
        raise missing_error(interface_name, 'structuredClone', 1, 0, constructor=False)
    settings=dictionary(options,name='StructuredSerializeOptions',
                        prefix=f"Failed to execute 'structuredClone' on '{interface_name}': ")
    transfer=sequence(settings.get('transfer',()))
    function=getattr(scope,'_structured_clone_function',None)
    if function is None:
        function=scope._structured_clone_function=scope._services.realm_services.evaluate_script(_CLONE_API)
    return function.call(None,value,transfer)
