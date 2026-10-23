import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int = 50 * 1024 * 1024
    max_pages: int = 500
    parse_timeout: int = 300
    deepseek_api_key: str = field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    model_timeout: int = 90
    max_output_tokens: int = 2048
    context_bytes: int = 24000
    embedding_model: str = "BAAI/bge-m3"
    embedding_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    index_timeout: int = 600
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2"
    reranker_revision: str = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
    reranker_timeout: int = 120
    # Per-paper recall depth is an internal candidate budget, not a delivered quota.
    per_paper_candidates: int = 20
    # Upper bound for the fused library-level pool that reaches the reranker.
    candidates: int = 60
    rerank_top_n: int = 10
    agent_timeout: int = 600
    agent_max_calls: int = 7
    tavily_api_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        # Only the backend reads this file; process variables take precedence.
        local = dotenv_values(Path(__file__).resolve().parents[2] / ".env")

        def value(key: str, default: str = "") -> str:
            return os.getenv(key, local.get(key) or default)

        return cls(
            data_dir=Path(value("RESEARCH_DATA_DIR", "data")).resolve(),
            max_upload_bytes=int(value("RESEARCH_MAX_UPLOAD_MB", "50")) * 1024 * 1024,
            max_pages=int(value("RESEARCH_MAX_PAGES", "500")),
            parse_timeout=int(value("RESEARCH_PARSE_TIMEOUT", "300")),
            deepseek_api_key=value("DEEPSEEK_API_KEY"),
            deepseek_base_url=value("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model_timeout=int(value("DEEPSEEK_TIMEOUT", "90")),
            max_output_tokens=int(value("DEEPSEEK_MAX_OUTPUT_TOKENS", "2048")),
            context_bytes=int(value("RESEARCH_CONTEXT_BYTES", "24000")),
            embedding_revision=value(
                "RESEARCH_EMBEDDING_REVISION", "5617a9f61b028005a4858fdac845db406aefb181"
            ),
            index_timeout=int(value("RESEARCH_INDEX_TIMEOUT", "600")),
            per_paper_candidates=int(value("RESEARCH_PER_PAPER_CANDIDATES", "20")),
            candidates=int(value("RESEARCH_CANDIDATES", "60")),
            rerank_top_n=int(value("RESEARCH_RERANK_TOP_N", "10")),
            agent_max_calls=int(value("RESEARCH_AGENT_MAX_CALLS", "7")),
            tavily_api_key=value("TAVILY_API_KEY"),
        )
