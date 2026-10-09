"""Per-document retrieval: BM25 (lexical + unit tokens) with optional BGE-M3 dense re-scoring."""

from __future__ import annotations

import logging
import re
import threading

import numpy as np
from rank_bm25 import BM25Okapi

log = logging.getLogger(__name__)
_TOK = re.compile(r"[a-z0-9À-ɏЀ-ӿ]+|[°%″′\"]")


def tokenize(text: str) -> list[str]:
    return _TOK.findall((text or "").lower())


class BlockIndex:
    def __init__(self, block_ids: list[str], texts: list[str]):
        self.block_ids = block_ids
        self.texts = texts
        self.bm25 = BM25Okapi([tokenize(t) or ["_"] for t in texts]) if texts else None

    def search(self, query: str, k: int = 8, allowed: set[str] | None = None) -> list[tuple[str, float]]:
        if self.bm25 is None:
            return []
        scores = self.bm25.get_scores(tokenize(query) or ["_"])
        order = np.argsort(-scores)
        out = []
        for i in order:
            if scores[i] <= 0:
                break
            bid = self.block_ids[i]
            if allowed is not None and bid not in allowed:
                continue
            out.append((bid, float(scores[i])))
            if len(out) >= k:
                break
        return out


class Embedder:
    """Lazy BGE-M3 (dense) embedder. Loaded once per process; CPU by default."""

    _lock = threading.Lock()
    _model = None

    def __init__(self, model_name: str = "BAAI/bge-m3", max_length: int = 512, batch_size: int = 16):
        self.model_name = model_name
        self.max_length = max_length
        self.batch_size = batch_size

    def _load(self):
        with Embedder._lock:
            if Embedder._model is None:
                from FlagEmbedding import BGEM3FlagModel

                Embedder._model = BGEM3FlagModel(self.model_name, use_fp16=False, devices=["cpu"])
        return Embedder._model

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)
        model = self._load()
        out = model.encode(texts, batch_size=self.batch_size, max_length=self.max_length, return_dense=True,
                           return_sparse=False, return_colbert_vecs=False)
        vecs = np.asarray(out["dense_vecs"], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        return vecs / norms
