"""Create one Feature runtime per Browser."""

from ..engine.features import FeatureRuntime
from ..web.features.catalog import CATALOG


def bootstrap_runtime():
    """Return a fresh Browser-local FeatureRuntime.

    The catalog is process-shared; caches and loaded state belong to one Browser.
    """
    runtime = FeatureRuntime(CATALOG)
    runtime.preload_eager()
    return runtime


def validate_feature_catalog():
    """Development-time full validation without a Browser constructor flag."""
    runtime = bootstrap_runtime()
    runtime.validate()
    return runtime
