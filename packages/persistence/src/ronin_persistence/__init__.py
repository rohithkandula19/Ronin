"""Ronin AI OS swappable document persistence.

One tiny interface (``DocumentStore``) with three backends — in-memory,
JSON-file (the default, byte-compatible with the legacy stores), and PostgreSQL
(JSONB) — so a store's code is identical whether it persists to a local file or
a managed database. Selection is centralized so a single env switch
(``RONIN_DATABASE_URL``) moves a deployment from files to Postgres.
"""

from .document_store import (
    DocumentStore,
    InMemoryDocumentStore,
    JsonFileDocumentStore,
    PostgresDocumentStore,
    from_env,
    open_document_store,
)

__all__ = [
    "DocumentStore",
    "InMemoryDocumentStore",
    "JsonFileDocumentStore",
    "PostgresDocumentStore",
    "from_env",
    "open_document_store",
]
