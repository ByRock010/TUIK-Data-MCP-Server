"""Optional multilingual text embeddings for hybrid search (#21, ADR 0005).

One pinned model embeds metadata documents at index time and queries at search
time, so the measured configuration is the served one. The ONNX runtime comes from
the optional `semantic` extra (fastembed); nothing here is imported by the base
server unless semantic search is configured.
"""

import threading
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

from .documents import METADATA_KINDS

MODEL = "intfloat/multilingual-e5-small"
DIMENSION = 384
# E5 models are trained with these role prefixes; omitting them degrades retrieval.
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
# Title first, then description/body: a bounded summary of each metadata record.
DOCUMENT_CHARS = 1000
BATCH_SIZE = 32
# fastembed defaults to the temporary directory; keep the model across reboots.
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "tuik-mcp" / "models"
# A missing runtime/model is not retried on every search.
FAILURE_TTL_SECONDS = 300


class EmbeddingUnavailable(Exception):
    """The optional runtime or the model files are missing or failed to load."""


def document_text(document: Mapping) -> str:
    parts = (document.get("title"), document.get("description"), document.get("body"))
    return " ".join(part for part in parts if part)[:DOCUMENT_CHARS]


def embeddable(document: Mapping) -> bool:
    # File content (#22) stays lexical: chunks are numerous and were not evaluated.
    return document.get("kind") in METADATA_KINDS


class Embedder:
    """Lazily loads the pinned model; thread-safe for the server's worker threads."""

    model = MODEL
    dimension = DIMENSION

    def __init__(self, *, cache_dir: str | None = None, local_files_only: bool = False):
        self.cache_dir = str(cache_dir or DEFAULT_CACHE_DIR)
        # The MCP server never downloads a model in the middle of a tool call.
        self.local_files_only = local_files_only
        self._model = None
        self._failure: tuple[float, EmbeddingUnavailable] | None = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            if self._failure and time.monotonic() - self._failure[0] < FAILURE_TTL_SECONDS:
                raise self._failure[1]
            try:
                self._model = self._create()
            except EmbeddingUnavailable as exc:
                self._failure = (time.monotonic(), exc)
                raise
            return self._model

    def _create(self):
        try:
            from fastembed import TextEmbedding
            from fastembed.common.model_description import ModelSource, PoolingType
        except ImportError as exc:
            raise EmbeddingUnavailable(
                "install the optional extra: pip install 'tuik-mcp[semantic]'"
            ) from exc
        try:
            if not any(m["model"] == MODEL for m in TextEmbedding.list_supported_models()):
                TextEmbedding.add_custom_model(
                    model=MODEL,
                    pooling=PoolingType.MEAN,
                    normalization=True,
                    sources=ModelSource(hf=MODEL),
                    dim=DIMENSION,
                    model_file="onnx/model.onnx",
                )
            return TextEmbedding(
                MODEL, cache_dir=self.cache_dir, local_files_only=self.local_files_only
            )
        except Exception as exc:
            hint = (
                "; run `tuik-mcp-index --embeddings` once (or set TUIK_EMBEDDING_CACHE_DIR "
                "to its cache) so the model files are available offline"
                if self.local_files_only
                else ""
            )
            raise EmbeddingUnavailable(f"{MODEL} could not be loaded ({exc}){hint}") from exc

    def _vectors(self, texts: Iterable[str]) -> list[list[float]]:
        model = self._load()
        try:
            vectors = [
                [float(x) for x in vector] for vector in model.embed(texts, batch_size=BATCH_SIZE)
            ]
        except Exception as exc:
            raise EmbeddingUnavailable(f"embedding failed ({exc})") from exc
        if any(len(vector) != DIMENSION for vector in vectors):
            raise EmbeddingUnavailable(f"{MODEL} returned vectors of an unexpected size")
        return vectors

    def documents(self, texts: list[str]) -> list[list[float]]:
        return self._vectors(PASSAGE_PREFIX + text for text in texts)

    def query(self, text: str) -> list[float]:
        return self._vectors([QUERY_PREFIX + text])[0]


def embed_payloads(payloads: list[dict], embedder: Embedder) -> int:
    """Add an `embedding` to each metadata payload in place; returns how many."""
    targets = [payload for payload in payloads if embeddable(payload)]
    if not targets:
        return 0
    vectors = embedder.documents([document_text(payload) for payload in targets])
    for payload, vector in zip(targets, vectors, strict=True):
        payload["embedding"] = vector
    return len(targets)
