"""
LanceDB memory layer for Hermes inference backend.
Forces retrieval before response generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import os
import uuid
from typing import Any, Dict, List, Optional

import lancedb


class LanceDBUnavailableError(RuntimeError):
    pass


@dataclass
class LanceDBStatus:
    ready: bool
    instance_name: str
    uri: str
    table: str
    error: Optional[str] = None


class LanceDBMemory:
    def __init__(self, required: bool = True):
        self.required = required
        self.instance_name = os.getenv("LANCEDB_INSTANCE_NAME", "hermes-inference-memory-v1")
        data_dir = os.getenv("DATA_DIR", "./data")
        root = os.getenv("LANCEDB_ROOT_PATH", os.path.join(data_dir, "lancedb"))
        self.uri = os.getenv("LANCEDB_URI", os.path.join(root, self.instance_name))
        self.table_name = os.getenv("LANCEDB_TABLE", "inference_memory")
        self.embedding_dim = int(os.getenv("LANCEDB_EMBEDDING_DIM", "384"))

        self._db = None
        self._table = None
        self._init_error: Optional[str] = None

        self._ensure_ready()

    def _ensure_ready(self) -> None:
        if self._table is not None:
            return

        try:
            os.makedirs(self.uri, exist_ok=True)
            self._db = lancedb.connect(self.uri)

            try:
                self._table = self._db.open_table(self.table_name)
            except Exception:
                seed = {
                    "id": str(uuid.uuid4()),
                    "user_id": "system",
                    "role": "seed",
                    "source": "system",
                    "content": "Hermes inference memory initialized",
                    "created_at": self._utc_now(),
                    "vector": self._embed_text("Hermes inference memory initialized"),
                }
                self._table = self._db.create_table(self.table_name, data=[seed], mode="create")

            self._init_error = None
        except Exception as e:
            self._init_error = str(e)
            if self.required:
                raise LanceDBUnavailableError(f"LanceDB init failed: {self._init_error}")

    def status(self) -> LanceDBStatus:
        return LanceDBStatus(
            ready=self._table is not None,
            instance_name=self.instance_name,
            uri=self.uri,
            table=self.table_name,
            error=self._init_error,
        )

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load_sentence_transformer(self):
        """Lazy-load SentenceTransformer model (cached after first call)."""
        if not hasattr(self, "_st_model"):
            try:
                from sentence_transformers import SentenceTransformer
                self._st_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
            except Exception:
                self._st_model = None
        return self._st_model

    def _embed_text(self, text: str) -> List[float]:
        text = (text or "").strip()
        if not text:
            text = "empty"

        # Prefer real semantic embeddings via sentence-transformers
        model = self._load_sentence_transformer()
        if model is not None:
            try:
                vec = model.encode(text[:512], show_progress_bar=False, normalize_embeddings=True)
                return vec.tolist()
            except Exception:
                pass

        # Fallback: deterministic hash-based embedding (no semantic similarity)
        text_lower = text.lower()
        vec = [0.0] * self.embedding_dim
        tokens = text_lower.split()
        if not tokens:
            tokens = ["empty"]

        for token in tokens:
            h = hashlib.sha256(token.encode("utf-8")).hexdigest()
            n = int(h, 16)
            idx = n % self.embedding_dim
            sign = -1.0 if ((n >> 1) & 1) else 1.0
            weight = 1.0 + (len(token) % 7) * 0.1
            vec[idx] += sign * weight

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec

    def query_context(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        self._ensure_ready()
        if self._table is None:
            raise LanceDBUnavailableError(self._init_error or "LanceDB table unavailable")

        vector = self._embed_text(query)
        rows: List[Dict[str, Any]] = []

        try:
            rows = self._table.search(vector).limit(top_k).to_list()
        except Exception:
            try:
                rows = self._table.search(vector).limit(top_k).to_pandas().to_dict("records")
            except Exception as e:
                raise LanceDBUnavailableError(f"LanceDB query failed: {e}")

        usable = [r for r in rows if str(r.get("role", "")) != "seed"]
        snippets: List[str] = []
        for i, row in enumerate(usable[:top_k], 1):
            # Use up to 1500 chars for SCJN tesis (they contain full legal text)
            # Use 600 chars for everything else
            content = str(row.get("content", "")).strip()
            is_scjn = content.startswith("SCJN_PENAL_TESIS")
            max_chars = 1500 if is_scjn else 600
            snippets.append(f"[{i}] {content[:max_chars]}")

        context_block = "\n".join(snippets).strip()
        if not context_block:
            context_block = "No prior memory found in LanceDB for this query."

        return {
            "context": context_block,
            "hits": len(usable[:top_k]),
            "instance_name": self.instance_name,
            "table": self.table_name,
        }

    def query_context_hybrid(self, query: str, top_k: int = 10) -> Dict[str, Any]:
        """Hybrid search: combines semantic search with a SCJN-prefixed search.
        Used when the query is about tesis/jurisprudencia to ensure SCJN results surface.
        Returns merged results: top-k/2 from general + top-k/2 from SCJN-prefixed search.
        """
        self._ensure_ready()
        if self._table is None:
            raise LanceDBUnavailableError(self._init_error or "LanceDB table unavailable")

        half = max(top_k // 2, 3)

        # Pass 1: semantic search on original query
        general = self._raw_search(query, limit=top_k)

        # Pass 2: semantic search on SCJN-prefixed query to pull tesis into results
        scjn_query = f"SCJN_PENAL_TESIS {query}"
        scjn_rows = self._raw_search(scjn_query, limit=top_k)

        # Separate SCJN from non-SCJN in both result sets
        def is_scjn(r): return str(r.get("content", "")).startswith("SCJN_PENAL_TESIS")

        general_scjn = [r for r in general if is_scjn(r)]
        general_other = [r for r in general if not is_scjn(r)]
        scjn_extra = [r for r in scjn_rows if is_scjn(r)]

        # Merge: deduplicate by content prefix, take top-half of each
        seen_ids = set()
        merged = []

        def add_rows(rows, limit):
            for r in rows:
                key = str(r.get("content", ""))[:80]
                if key not in seen_ids and len(merged) < limit + len(merged):
                    seen_ids.add(key)
                    merged.append(r)

        # Prioritize SCJN results up to half quota
        scjn_combined = general_scjn + [r for r in scjn_extra if r not in general_scjn]
        add_rows(scjn_combined[:half], half)
        # Fill rest with non-SCJN
        add_rows(general_other, top_k - len(merged))
        # Top up with any remaining SCJN if slots available
        if len(merged) < top_k:
            add_rows(scjn_combined, top_k - len(merged))

        usable = [r for r in merged[:top_k] if str(r.get("role", "")) != "seed"]
        snippets = []
        for i, row in enumerate(usable, 1):
            content = str(row.get("content", "")).strip()
            is_scjn_row = content.startswith("SCJN_PENAL_TESIS")
            max_chars = 1500 if is_scjn_row else 600
            snippets.append(f"[{i}] {content[:max_chars]}")

        context_block = "\n".join(snippets).strip() or "No prior memory found in LanceDB for this query."
        return {
            "context": context_block,
            "hits": len(usable),
            "instance_name": self.instance_name,
            "table": self.table_name,
        }

    def _raw_search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Raw vector search, returns list of row dicts."""
        vector = self._embed_text(query)
        try:
            return self._table.search(vector).limit(limit).to_list()
        except Exception:
            try:
                return self._table.search(vector).limit(limit).to_pandas().to_dict("records")
            except Exception:
                return []

    def save_entry(self, content: str, role: str = "memory", user_id: str = "global", source: str = "chat") -> None:
        self._ensure_ready()
        if self._table is None:
            raise LanceDBUnavailableError(self._init_error or "LanceDB table unavailable")

        row = {
            "id": str(uuid.uuid4()),
            "user_id": user_id or "global",
            "role": role,
            "source": source,
            "content": content,
            "created_at": self._utc_now(),
            "vector": self._embed_text(content),
        }
        self._table.add([row])

    def save_interaction(self, user_id: str, user_message: str, assistant_reply: str) -> None:
        self.save_entry(content=f"User: {user_message}", role="user", user_id=user_id or "global", source="chat")
        self.save_entry(content=f"Assistant: {assistant_reply}", role="assistant", user_id=user_id or "global", source="chat")
