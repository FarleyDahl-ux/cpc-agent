"""Reads the approved data/ sources. Read-only: never writes to data/."""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from . import config


@dataclass
class LoadedFile:
    key: str
    filename: str
    path: Path
    present: bool
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    sha256: str | None = None


@dataclass
class LoadedSources:
    files: dict[str, LoadedFile]

    def get(self, key: str) -> LoadedFile:
        return self.files[key]

    def __getitem__(self, key: str) -> LoadedFile:
        return self.files[key]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_csv(key: str, filename: str, data_dir: Path) -> LoadedFile:
    path = data_dir / filename
    if not path.exists():
        return LoadedFile(key=key, filename=filename, path=path, present=False)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        rows = [dict(row) for row in reader]
    return LoadedFile(
        key=key,
        filename=filename,
        path=path,
        present=True,
        columns=list(columns),
        rows=rows,
        sha256=_hash_file(path),
    )


def load_approved_sources(data_dir: Path = config.DATA_DIR) -> LoadedSources:
    """Loads every file listed in config.ALL_FILES from data_dir.

    Missing files (required or optional) are represented as a LoadedFile
    with present=False rather than raising; validation.py decides what to
    do with that (HALT for required, LIMITATION for optional).
    """
    files: dict[str, LoadedFile] = {}
    for key, filename in config.ALL_FILES.items():
        files[key] = _load_csv(key, filename, data_dir)
    return LoadedSources(files=files)
