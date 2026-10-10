"""``DOMMatrix``: the 4x4 matrix CSS transforms are expressed in.

Values live in one flat, column-major list, which is the order ``toFloat32Array``
and the ``matrix3d()`` serialisation both use: ``m11, m12, m13, m14, m21, ...``.
"""
import math
import struct

import pyv8
from ...idl import interface
from ...foundation.arguments import MISSING, dictionary, is_nullish, missing_error, sequence
from ..page_errors import PageError
from ..page_errors import InvalidStateError, PageSyntaxError, PageTypeError
from .point import DOMPoint, double, point_init


#: ``name -> index`` for every member a matrix exposes, ``a..f`` included.
SLOTS = {
    "m11": 0, "m12": 1, "m13": 2, "m14": 3,
    "m21": 4, "m22": 5, "m23": 6, "m24": 7,
    "m31": 8, "m32": 9, "m33": 10, "m34": 11,
    "m41": 12, "m42": 13, "m43": 14, "m44": 15,
    "a": 0, "b": 1, "c": 4, "d": 5, "e": 12, "f": 13,
}
#: The four names ``DOMMatrix`` adds write access to, being plain numbers elsewhere.
WRITABLE = tuple(name for name in SLOTS if name not in ("is2D", "isIdentity"))
#: The positions a 2D matrix keeps at zero, and the two it keeps at one.
_ZERO_IN_2D = (2, 3, 6, 7, 8, 9, 11, 14)
_ONE_IN_2D = (10, 15)
#: Where the six values of the 2D serialisation sit in the flat list.
_TWO_D = (0, 1, 4, 5, 12, 13)

_MATRIX = "matrix"
_MATRIX3D = "matrix3d"


def identity():
    return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


#: The one numeric conversion the whole family reads its arguments with.
_number = double


