from .base import VendorAdapter


class SiemensAdapter(VendorAdapter):
    """Siemens extension point; no unverified CSA/private fields are inferred."""

    name = "siemens"
