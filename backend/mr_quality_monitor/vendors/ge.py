from .base import VendorAdapter


class GEAdapter(VendorAdapter):
    """GE extension point; bindings require exact scanner model and version."""

    name = "ge"
