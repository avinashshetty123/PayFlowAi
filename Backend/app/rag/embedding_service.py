"""Local, deterministic text embeddings (feature hashing).

No external embedding API is required, so RAG works offline and is fully
reproducible. The vectors are stored in pgvector when available.
"""

import hashlib
import math
import re

EMBEDDING_DIM = 256
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({"the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "was", "is", "by", "with", "but", "not"})


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


def features(text: str) -> list[str]:
    tokens = tokenize(text)
    bigrams = [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    return tokens + bigrams


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.md5(feature.encode("utf-8")).digest()  # noqa: S324 - hashing, not security
    index = int.from_bytes(digest[:4], "little") % EMBEDDING_DIM
    sign = 1.0 if digest[4] & 1 else -1.0
    return index, sign


def embed(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    for feature in features(text):
        index, sign = _bucket(feature)
        # Bigrams carry more signal ("ledger_failed" vs "ledger").
        vector[index] += sign * (1.5 if "_" in feature else 1.0)
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [round(v / norm, 6) for v in vector]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def keyword_similarity(a: str, b: str) -> float:
    sa, sb = set(features(a)), set(features(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def to_pgvector(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"
