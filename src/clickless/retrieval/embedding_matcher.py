"""Configurations B/C preference matcher: Sentence-BERT cosine similarity. Owner: Manas.

score(preference, amenity) = cosine(embed(query), embed(document)), clipped to [0, 1] and rounded
to SCORE_DECIMALS. Query and document text come from retrieval/documents.py, shared with TF-IDF so
the component-level comparison is fair (proposal §IV-A).

Model and version (configs/retrieval.yaml):
  * `sbert_model` is the Hugging Face model id and `sbert_revision` the exact commit of that
    model. Both are required, so every run and every teammate uses the same weights.
  * `sbert_device` defaults to cpu: GPU/MPS kernels can change the last digits of embeddings.
  * Install with `pip install -e ".[nlp]"` (sentence-transformers + torch). The first use downloads
    the pinned revision (~90 MB for all-MiniLM-L6-v2) into the Hugging Face cache (`HF_HOME`,
    default ~/.cache/huggingface); after that, `HF_HUB_OFFLINE=1` runs without internet.
  * Nothing is loaded until the first score, so building a B/C pipeline stays cheap.

Stable scores:
  * The first score embeds every amenity in the store in one pass, in sorted document order, so an
    amenity's vector never depends on which candidates happened to be scored first. Queries are
    embedded one at a time. The same inputs give the same score whatever the call order.
  * Rounding hides float noise (~1e-7) between batch layouts and BLAS builds.

Caching:
  * In memory for the matcher's lifetime (query and document vectors).
  * On disk when `cache_dir` is set (the pipeline uses `sbert_cache_dir`, data/cache/sbert/,
    git-ignored): one .npz per (model, revision, device, encoding settings, exact set of amenity
    documents). The key hashes the documents themselves, so each snapshot -- and the fixtures --
    gets its own file and a stale cache is never read. Files are safe to delete at any time.

Open, shared with TF-IDF (decide on DEV, write an ADR): whether the amenity category is a hard
gate. Raw cosine gives unrelated categories small positive scores (about 0.15-0.45 with MiniLM).
The wheelchair rule is NOT applied here -- planning applies it (unknown never counts).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..contracts import Amenity, AmenityPreference
from .documents import amenity_document, preference_query

_LOG = logging.getLogger(__name__)

# texts -> one embedding row per text (any length; the matcher normalizes them).
Encoder = Callable[[list[str]], np.ndarray]

SCORE_DECIMALS = 4
BATCH_SIZE = 64
_CACHE_FORMAT = 1  # bump when the cache file layout or the vector normalization changes


class EmbeddingMatcher:
    def __init__(
        self,
        store=None,
        model_name: str = "",
        *,
        revision: str | None = None,
        device: str = "cpu",
        cache_dir: str | Path | None = None,
        encoder: Encoder | None = None,
    ) -> None:
        """`encoder` replaces the Sentence-BERT model (tests, experiments). The disk cache is keyed
        by `model_name`/`revision`, so give an injected encoder its own names or no `cache_dir`."""
        self.store = store
        self.model_name = model_name
        self.revision = revision
        self.device = device
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._encoder = encoder
        self._store_indexed = False
        self._document_vectors: dict[str, np.ndarray] = {}
        self._query_vectors: dict[str, np.ndarray] = {}

    def score(self, preference: AmenityPreference, amenity: Amenity) -> float:
        document = self._document_vector(amenity_document(amenity))
        query = self._query_vector(preference_query(preference))
        similarity = float(np.dot(query.astype(np.float64), document.astype(np.float64)))
        return round(min(1.0, max(0.0, similarity)), SCORE_DECIMALS)

    # ------------------------------------------------------------------ vectors
    def _query_vector(self, text: str) -> np.ndarray:
        vector = self._query_vectors.get(text)
        if vector is None:
            vector = self._query_vectors[text] = self._embed([text])[0]
        return vector

    def _document_vector(self, text: str) -> np.ndarray:
        if not self._store_indexed:
            self._index_store()
        vector = self._document_vectors.get(text)
        if vector is None:  # an amenity that is not in the store (ad hoc calls, tests)
            vector = self._document_vectors[text] = self._embed([text])[0]
        return vector

    def _index_store(self) -> None:
        amenities = getattr(self.store, "amenities", None) or []
        texts = sorted({amenity_document(amenity) for amenity in amenities})
        if texts:
            vectors = self._read_cache(texts)
            if vectors is None:
                vectors = self._embed(texts)
                self._write_cache(texts, vectors)
            self._document_vectors.update(zip(texts, vectors, strict=True))
        self._store_indexed = True

    def _embed(self, texts: list[str]) -> np.ndarray:
        """Unit-length float32 rows, one per text (a zero vector stays zero and scores 0)."""
        if self._encoder is None:
            self._encoder = self._load_model()
        raw = np.asarray(self._encoder(texts), dtype=np.float64)
        if raw.ndim != 2 or raw.shape[0] != len(texts):
            raise ValueError(f"encoder returned shape {raw.shape} for {len(texts)} texts")
        norms = np.linalg.norm(raw, axis=1, keepdims=True)
        unit = np.divide(raw, norms, out=np.zeros_like(raw), where=norms > 0)
        return unit.astype(np.float32)

    def _load_model(self) -> Encoder:
        if not self.model_name or not self.revision:
            raise ValueError(
                "Sentence-BERT matching needs a pinned model: set sbert_model and sbert_revision "
                "(a Hugging Face commit hash) in configs/retrieval.yaml"
            )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise ImportError(
                'Sentence-BERT matching (Configs B/C) needs the nlp extra: pip install -e ".[nlp]"'
            ) from error
        model = SentenceTransformer(self.model_name, revision=self.revision, device=self.device)

        def encode(texts: list[str]) -> np.ndarray:
            return model.encode(texts, batch_size=BATCH_SIZE, convert_to_numpy=True, show_progress_bar=False)

        return encode

    # ------------------------------------------------------------------ disk cache
    def _cache_path(self, texts: list[str]) -> Path | None:
        if self.cache_dir is None:
            return None
        settings = {
            "format": _CACHE_FORMAT,
            "model": self.model_name,
            "revision": self.revision,
            "device": self.device,
            "batch_size": BATCH_SIZE,
        }
        digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode())
        for text in texts:
            digest.update(b"\0" + text.encode())
        return self.cache_dir / f"{digest.hexdigest()}.npz"

    def _read_cache(self, texts: list[str]) -> np.ndarray | None:
        path = self._cache_path(texts)
        if path is None or not path.is_file():
            return None
        try:
            with np.load(path, allow_pickle=False) as cached:
                cached_texts = cached["texts"].tolist()
                vectors = cached["vectors"]
        except Exception:  # the cache is only an optimization: anything unreadable is recomputed
            _LOG.warning("ignoring unreadable embedding cache %s", path, exc_info=True)
            return None
        if cached_texts != texts or vectors.dtype != np.float32 or vectors.shape[0] != len(texts):
            return None
        return vectors

    def _write_cache(self, texts: list[str], vectors: np.ndarray) -> None:
        path = self._cache_path(texts)
        if path is None:
            return
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as f:
                np.savez(f, texts=np.array(texts, dtype=str), vectors=vectors)
            os.replace(tmp, path)  # readers never see a half-written file
        except OSError:
            _LOG.warning("could not write embedding cache %s", path, exc_info=True)
            tmp.unlink(missing_ok=True)
