"""
mocks/gcs_mock.py

Minimal in-memory stand-in for google.cloud.storage.Client. Supports the
surface used by line_webhook.py and the profile/plan generation tools:

    client.bucket(name)                         -> MockBucket
    bucket.blob(path)                           -> MockBlob
    bucket.list_blobs(prefix=..., delimiter=...) -> iterable of MockBlob
    blob.exists() / .download_as_text() / .upload_from_string()

Data lives in a per-client dict so tests stay isolated. Nothing touches disk
unless the caller asks — use MockGCSClient(persist_root=...) if a test wants
on-disk storage for inspection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


class MockBlob:
    def __init__(self, bucket: "MockBucket", name: str):
        self._bucket = bucket
        self.name = name

    def exists(self) -> bool:
        return self.name in self._bucket._store

    def download_as_text(self, encoding: str = "utf-8") -> str:
        if self.name not in self._bucket._store:
            raise FileNotFoundError(f"blob {self.name!r} not found")
        return self._bucket._store[self.name]

    def upload_from_string(self, data: str | bytes, content_type: str | None = None) -> None:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        self._bucket._store[self.name] = data
        if self._bucket._persist_root is not None:
            p = self._bucket._persist_root / self.name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data, encoding="utf-8")

    def delete(self) -> None:
        self._bucket._store.pop(self.name, None)


class MockBucket:
    def __init__(self, name: str, persist_root: Path | None = None):
        self.name = name
        self._store: dict[str, str] = {}
        self._persist_root = persist_root

    def blob(self, name: str) -> MockBlob:
        return MockBlob(self, name)

    def list_blobs(
        self,
        prefix: str | None = None,
        delimiter: str | None = None,
    ) -> Iterable[MockBlob]:
        for name in sorted(self._store):
            if prefix and not name.startswith(prefix):
                continue
            yield MockBlob(self, name)

    def seed(self, name: str, data: str | dict) -> None:
        """Pre-populate a blob. Dict is JSON-encoded."""
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False)
        self._store[name] = data

    def all_blobs(self) -> dict[str, str]:
        return dict(self._store)


class MockGCSClient:
    def __init__(self, persist_root: str | Path | None = None):
        self._buckets: dict[str, MockBucket] = {}
        self._persist_root = Path(persist_root) if persist_root else None

    def bucket(self, name: str) -> MockBucket:
        if name not in self._buckets:
            root = self._persist_root / name if self._persist_root else None
            self._buckets[name] = MockBucket(name, persist_root=root)
        return self._buckets[name]
