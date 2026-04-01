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

    def _embed_text(self, text: str) -> List[float]:
        text = (text or "").strip().lower()
        if not text:
            text = "empty"

        vec = [0.0] * self.embedding_dim
        tokens = text.split()

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
            snippets.append(f"[{i}] {str(row.get('content', '')).strip()[:500]}")

        context_block = "\n".join(snippets).strip()
        if not context_block:
            context_block = "No prior memory found in LanceDB for this query."

        return {
            "context": context_block,
            "hits": len(usable[:top_k]),
            "instance_name": self.instance_name,
            "table": self.table_name,
        }

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
