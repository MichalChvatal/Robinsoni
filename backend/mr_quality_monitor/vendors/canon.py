from .base import VendorAdapter


class CanonAdapter(VendorAdapter):
    """Canon extension point; bindings require exact scanner model and version."""

    name = "canon"