def _text(value):
    """A matrix value written the way JavaScript writes a number."""
    value = float(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return repr(value)


def _multiply(left, right):
    result = [0.0] * 16
    for column in range(4):
        for row in range(4):
            result[column * 4 + row] = sum(
                left[index * 4 + row] * right[column * 4 + index] for index in range(4))
    return result


def _inverse(values):
    """The adjugate over the determinant, or NaN throughout when there is none."""
    rows = [[values[column * 4 + row] for column in range(4)] for row in range(4)]
    cofactors = [[0.0] * 4 for _ in range(4)]
    for row in range(4):
        for column in range(4):
            minor = [[rows[r][c] for c in range(4) if c != column]
                     for r in range(4) if r != row]
            cofactors[row][column] = ((-1) ** (row + column)) * _determinant(minor)
    determinant = sum(rows[0][column] * cofactors[0][column] for column in range(4))
    if determinant == 0 or determinant != determinant:
        return [float("nan")] * 16
    # The inverse is the transposed cofactor matrix over the determinant, and the
    # transpose is what turns row-major cofactors back into column-major values.
    return [cofactors[column][row] / determinant
            for column in range(4) for row in range(4)]


def _determinant(rows):
    size = len(rows)
    if size == 1:
        return rows[0][0]
    if size == 2:
        return rows[0][0] * rows[1][1] - rows[0][1] * rows[1][0]
    total = 0.0
    for column in range(size):
        minor = [[row[c] for c in range(size) if c != column] for row in rows[1:]]
        total += ((-1) ** column) * rows[0][column] * _determinant(minor)
    return total


def _is_2d(values):
    return all(values[index] == 0 for index in _ZERO_IN_2D) \
        and all(values[index] == 1 for index in _ONE_IN_2D)


def _rotation(x, y, z, angle):
    """The rotation of *angle* degrees about the (x, y, z) axis, as Rodrigues gives it."""
    length = math.sqrt(x * x + y * y + z * z)
    if length == 0:
        return identity()
    x, y, z = x / length, y / length, z / length
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    if angle % 90 == 0:
        # A quarter turn is exact in Chrome, where the trig carries its own noise.
        cosine, sine = float(round(cosine)), float(round(sine))
    one = 1 - cosine
    return [
        cosine + x * x * one, x * y * one + z * sine, x * z * one - y * sine, 0.0,
        y * x * one - z * sine, cosine + y * y * one, y * z * one + x * sine, 0.0,
        z * x * one + y * sine, z * y * one - x * sine, cosine + z * z * one, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]


def _around(core, origin):
    """*core* moved to act about *origin*."""
    ox, oy, oz = origin
    if ox == 0 and oy == 0 and oz == 0:
        return core
    return _multiply(_multiply(_translation(ox, oy, oz), core),
                     _translation(-ox, -oy, -oz))


def _float32(value):
    """What Chrome keeps of a translation or a scale, which it builds as a float."""
    return struct.unpack("f", struct.pack("f", float(value)))[0]


def _translation(x, y, z):
    values = identity()
    values[12], values[13], values[14] = _float32(x), _float32(y), _float32(z)
    return values


def _scaling(x, y, z):
    values = identity()
    values[0], values[5], values[10] = _float32(x), _float32(y), _float32(z)
    return values


def _is_matrix(value):
    return isinstance(value, DOMMatrixReadOnly)


def _init_values(init):
    """The six or sixteen values of a ``DOMMatrixInit``, and whether they are 2D."""
    if _is_matrix(init):
        return list(init._values()), init._is_2d
    options = {} if init is MISSING or is_nullish(init) else dictionary(init)
    values = identity()
    for name, index in SLOTS.items():
        if name in options:
            values[index] = _number(options[name])
    return values, _is_2d(values)


def _as_sequence(value):
    """The values of an iterable, or ``None`` for what Chrome parses as a string."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if not isinstance(value, pyv8.JSObject):
        return None
    try:
        return sequence(value, iterator_method=value._iterator_method())
    except (PageError, TypeError, ValueError):
        return None


def _array_values(array):
    """The numbers of a typed array, which reads by length and index."""
    return [array[index] for index in range(int(array.length))]


def _parse_text(init):
    """What Chrome turns a non-sequence constructor argument into before parsing it."""
    if isinstance(init, bool):
        return "true" if init else "false"
    if init is None:
        return "null"
    if isinstance(init, (str, int, float)):
        return str(init)
    return "[object Object]"


def _sequence_values(source, length, prefix=""):
    """The six or sixteen numbers of a sequence, as ``fromFloat32Array`` reads one."""
    if length not in (6, 16):
        raise PageTypeError(f"{prefix} The sequence must contain 6 elements for a 2D matrix"
                          f" or 16 elements for a 3D matrix.")
    if length == 6:
        values = identity()
        for target, index in zip(_TWO_D, range(6)):
            values[target] = _number(source[index])
        return values, True
    return [_number(item) for item in source], False


def _parse(text):
    """A ``matrix()`` or ``matrix3d()`` string, or ``None`` when it is neither."""
    body = text.strip()
    lowered = body.lower()
    if lowered in ("none", ""):
        return identity(), True
    for keyword, count in ((_MATRIX3D, 16), (_MATRIX, 6)):
        if not lowered.startswith(keyword + "(") or not body.endswith(")"):
            continue
        parts = body[len(keyword) + 1:-1].split(",")
        if len(parts) != count:
            return None
        numbers = []
        for part in parts:
            try:
                numbers.append(float(part.strip()))
            except ValueError:
                return None
        if count == 6:
            values = identity()
            for target, number in zip(_TWO_D, numbers):
                values[target] = number
            return values, True
        return numbers, False
    return None


@interface('DOMMatrixReadOnly', None)
class DOMMatrixReadOnly:
    def __init__(self, init=MISSING, *_extra, **_):
        if init is MISSING or init is pyv8.Undefined:
            self._matrix, self._is_2d = identity(), True
            return
        if _is_matrix(init):
            self._matrix, self._is_2d = list(init._values()), init._is_2d
            return
        values = _as_sequence(init)
        if values is not None:
            self._matrix, self._is_2d = _sequence_values(
                values, len(values), prefix="Failed to construct 'DOMMatrix':")
            return
        # Everything else resolves as a string, so an object that is not iterable
        # parses as "[object Object]" and fails.
        text = _parse_text(init)
        parsed = _parse(text)
        if parsed is None:
            raise PageSyntaxError(f"Failed to construct 'DOMMatrix': Failed to parse '{text}'.")
        self._matrix, self._is_2d = parsed

    # -- storage ---------------------------------------------------------------
    def _values(self):
        return list(self._matrix)

    def _store(self, values, is_2d):
        self._matrix = list(values)
        self._is_2d = bool(is_2d) and _is_2d(self._matrix)
        return self

    def _apply(self, core, origin=(0.0, 0.0, 0.0), *, three_d=False):
        values = _multiply(self._matrix, _around(core, origin))
        self._store(values, self._is_2d and not three_d)
        return self

    # -- attributes ------------------------------------------------------------
    def get_is2D(self): return self._is_2d

    def get_isIdentity(self):
        return _is_2d(self._matrix) and all(
            value == expected for value, expected in zip(self._matrix, identity()))

    def _transform_point(self, x, y, z, w):
        values = self._matrix
        return DOMPoint(
            x * values[0] + y * values[4] + z * values[8] + w * values[12],
            x * values[1] + y * values[5] + z * values[9] + w * values[13],
            x * values[2] + y * values[6] + z * values[10] + w * values[14],
            x * values[3] + y * values[7] + z * values[11] + w * values[15])

    def fn_transformPoint(self, init=None, *_extra):
        x, y, z, w = point_init(init)
        return self._transform_point(x, y, z, w)

    def fn_toString(self, *_extra):
        if any(not math.isfinite(value) for value in self._matrix):
            raise InvalidStateError(
                "Failed to execute 'toString' on 'DOMMatrixReadOnly':"
                " DOMMatrix cannot be serialized with NaN or Infinity values.")
        if self._is_2d:
            return f"matrix({', '.join(_text(self._matrix[index]) for index in _TWO_D)})"
        return f"matrix3d({', '.join(_text(value) for value in self._matrix)})"

    def fn_toJSON(self, *_extra):
        payload = {name: self._matrix[index] for name, index in sorted(SLOTS.items())}
        payload["is2D"] = self._is_2d
        payload["isIdentity"] = self.get_isIdentity()
        return payload

    def fn_toFloat32Array(self, *_extra):
        return pyv8.JSTypeArray(self._matrix, pyv8.ArrayType.Float32Array)

    def fn_toFloat64Array(self, *_extra):
        return pyv8.JSTypeArray(self._matrix, pyv8.ArrayType.Float64Array)

    # -- transforms that answer with a new matrix ------------------------------
    def _copy(self):
        copy = DOMMatrixReadOnly()
        copy._matrix, copy._is_2d = self._values(), self._is_2d
        return copy

    def _with(self, core, origin=(0.0, 0.0, 0.0), *, three_d=False):
        copy = self._copy()
        copy._apply(core, origin, three_d=three_d)
        return copy

    def fn_translate(self, tx=MISSING, ty=MISSING, tz=MISSING, *_extra):
        z = _number(tz)
        return self._with(_translation(_number(tx), _number(ty), z), three_d=z != 0)

    def fn_scale(self, scale_x=MISSING, scale_y=MISSING, scale_z=MISSING,
                 origin_x=MISSING, origin_y=MISSING, origin_z=MISSING, *_extra):
        x = _number(scale_x, 1.0)
        y = x if scale_y is MISSING or scale_y is pyv8.Undefined else _number(scale_y, x)
        z = _number(scale_z, 1.0)
        origin = (_number(origin_x), _number(origin_y), _number(origin_z))
        return self._with(_scaling(x, y, z), origin, three_d=z != 1.0)

    def fn_scale3d(self, scale=MISSING, origin_x=MISSING, origin_y=MISSING,
                   origin_z=MISSING, *_extra):
        # Chrome's ``scale3d`` is one scale and an origin, not three scales.
        factor = _number(scale, 1.0)
        origin = (_number(origin_x), _number(origin_y), _number(origin_z))
        return self._with(_scaling(factor, factor, factor), origin, three_d=True)

    def fn_scaleNonUniform(self, scale_x=MISSING, scale_y=MISSING, scale_z=MISSING,
                           origin_x=MISSING, origin_y=MISSING, origin_z=MISSING, *_extra):
        x = _number(scale_x, 1.0)
        y = _number(scale_y, 1.0)
        z = _number(scale_z, 1.0)
        origin = (_number(origin_x), _number(origin_y), _number(origin_z))
        return self._with(_scaling(x, y, z), origin, three_d=z != 1.0)

    def fn_rotate(self, angle=MISSING, *_extra):
        return self._plain(_rotation(0.0, 0.0, 1.0, _number(angle)))

    def fn_rotateAxisAngle(self, x=MISSING, y=MISSING, z=MISSING, angle=MISSING, *_extra):
        return self._plain(_rotation(_number(x), _number(y), _number(z), _number(angle)))

    def fn_rotateFromVector(self, x=MISSING, y=MISSING, *_extra):
        return self._plain(_rotation(0.0, 0.0, 1.0,
                                     math.degrees(math.atan2(_number(y), _number(x)))))

    def fn_skewX(self, angle=MISSING, *_extra):
        return self._plain(_skew(_number(angle), 0.0))

    def fn_skewY(self, angle=MISSING, *_extra):
        return self._plain(_skew(0.0, _number(angle)))

    def _plain(self, core):
        values = _multiply(self._matrix, core)
        return self._copy()._store(values, self._is_2d)

    def fn_flipX(self, *_extra):
        return self._plain(_scaling(-1.0, 1.0, 1.0))

    def fn_flipY(self, *_extra):
        return self._plain(_scaling(1.0, -1.0, 1.0))

    def fn_multiply(self, other=MISSING, *_extra):
        values, is_2d = _init_values(other)
        return self._copy()._store(_multiply(self._matrix, values), self._is_2d and is_2d)

    def fn_inverse(self, *_extra):
        return self._copy()._store(_inverse(self._matrix), self._is_2d)

    # -- statics ---------------------------------------------------------------
    @staticmethod
    def fn_fromMatrix(_realm, init=None, *_extra):
        values, is_2d = _init_values(init)
        matrix = DOMMatrix()
        matrix._store(values, is_2d)
        return matrix

    @staticmethod
    def fn_fromFloat32Array(_realm, array=MISSING, *_extra):
        if array is MISSING:
            raise missing_error('DOMMatrix', 'fromFloat32Array', 1, 0, constructor=False)
        return _from_array(array, 'fromFloat32Array')

    @staticmethod
    def fn_fromFloat64Array(_realm, array=MISSING, *_extra):
        if array is MISSING:
            raise missing_error('DOMMatrix', 'fromFloat64Array', 1, 0, constructor=False)
        return _from_array(array, 'fromFloat64Array')


def _from_array(array, method):
    values = _array_values(array)
    return DOMMatrix()._store(*_sequence_values(
        values, len(values), prefix=f"Failed to execute '{method}' on 'DOMMatrix':"))


def _skew(x_angle, y_angle):
    values = identity()
    values[4] = math.tan(math.radians(x_angle))
    values[1] = math.tan(math.radians(y_angle))
    return values


def _getter(index):
    def read(self):
        return self._matrix[index]
    return read

for _name, _index in SLOTS.items():
    setattr(DOMMatrixReadOnly, f"get_{_name}", _getter(_index))
del _name, _index


@interface('DOMMatrix', 'DOMMatrixReadOnly')
class DOMMatrix(DOMMatrixReadOnly):
    # Declared: the baseline does not say they are writable, so ``set_<name>`` makes them so.

    def fn_translateSelf(self, tx=MISSING, ty=MISSING, tz=MISSING, *_extra):
        z = _number(tz)
        self._apply(_translation(_number(tx), _number(ty), z), three_d=z != 0)
        return self

    def fn_scaleSelf(self, scale_x=MISSING, scale_y=MISSING, scale_z=MISSING,
                     origin_x=MISSING, origin_y=MISSING, origin_z=MISSING, *_extra):
        x = _number(scale_x, 1.0)
        y = x if scale_y is MISSING or scale_y is pyv8.Undefined else _number(scale_y, x)
        z = _number(scale_z, 1.0)
        self._apply(_scaling(x, y, z),
                    (_number(origin_x), _number(origin_y), _number(origin_z)),
                    three_d=z != 1.0)
        return self

    def fn_scale3dSelf(self, scale=MISSING, origin_x=MISSING, origin_y=MISSING,
                       origin_z=MISSING, *_extra):
        factor = _number(scale, 1.0)
        self._apply(_scaling(factor, factor, factor),
                    (_number(origin_x), _number(origin_y), _number(origin_z)),
                    three_d=True)
        return self

    def fn_rotateSelf(self, angle=MISSING, *_extra):
        self._plain_apply(_rotation(0.0, 0.0, 1.0, _number(angle)))
        return self

    def fn_rotateAxisAngleSelf(self, x=MISSING, y=MISSING, z=MISSING, angle=MISSING, *_extra):
        self._plain_apply(_rotation(_number(x), _number(y), _number(z), _number(angle)))
        return self

    def fn_rotateFromVectorSelf(self, x=MISSING, y=MISSING, *_extra):
        self._plain_apply(_rotation(0.0, 0.0, 1.0,
                                    math.degrees(math.atan2(_number(y), _number(x)))))
        return self

    def fn_skewXSelf(self, angle=MISSING, *_extra):
        self._plain_apply(_skew(_number(angle), 0.0))
        return self

    def fn_skewYSelf(self, angle=MISSING, *_extra):
        self._plain_apply(_skew(0.0, _number(angle)))
        return self

    def _plain_apply(self, core):
        self._store(_multiply(self._matrix, core), self._is_2d)

    def fn_multiplySelf(self, other=MISSING, *_extra):
        values, is_2d = _init_values(other)
        self._store(_multiply(self._matrix, values), self._is_2d and is_2d)
        return self

    def fn_preMultiplySelf(self, other=MISSING, *_extra):
        values, is_2d = _init_values(other)
        self._store(_multiply(values, self._matrix), self._is_2d and is_2d)
        return self

    def fn_invertSelf(self, *_extra):
        self._store(_inverse(self._matrix), self._is_2d)
        return self

    def fn_setMatrixValue(self, text=MISSING, *_extra):
        if text is MISSING:
            raise missing_error('DOMMatrix', 'setMatrixValue', 1, 0, constructor=False)
        written = _parse_text(text)
        parsed = _parse(written)
        if parsed is None:
            raise PageSyntaxError(f"Failed to execute 'setMatrixValue' on 'DOMMatrix':"
                                f" Failed to parse '{written}'.")
        self._store(*parsed)
        return self


def _setter(index):
    def write(self, value):
        self._matrix[index] = _number(value)
        self._store(self._matrix, self._is_2d)
    return write

for _name, _index in SLOTS.items():
    setattr(DOMMatrix, f"set_{_name}", _setter(_index))
del _name, _index


__all__ = ["DOMMatrix", "DOMMatrixReadOnly", "SLOTS", "identity"]
