"""In-memory stand-ins for the parts of Firestore and Cloud Storage the services use.

Unit tests run on these, so they need neither emulators nor a Google Cloud project.
They copy the behaviour the code relies on: create() refuses an existing document,
set(merge=True) keeps other fields, and a missing object raises NotFound.
"""
from __future__ import annotations

import copy

from google.api_core.exceptions import AlreadyExists, NotFound


class Snapshot:
    def __init__(self, doc_id: str, data: dict | None) -> None:
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict | None:
        return copy.deepcopy(self._data)


class Document:
    def __init__(self, store: dict, doc_id: str) -> None:
        self._store = store
        self.id = doc_id

    def get(self) -> Snapshot:
        entry = self._store.get(self.id)
        return Snapshot(self.id, entry["data"] if entry else None)

    def set(self, data: dict, merge: bool = False) -> None:
        entry = self._store.setdefault(self.id, {"data": None, "collections": {}})
        if merge and entry["data"] is not None:
            entry["data"].update(copy.deepcopy(data))
        else:
            entry["data"] = copy.deepcopy(data)

    def create(self, data: dict) -> None:
        if self._store.get(self.id, {}).get("data") is not None:
            raise AlreadyExists(f"document {self.id} already exists")
        self.set(data)

    def collection(self, name: str) -> "Collection":
        entry = self._store.setdefault(self.id, {"data": None, "collections": {}})
        return Collection(entry["collections"].setdefault(name, {}))


class Collection:
    def __init__(self, store: dict) -> None:
        self._store = store
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None

    def document(self, doc_id: str) -> Document:
        return Document(self._store, doc_id)

    def order_by(self, field: str, direction: str = "ASCENDING") -> "Collection":
        query = Collection(self._store)
        query._order = (field, direction == "DESCENDING")
        query._limit = self._limit
        return query

    def limit(self, count: int) -> "Collection":
        query = Collection(self._store)
        query._order = self._order
        query._limit = count
        return query

    def stream(self):
        rows = [Snapshot(doc_id, entry["data"]) for doc_id, entry in self._store.items() if entry["data"] is not None]
        if self._order:
            field, descending = self._order
            rows.sort(key=lambda row: row.to_dict()[field], reverse=descending)
        return iter(rows[: self._limit] if self._limit is not None else rows)


class Firestore:
    def __init__(self) -> None:
        self._collections: dict[str, dict] = {}

    def collection(self, name: str) -> Collection:
        return Collection(self._collections.setdefault(name, {}))


class Blob:
    def __init__(self, bucket: "Bucket", name: str) -> None:
        self._bucket = bucket
        self.name = name
        self.content_type: str | None = None

    def upload_from_string(self, data, content_type: str | None = None) -> None:
        self._bucket.objects[self.name] = data.encode() if isinstance(data, str) else bytes(data)
        self._bucket.content_types[self.name] = content_type

    def download_as_bytes(self) -> bytes:
        if self.name not in self._bucket.objects:
            raise NotFound(f"gs://{self._bucket.name}/{self.name}")
        return self._bucket.objects[self.name]


class Bucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str | None] = {}

    def blob(self, name: str) -> Blob:
        return Blob(self, name)


class Storage:
    def __init__(self) -> None:
        self._buckets: dict[str, Bucket] = {}

    def bucket(self, name: str) -> Bucket:
        return self._buckets.setdefault(name, Bucket(name))
