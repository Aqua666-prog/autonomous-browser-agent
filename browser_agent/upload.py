from pathlib import Path

from .errors import BrowserActionError


def resolve_upload_path(root: str | None, relative_path: str, *, max_bytes: int = 25 * 1024 * 1024) -> Path:
    """Resolve an operator-approved upload path without exposing arbitrary filesystem access."""
    if not root:
        raise BrowserActionError('File upload is disabled; configure BROWSER_UPLOAD_ROOT')
    normalized = relative_path.replace('\\', '/')
    parts = normalized.split('/')
    if any(ord(c)<32 for c in relative_path) or normalized.startswith('/') or any(p in {'', '..'} for p in parts) or ':' in parts[0]:
        raise BrowserActionError('Upload path must be relative to the configured root')
    base = Path(root).expanduser().resolve()
    candidate = (base / normalized).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise BrowserActionError('Upload path escapes the configured upload root') from None
    if not candidate.is_file():
        raise BrowserActionError('Upload file does not exist under the configured upload root')
    try:
        size = candidate.stat().st_size
    except OSError:
        raise BrowserActionError('Upload file cannot be inspected') from None
    if size > max_bytes:
        raise BrowserActionError('Upload file exceeds the 25 MiB safety limit')
    return candidate
