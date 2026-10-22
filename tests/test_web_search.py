import json

import httpx
import pytest

from app.core.errors import AppError
from app.providers.web_search import TavilySearch


def test_search_request_contains_only_bounded_query_and_returns_distinct_sources():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://example.org/a", "title": "Example", "content": "snippet"},
                    {"url": "javascript:bad", "content": "bad"},
                    {"url": "https://example.org/a", "content": "duplicate"},
                ]
            },
        )

    search = TavilySearch("synthetic-test-key", httpx.MockTransport(handler))
    results = search.search("query" * 100)
    assert len(results) == 1 and results[0].source_id == "W1"
    assert results[0].content_kind == "search_snippet" and results[0].retrieved_at
    request = seen[0]
    assert str(request.url) == "https://api.tavily.com/search"
    assert request.headers["authorization"] == "Bearer synthetic-test-key"
    body = json.loads(request.content)
    assert len(body["query"]) == 400 and body["max_results"] == 3
    assert body["include_answer"] is False and body["include_raw_content"] is False
    assert "synthetic-test-key" not in request.content.decode()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, text="synthetic-test-key"),
        httpx.Response(302, headers={"location": "https://other.example/"}),
        httpx.Response(200, text="bad json"),
        httpx.Response(
            200, json={"results": [{"url": "https://example.org", "content": "synthetic-test-key"}]}
        ),
        httpx.Response(200, content=b"x" * (1024 * 1024 + 1)),
    ],
)
def test_errors_are_bounded_and_do_not_echo_credentials(response):
    search = TavilySearch("synthetic-test-key", httpx.MockTransport(lambda request: response))
    with pytest.raises(AppError) as error:
        search.search("test")
    assert "synthetic-test-key" not in str(error.value)
