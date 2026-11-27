from .base import VendorAdapter


class PhilipsAdapter(VendorAdapter):
    """Philips extension point; bindings require exact scanner model and version."""

    name = "philips"
