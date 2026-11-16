"""Career knowledge base retrieval (vector RAG) - persistent Qdrant + fastembed.

Why this design (Batch 11)
--------------------------
The career KB used to live in an *in-process, ephemeral* Chroma index that was
rebuilt from scratch on every worker start. That works, but it is not a real,
persistent vector store: nothing survives a restart, and it can't be shared or
inspected outside the process.

This module now stores the KB in **Qdrant** - the SAME vector database the face
module already uses - and embeds text LOCALLY with **fastembed** (an ONNX
sentence-transformer). So:

  * Embeddings still run locally: no embedding API key, no per-call cost.
  * The index is PERSISTENT: written to Qdrant once and reused across restarts.
  * It is fully CONFIG-DRIVEN (same QDRANT_* env vars as face.py), so the exact
    same code runs against Qdrant Cloud, a local/Docker server, or an embedded
    on-disk store - switching environments is a `.env` change only.

Seeding is idempotent and version-gated: on first use we ensure the collection
exists and is populated for the current KB_VERSION; if the KB version or document
count changed, we re-seed. Steady-state startups do NO re-embedding.

Retrieval failures stay non-fatal: the mentor can still answer from the
student's verified profile, just without extra general guidance.

Location:
    E:\\campus-ai\\ai-worker\\rag.py
"""

from __future__ import annotations

import logging
import threading
import uuid
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from kb import CAREER_KB, KB_VERSION

logger = logging.getLogger("campus_ai.rag")


# ---------------------------------------------------------------------------
# Configuration (self-contained; reads the worker's .env directly, like face.py)
# ---------------------------------------------------------------------------
class RagSettings(BaseSettings):
    """RAG / vector-store settings, loaded from the ai-worker ``.env``.

    Kept self-contained (mirrors ``face.FaceSettings``) so this module owns its
    own config. The QDRANT_* vars are intentionally the SAME ones the face
    module reads, so a single Qdrant instance serves both features - each in its
    own collection.
    """

    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", protected_namespaces=()
    )

    # Vector store (shared with face.py) ------------------------------------
    # https://... (cloud) or http://... (server); empty => embedded on-disk.
    QDRANT_URL: str = ""
    QDRANT_API_KEY: str = ""  # required for Qdrant Cloud
    # Embedded on-disk path used ONLY when QDRANT_URL is empty. Deliberately a
    # DIFFERENT folder from the face store: two embedded QdrantClient(path=...)
    # instances in one process cannot open the same directory.
    RAG_QDRANT_LOCAL_PATH: str = "qdrant_rag_data"

    # KB collection + embedding model --------------------------------------
    RAG_COLLECTION: str = "career_kb"
    # Local ONNX sentence-transformer (same family Chroma used before). No key.
    RAG_EMBED_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def get_settings() -> RagSettings:
    """Return a cached settings instance (parsed once per process)."""
    return RagSettings()


# ---------------------------------------------------------------------------
# Qdrant client (lazy singleton, config-driven + seeded once)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_client = None  # populated + seeded on first use


# Fixed namespace -> deterministic, collision-free UUID point ids. Qdrant only
# accepts unsigned ints or UUIDs as point ids (NOT arbitrary strings), so we
# derive a stable UUID from the KB version + document id.
_ID_NAMESPACE = uuid.UUID("a3f1c2d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d")


def _versioned_ids() -> list[str]:
    """Stable, version-scoped UUID point ids (KB_VERSION bump => fresh ids)."""
    return [
        str(uuid.uuid5(_ID_NAMESPACE, f"{KB_VERSION}:{doc['id']}"))
        for doc in CAREER_KB
    ]


def _build_client():
    """Create the Qdrant client from .env (cloud / server / embedded)."""
    from qdrant_client import QdrantClient

    cfg = get_settings()
    url = cfg.QDRANT_URL.strip()
    if url:
        # Cloud or self-hosted server (HTTPS uses the API key).
        client = QdrantClient(url=url, api_key=cfg.QDRANT_API_KEY or None, timeout=30)
    else:
        # Embedded, on-disk store - no server required.
        client = QdrantClient(path=cfg.RAG_QDRANT_LOCAL_PATH)
    # Local ONNX embeddings power the high-level add()/query() helpers.
    client.set_model(cfg.RAG_EMBED_MODEL)
    return client


def _needs_seed(client, collection: str) -> bool:
    """True when the collection is missing or not populated for this KB version."""
    if not client.collection_exists(collection):
        return True
    try:
        return client.count(collection).count != len(CAREER_KB)
    except Exception:  # noqa: BLE001 - treat any count error as "reseed"
        return True


def _seed(client, collection: str) -> None:
    """(Re)build the collection for the current KB_VERSION (idempotent)."""
    if client.collection_exists(collection):
        client.delete_collection(collection)
    client.add(
        collection_name=collection,
        documents=[doc["text"] for doc in CAREER_KB],
        metadata=[
            {"topic": doc["topic"], "doc_id": doc["id"], "kb_version": KB_VERSION}
            for doc in CAREER_KB
        ],
        ids=_versioned_ids(),
    )
    logger.info(
        "Career KB seeded into Qdrant: %d docs (collection=%s, version=%s)",
        len(CAREER_KB),
        collection,
        KB_VERSION,
    )


def _get_client():
    """Build + seed (once, lazily) and return the Qdrant client."""
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            client = _build_client()
            collection = get_settings().RAG_COLLECTION
            if _needs_seed(client, collection):
                _seed(client, collection)
            else:
                logger.info(
                    "Career KB already present in Qdrant "
                    "(collection=%s, version=%s)",
                    collection,
                    KB_VERSION,
                )
            _client = client
    return _client


def retrieve(query: str, k: int = 3) -> list[str]:
    """Return up to ``k`` KB chunks most relevant to ``query``.

    Signature-compatible drop-in for the previous Chroma implementation, so
    callers (mentor.py) need no changes. Retrieval failures are swallowed and
    return an empty list - augmentation is best-effort.
    """
    try:
        cfg = get_settings()
        hits = _get_client().query(
            collection_name=cfg.RAG_COLLECTION,
            query_text=query,
            limit=k,
        )
        return [h.document for h in hits if getattr(h, "document", None)]
    except Exception as exc:  # noqa: BLE001 - best-effort augmentation
        logger.warning("KB retrieval failed: %s", exc)
        return []


def rag_status() -> dict:
    """Lightweight status for /health-style checks (never raises)."""
    cfg = get_settings()
    info: dict = {
        "backend": "qdrant",
        "collection": cfg.RAG_COLLECTION,
        "kb_version": KB_VERSION,
        "mode": "server" if cfg.QDRANT_URL.strip() else "embedded",
        "embed_model": cfg.RAG_EMBED_MODEL,
    }
    try:
        info["indexed"] = _get_client().count(cfg.RAG_COLLECTION).count
        info["ready"] = True
    except Exception as exc:  # noqa: BLE001 - status must never raise
        info["ready"] = False
        info["error"] = str(exc)
    return info
