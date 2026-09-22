"""Shared browser-layer exceptions used by every backend."""


class StaleRef(ValueError):
    """A semantic element ref is unknown, expired, detached, or changed."""


class BrowserActionError(RuntimeError):
    """A browser command failed in a recoverable way."""
