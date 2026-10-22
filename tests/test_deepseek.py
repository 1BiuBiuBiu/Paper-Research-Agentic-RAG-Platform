import asyncio
import json
import time
from dataclasses import replace

import httpx
import pytest

from app.core.errors import AppError
from app.providers.deepseek import DeepSeekProvider
from tests.qa_helpers import model_response


@pytest.mark.parametrize(
    "model,expected", [("flash", "deepseek-flash"), ("pro", "deepseek-v4-pro")]
)
def test_correct_model_endpoint_json_and_usage(settings, model, expected):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url == httpx.URL("https://api.deepseek.com/chat/completions")
        assert request.headers["authorization"] == "Bearer unit-test-credential"
        payload = json.loads(request.content)
        assert payload["model"] == expected and payload["stream"] is False
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["reasoning_effort"] == "none"
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["max_tokens"] == 2048
        return httpx.Response(200, json=model_response())

    provider = DeepSeekProvider(
        replace(settings, deepseek_api_key="unit-test-credential"), httpx.MockTransport(handler)
    )
    result = provider.complete(model, [{"role": "system", "content": "Output JSON"}])
    assert len(requests) == 1 and result.usage.total_tokens == 70
    assert provider.live_verified and "unit-test-credential" not in repr(provider.settings)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "model_auth_failed"),
        (402, "model_balance"),
        (403, "model_forbidden"),
        (429, "model_rate_limited"),
        (500, "model_request_failed"),
        (302, "model_request_failed"),
    ],
)
def test_http_failures_no_secret_echo_no_retry(settings, status, code, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"error": "unit-test-credential private server detail"},
            headers={"location": "https://attacker.invalid/"},
        )

    provider = DeepSeekProvider(
        replace(settings, deepseek_api_key="unit-test-credential"), httpx.MockTransport(handler)
    )
    with pytest.raises(AppError) as error:
        provider.complete("flash", [])
    assert error.value.code == code and "unit-test-credential" not in str(error.value)
    assert "private server detail" not in caplog.text and len(calls) == 1
    assert not provider.live_verified


def test_timeout_including_keepalive_deadline(settings):
    async def delayed(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=model_response())

    provider = DeepSeekProvider(
        replace(settings, deepseek_api_key="fake", model_timeout=0.01), httpx.MockTransport(delayed)
    )
    with pytest.raises(AppError) as error:
        provider.complete("pro", [])
    assert error.value.code == "model_timeout"


@pytest.mark.parametrize(
    "data,code",
    [
        ({"unexpected": "body"}, "invalid_model_response"),
        (model_response(finish="length"), "model_truncated"),
        (model_response(answer="unit-test-credential"), "invalid_model_response"),
    ],
)
def test_invalid_incomplete_or_secret_echo_rejected(settings, data, code):
    provider = DeepSeekProvider(
        replace(settings, deepseek_api_key="unit-test-credential"),
        httpx.MockTransport(lambda request: httpx.Response(200, json=data)),
    )
    with pytest.raises(AppError) as error:
        provider.complete("flash", [])
    assert error.value.code == code


def test_unknown_usage_not_reported_as_zero(settings):
    data = model_response()
    data.pop("usage")
    provider = DeepSeekProvider(
        replace(settings, deepseek_api_key="fake"),
        httpx.MockTransport(lambda request: httpx.Response(200, json=data)),
    )
    assert provider.complete("flash", []).usage.total_tokens is None


def test_config_missing_and_untrusted_base_url(settings):
    provider = DeepSeekProvider(settings)
    with pytest.raises(AppError) as error:
        provider.complete("flash", [])
    assert error.value.code == "model_not_configured"
    for url in [
        "http://api.deepseek.com",
        "https://user:password@api.deepseek.com",
        "https://api.deepseek.com?key=secret",
    ]:
        with pytest.raises(ValueError):
            DeepSeekProvider(replace(settings, deepseek_base_url=url))


def test_keepalive_stream_cannot_extend_total_deadline(settings):
    class KeepAlive(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                yield b" "
                await asyncio.sleep(0.01)

    provider = DeepSeekProvider(
        replace(settings, deepseek_api_key="fake", model_timeout=0.04),
        httpx.MockTransport(lambda request: httpx.Response(200, stream=KeepAlive())),
    )
    start = time.monotonic()
    with pytest.raises(AppError) as error:
        provider.complete("flash", [])
    assert error.value.code == "model_timeout" and time.monotonic() - start < 1
