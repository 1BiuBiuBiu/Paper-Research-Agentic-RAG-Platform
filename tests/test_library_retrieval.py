"""One index over the selected papers: global top 60, no per-paper depth and no floor."""

from app.retrieval.search import fuse, library_candidates
from tests.qa_helpers import qa_client, seed_document


def build(client, *documents):
    service = client.app.state.indexing
    for document in documents:
        service.build(document.id)
    return [service.require(document) for document in documents]


def test_unrelated_paper_contributes_nothing_to_the_global_top_k(settings):
    client, _, _ = qa_client(settings)
    with client:
        repo = client.app.state.repository
        big = seed_document(repo, "Aurora wavelength method result. " * 2000)
        noise = seed_document(repo, "NEBULA_ONLY observation without the searched terms. " * 40)
        manifests = build(client, big, noise)
        service = client.app.state.indexing
        documents = [repo.get(big.id), repo.get(noise.id)]
        candidates = library_candidates(service, documents, manifests, "Aurora wavelength", 60)
        selected = fuse(candidates, 60)
        assert len(selected) == 60
        assert {item.document_id for item in selected} == {big.id}
        assert all("NEBULA_ONLY" not in item.text for item in selected)


def test_more_than_twenty_chunks_survive_from_one_paper(settings):
    client, _, _ = qa_client(settings)
    with client:
        repo = client.app.state.repository
        paper = seed_document(repo, "Aurora wavelength method result. " * 1200)
        manifest = build(client, paper)[0]
        assert len(manifest.chunks) > 20
        service = client.app.state.indexing
        candidates = library_candidates(
            service, [repo.get(paper.id)], [manifest], "Aurora wavelength", 60
        )
        selected = fuse(candidates, 60)
        # The old per-paper depth of 20 would have capped this at 20 rows.
        assert 20 < len(selected) <= 60


def test_global_top_k_spans_every_selected_paper(settings):
    client, _, _ = qa_client(settings)
    with client:
        repo = client.app.state.repository
        aurora = seed_document(repo, "Aurora wavelength method result. " * 30)
        nebula = seed_document(repo, "Aurora wavelength comparison from Nebula. " * 30)
        manifests = build(client, aurora, nebula)
        service = client.app.state.indexing
        documents = [repo.get(aurora.id), repo.get(nebula.id)]
        candidates = library_candidates(service, documents, manifests, "Aurora wavelength", 60)
        selected = fuse(candidates, 60)
        assert {item.document_id for item in selected} == {aurora.id, nebula.id}


def test_repeated_queries_reuse_the_cached_index(settings):
    client, embedder, _ = qa_client(settings)
    with client:
        repo = client.app.state.repository
        paper = seed_document(repo, "Aurora wavelength method result.")
        manifest = build(client, paper)[0]
        service = client.app.state.indexing
        documents = [repo.get(paper.id)]
        encodes = len(embedder.calls)
        for _ in range(3):
            library_candidates(service, documents, [manifest], "Aurora wavelength", 60)
        assert len(embedder.calls) == encodes + 3  # only the question is encoded
        assert service.cache.stats()["entries"] == 1
