"""``devicemotion`` and ``deviceorientation``: readings with no sensor behind them.

Chrome starts delivering as soon as a page listens for one of these types, and the
first reading carries nothing: every field is null and only ``interval`` says
something.  Nothing here moves either, so that empty shape is the whole payload -- what
a page can observe is the event and the fields it promises.
"""

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, boolean, dictionary, number
from .events import Event, event_constructor_arguments, event_init

__all__ = [
    "DeviceMotionEvent", "DeviceMotionEventAcceleration", "DeviceMotionEventRotationRate",
    "DeviceOrientationEvent",
]


#: The fields of each reading, in the order the specification lists them.
ACCELERATION_FIELDS = ("x", "y", "z")
ROTATION_FIELDS = ("alpha", "beta", "gamma")


#: What Chrome exposes only on a secure origin.
INSECURE_ORIGIN_INTERFACES = (
    "DeviceMotionEvent", "DeviceMotionEventAcceleration", "DeviceMotionEventRotationRate",
    "DeviceOrientationEvent",
)


def _reading(fields, source):
    """One reading object: the fields of *source*, null where it names none."""
    values = dictionary(source) if isinstance(source, pyv8.JSObject) else (source or {})
    return {field: values.get(field) for field in fields}


def _field(value):
    """Chrome reports null for a reading it does not have."""
    return pyv8.Null if value is None else value


@interface('DeviceMotionEventAcceleration', None)
class DeviceMotionEventAcceleration:
    """One acceleration reading; every field is null without a sensor."""

    def __init__(self, x=None, y=None, z=None, **_):
        self._values = dict(zip(ACCELERATION_FIELDS, (x, y, z)))

    def get_x(self): return _field(self._values["x"])
    def get_y(self): return _field(self._values["y"])
    def get_z(self): return _field(self._values["z"])


@interface('DeviceMotionEventRotationRate', None)
class DeviceMotionEventRotationRate:
    """One rotation-rate reading, with the same all-null shape."""

    def __init__(self, alpha=None, beta=None, gamma=None, **_):
        self._values = dict(zip(ROTATION_FIELDS, (alpha, beta, gamma)))

    def get_alpha(self): return _field(self._values["alpha"])
    def get_beta(self): return _field(self._values["beta"])
    def get_gamma(self): return _field(self._values["gamma"])


@interface('DeviceMotionEvent', 'Event')
class DeviceMotionEvent(Event):
    """``devicemotion``: the reading a page subscribes to for device movement."""

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('DeviceMotionEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.acceleration = DeviceMotionEventAcceleration(
            **_reading(ACCELERATION_FIELDS, init.get("acceleration")))
        self.accelerationIncludingGravity = DeviceMotionEventAcceleration(
            **_reading(ACCELERATION_FIELDS, init.get("accelerationIncludingGravity")))
        self.rotationRate = DeviceMotionEventRotationRate(
            **_reading(ROTATION_FIELDS, init.get("rotationRate")))
        self.interval = number(init.get("interval", 0))

    def get_acceleration(self): return self.acceleration
    def get_accelerationIncludingGravity(self): return self.accelerationIncludingGravity
    def get_rotationRate(self): return self.rotationRate
    def get_interval(self): return self.interval

    def fn_requestPermission(self, *_extra):
        """Nothing gates the sensor here, so the answer is the granted one."""
        return request_permission(self)


@interface('DeviceOrientationEvent', 'Event')
class DeviceOrientationEvent(Event):
    """``deviceorientation``: the reading a page subscribes to for device posture."""

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('DeviceOrientationEvent', event_type,
                                                         options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.alpha = init.get("alpha")
        self.beta = init.get("beta")
        self.gamma = init.get("gamma")
        self.absolute = boolean(init.get("absolute", False))

    def get_alpha(self): return _field(self.alpha)
    def get_beta(self): return _field(self.beta)
    def get_gamma(self): return _field(self.gamma)
    def get_absolute(self): return self.absolute

    def fn_requestPermission(self, *_extra):
        """Nothing gates the sensor here, so the answer is the granted one."""
        return request_permission(self)


def request_permission(event):
    """A resolved ``"granted"`` promise, the answer Chrome gives where it asks."""
    services = getattr(event, "_services", None)
    realm_services = getattr(services, "realm_services", None)
    if realm_services is None:
        return pyv8.Undefined
    promise = realm_services.make_promise()
    realm_services.promise_resolve(promise, "granted")
    return promise
