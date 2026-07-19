from __future__ import annotations

import hashlib
from pathlib import Path


def recording_id(path: Path, root: Path) -> str:
    """Return a stable opaque ID without leaking the original file name."""
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    digest = hashlib.sha256(relative.encode("utf-8", errors="surrogatepass")).hexdigest()
    return f"rec_{digest[:20]}"


def safe_error(exc: BaseException) -> str:
    """Return only the exception type; messages may contain confidential paths."""
    return type(exc).__name__

