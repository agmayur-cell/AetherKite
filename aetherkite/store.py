"""
Crash-safe JSON persistence.

- Writes go to a temp file, are fsynced, then atomically replace the target.
  The previous good copy is kept as `<name>.bak`.
- NaN/Infinity are rejected before anything reaches disk.
- A file that exists but can't be parsed raises StoreCorrupt instead of being
  silently replaced with defaults, so a crash can never wipe the journal,
  secrets or open positions.
"""

import copy
import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable


class StoreCorrupt(Exception):
    pass


class JsonStore:
    def __init__(self, path: Path, default_factory: Callable[[], Any]):
        self.path = Path(path)
        self.default_factory = default_factory

    def load(self) -> Any:
        if not self.path.exists():
            return copy.deepcopy(self.default_factory())
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f, parse_constant=_reject_constant)
        except (OSError, ValueError) as e:
            raise StoreCorrupt(
                f"{self.path.name} could not be read ({e}). The app will not overwrite it. "
                f"Fix the file or restore {self.path.name}.bak, then restart."
            ) from e

    def save(self, data: Any):
        text = json.dumps(data, indent=2, allow_nan=False)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
        os.replace(tmp, self.path)


def _reject_constant(name: str):
    raise ValueError(f"non-finite number {name} in file")
