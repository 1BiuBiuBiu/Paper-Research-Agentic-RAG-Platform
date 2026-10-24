"""Chunk windows: paragraphs stay whole up to 800 chars, long ones split on sentences."""

import re

from app.retrieval.chunks import CHUNK_SIZE, OVERLAP_MAX, sentences, windows

SENTENCE = re.compile(r"[^.!?]+[.!?]")


def test_short_paragraph_is_one_chunk():
    text = "Short paragraph. Two sentences only."
    assert windows(text) == [text]


def test_long_paragraph_splits_without_cutting_sentences():
    text = " ".join(f"Sentence number {index} stays whole." for index in range(80))
    chunks = windows(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= CHUNK_SIZE for chunk in chunks)
    for chunk in chunks:
        # every chunk is a contiguous slice of the paragraph, so no word is cut in half
        assert chunk in text
    assert all(chunk.endswith(".") for chunk in chunks)


def test_overlap_is_one_sentence_and_under_the_character_limit():
    text = " ".join(f"Sentence number {index} stays whole." for index in range(80))
    chunks = windows(text)
    first = SENTENCE.findall(chunks[0])
    overlap = first[-1].strip()
    assert len(overlap) < 150
    assert chunks[1].startswith(overlap)


def test_overlong_single_sentence_is_hard_split():
    text = "x" * 2000
    chunks = windows(text)
    assert all(len(chunk) <= CHUNK_SIZE for chunk in chunks)
    assert "".join(chunks) == text


def test_sentences_keeps_delimiters_and_drops_blanks():
    assert sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]


def test_overlap_limit_constant_is_under_150():
    assert OVERLAP_MAX < 150
