"""Helpers for validating user-supplied links."""
from urllib.parse import urlparse


def safe_url(url):
    """Return the stripped URL if it is a valid http(s) URL, otherwise None.

    This prevents ``javascript:`` and other unsafe schemes from reaching the frontend.
    """
    if not isinstance(url, str):
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return url.strip()
    return None


def safe_scheme(scheme):
    """Return the scheme if it is http or https, otherwise None."""
    return scheme if scheme in ("http", "https") else None
