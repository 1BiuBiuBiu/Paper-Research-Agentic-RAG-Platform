"""BM25 statistics computed once per paper at index time.

Chunk tokenisation, term frequencies, document frequency and the inverted index are
persisted with the index, so a query only tokenises the question, merges the document
frequencies of the papers currently in scope, then looks up the inverted lists.

IDF cannot be precomputed because it depends on which papers are selected for the turn:
one library over the selected papers means the corpus size and document frequencies are
the union of exactly those papers.
"""

import json
import math
import re
from pathlib import Path
from typing import TypedDict

from app.domain.qa import Chunk

STOP = set(
    (
        "the of and to is a in what how does are on this paper study that which "
        "from with an for was be or by it as"
    ).split()
)
BM25_VERSION = "bm25-v1"
K1 = 1.5
B = 0.75


def tokens(text: str) -> list[str]:
    result: list[str] = []
    for word in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            result.extend(word[i : i + 2] for i in range(max(1, len(word) - 1)))
        elif word not in STOP:
            result.append(word)
    return result


class ChunkTerms(TypedDict):
    length: int
    tf: dict[str, int]


class PaperTerms(TypedDict):
    version: str
    length_sum: int
    size_bytes: int
    chunks: list[ChunkTerms]
    df: dict[str, int]
    postings: dict[str, list[list[int]]]


def build_bm25(chunks: list[Chunk]) -> PaperTerms:
    """Tokenise every chunk once and record tf, df and the inverted index."""
    chunk_terms: list[ChunkTerms] = []
    document_frequency: dict[str, int] = {}
    postings: dict[str, list[list[int]]] = {}
    length_sum = 0
    for position, chunk in enumerate(chunks):
        words = tokens(chunk.text)
        counts: dict[str, int] = {}
        for word in words:
            counts[word] = counts.get(word, 0) + 1
        length_sum += len(words)
        chunk_terms.append({"length": len(words), "tf": counts})
        for word, frequency in counts.items():
            document_frequency[word] = document_frequency.get(word, 0) + 1
            postings.setdefault(word, []).append([position, frequency])
    data: PaperTerms = {
        "version": BM25_VERSION,
        "length_sum": length_sum,
        "size_bytes": 0,
        "chunks": chunk_terms,
        "df": document_frequency,
        "postings": postings,
    }
    data["size_bytes"] = len(encode(data))
    return data


def encode(data: PaperTerms) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_bm25(path: Path, data: PaperTerms) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(data))


def load_bm25(path: Path) -> PaperTerms:
    data: PaperTerms = json.loads(path.read_bytes())
    if (
        data.get("version") != BM25_VERSION
        or not isinstance(data.get("chunks"), list)
        or not isinstance(data.get("postings"), dict)
        or not isinstance(data.get("length_sum"), int)
    ):
        raise ValueError("Unsupported BM25 statistics")
    return data


class BM25Library:
    """One corpus over the union of the selected papers; only the question is tokenised."""

    def __init__(self, papers: list[PaperTerms]) -> None:
        self.papers = papers
        self.size = sum(len(paper["chunks"]) for paper in papers)
        self.average_length = (
            sum(paper["length_sum"] for paper in papers) / self.size if self.size else 0.0
        )

    def document_frequency(self, term: str) -> int:
        return sum(paper["df"].get(term, 0) for paper in self.papers)

    def inverse_document_frequency(self, term: str) -> float:
        frequency = self.document_frequency(term)
        if not frequency:
            return 0.0
        return math.log(1 + (self.size - frequency + 0.5) / (frequency + 0.5))

    def top_k(self, query: str, k: int) -> dict[tuple[int, int], float]:
        """Score only the chunks listed under the question's terms, then take the top k."""
        if k <= 0 or not self.size:
            return {}
        scores: dict[tuple[int, int], float] = {}
        for term in dict.fromkeys(tokens(query)):
            weight = self.inverse_document_frequency(term)
            if weight <= 0:
                continue
            for paper_index, paper in enumerate(self.papers):
                for position, frequency in paper["postings"].get(term, []):
                    length = paper["chunks"][position]["length"]
                    denominator = frequency + K1 * (
                        1 - B + B * (length / self.average_length if self.average_length else 0)
                    )
                    value = weight * frequency * (K1 + 1) / denominator
                    key = (paper_index, position)
                    scores[key] = scores.get(key, 0.0) + value
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:k]
        return {key: float(score) for key, score in ranked if score > 0}
