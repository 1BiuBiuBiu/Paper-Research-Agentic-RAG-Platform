"""Sparse statistics are computed once at index time and merged per selected set."""

import json

import pytest

from app.domain.qa import Chunk
from app.retrieval.bm25 import BM25Library, build_bm25, load_bm25, tokens, write_bm25


def chunk(identifier: str, text: str) -> Chunk:
    return Chunk(id=identifier, page=1, text=text, block_ids=[], boxes=[])


def test_build_records_tf_length_df_and_postings():
    terms = build_bm25(
        [chunk("a", "Aurora wavelength method"), chunk("b", "Nebula wavelength result")]
    )
    assert terms["chunks"][0]["length"] == 3
    assert terms["chunks"][0]["tf"]["wavelength"] == 1
    assert terms["df"]["wavelength"] == 2 and terms["df"]["aurora"] == 1
    assert terms["postings"]["wavelength"] == [[0, 1], [1, 1]]
    assert terms["length_sum"] == 6 and terms["size_bytes"] > 0
    assert terms["version"] == "bm25-v1"


def test_statistics_are_persisted_and_reloaded(tmp_path):
    terms = build_bm25([chunk("a", "Aurora wavelength method")])
    path = tmp_path / "bm25.json"
    write_bm25(path, terms)
    loaded = load_bm25(path)
    assert loaded == terms


def test_old_or_broken_statistics_are_rejected(tmp_path):
    path = tmp_path / "bm25.json"
    path.write_text(json.dumps({"version": "bm25-v0"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_bm25(path)


def test_idf_is_merged_from_the_selected_papers_only():
    aurora = build_bm25([chunk("a", "Aurora wavelength method")])
    nebula = build_bm25([chunk("b", "Nebula wavelength result")])
    single = BM25Library([aurora])
    merged = BM25Library([aurora, nebula])
    # "wavelength" appears in both papers, so its library-wide IDF must drop when merged.
    assert single.inverse_document_frequency("wavelength") > merged.inverse_document_frequency(
        "wavelength"
    )
    assert merged.document_frequency("wavelength") == 2
    assert merged.inverse_document_frequency("absent") == 0.0


def test_scores_only_chunks_listed_under_the_question_terms():
    aurora = build_bm25([chunk("a", "Aurora wavelength method"), chunk("b", "unrelated text")])
    nebula = build_bm25([chunk("c", "Nebula wavelength result")])
    library = BM25Library([aurora, nebula])
    scores = library.top_k("wavelength", 60)
    assert set(scores) == {(0, 0), (1, 0)}
    assert all(score > 0 for score in scores.values())
    assert library.top_k("", 60) == {}


def test_top_k_is_global_across_the_union():
    left = build_bm25([chunk(f"a{i}", "aurora aurora aurora") for i in range(5)])
    right = build_bm25([chunk(f"b{i}", "aurora") for i in range(5)])
    library = BM25Library([left, right])
    ranked = library.top_k("aurora", 3)
    assert len(ranked) == 3
    # Higher term frequency in the left paper wins regardless of paper order.
    assert [paper for paper, _ in ranked] == [0, 0, 0]


def test_chinese_questions_are_tokenized_into_bigrams():
    assert "缺失" in tokens("缺失率")
    assert tokens("the method") == ["method"]
