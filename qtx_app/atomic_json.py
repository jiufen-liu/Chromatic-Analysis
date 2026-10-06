"""Replace a JSON document only after a complete, flushed temporary write."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def atomic_write_json(path: str | Path, payload, *, indent: int | None = 2) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=destination.parent,
                                         prefix='.chromatic-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False,
                      indent=indent, separators=(',', ':') if indent is None else None)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
