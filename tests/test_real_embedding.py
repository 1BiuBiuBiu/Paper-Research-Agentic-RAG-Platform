import os

import numpy as np
import pytest

from app.retrieval.embedding import BGEEmbedder


@pytest.mark.embedding
@pytest.mark.skipif(
    os.getenv("RUN_EMBEDDING_TESTS") != "1", reason="Requires fixed BGE-M3 snapshot"
)
def test_real_bge_multilingual_embeddings(settings):
    vectors = BGEEmbedder(settings).encode(
        [
            "The Aurora optical experiment measured an emission wavelength of 73 nanometers.",
            "恒星形成于分子气体云在引力作用下的坍缩。",
            "What wavelength did the Aurora optical experiment measure?",
            "恒星是如何形成的？",
            "极光光学实验测得的发射波长是多少？",
        ]
    )
    assert vectors.shape == (5, 1024)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4)
    assert vectors[2] @ vectors[0] > vectors[2] @ vectors[1]
    assert vectors[3] @ vectors[1] > vectors[3] @ vectors[0]
    assert vectors[4] @ vectors[0] > vectors[4] @ vectors[1]
