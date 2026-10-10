"""``DOMPoint``: one point, and the matrix transform applied to it."""
import pyv8
from ...idl import interface
from ...foundation.arguments import (
    MISSING,
    dictionary,
    interface_value,
    is_nullish,
    missing_error,
)


def double(value, default=0.0):
    """Web IDL ``unrestricted double``: an omitted argument takes the default."""
    if value is MISSING or value is pyv8.Undefined:
        return float(default)
    if value is None or value is pyv8.Null:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float("nan")


def point_init(value):
    """The four numbers of a ``DOMPointInit``, its defaults filled in."""
    if is_nullish(value):
        return 0.0, 0.0, 0.0, 1.0
    if isinstance(value, DOMPointReadOnly):
        return value._x, value._y, value._z, value._w
    options = dictionary(value)
    return (double(options.get("x", 0.0)), double(options.get("y", 0.0)),
            double(options.get("z", 0.0)), double(options.get("w", 1.0)))


@interface('DOMPointReadOnly', None)
class DOMPointReadOnly:
    def __init__(self, x=MISSING, y=MISSING, z=MISSING, w=MISSING, *_extra, **_):
        # Chrome reads the four numbers and nothing else: an object handed to the
        # first argument is a value that cannot be a number, not a dictionary.
        self._x = double(x)
        self._y = double(y)
        self._z = double(z)
        self._w = double(w, 1.0)

    def get_x(self): return self._x
    def get_y(self): return self._y
    def get_z(self): return self._z
    def get_w(self): return self._w

    def fn_toJSON(self, *_extra):
        return {"x": self._x, "y": self._y, "z": self._z, "w": self._w}

    def fn_matrixTransform(self, matrix=MISSING, *_extra):
        if matrix is MISSING:
            raise missing_error('DOMPointReadOnly', 'matrixTransform', 1, 0,
                                constructor=False)
        checked = interface_value(matrix, 'DOMMatrixReadOnly', 'DOMPointReadOnly',
                                  'matrixTransform', 1, nullable=False)
        values = checked._values()
        x, y, z, w = self._x, self._y, self._z, self._w
        return DOMPoint(
            x * values[0] + y * values[4] + z * values[8] + w * values[12],
            x * values[1] + y * values[5] + z * values[9] + w * values[13],
            x * values[2] + y * values[6] + z * values[10] + w * values[14],
            x * values[3] + y * values[7] + z * values[11] + w * values[15])

    @staticmethod
    def fn_fromPoint(_realm, init=None, *_extra):
        return DOMPoint(*point_init(init))


@interface('DOMPoint', 'DOMPointReadOnly')
class DOMPoint(DOMPointReadOnly):
    # Declared: the baseline does not say they are writable, so ``set_<name>`` makes them so.

    def set_x(self, value): self._x = double(value)
    def set_y(self, value): self._y = double(value)
    def set_z(self, value): self._z = double(value)
    def set_w(self, value): self._w = double(value)

__all__ = ["DOMPoint", "DOMPointReadOnly", "double", "point_init"]
