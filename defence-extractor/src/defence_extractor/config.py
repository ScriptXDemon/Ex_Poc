"""Runtime configuration (environment / .env driven)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ModelRoute(BaseModel):
    """How one logical model role is reached through the OpenAI-compatible gateway."""

    model: str
    providers: list[str] = Field(default_factory=list)
    allow_fallbacks: bool = True
    quantizations: list[str] = Field(default_factory=list)
    temperature: float = 0.2
    top_p: float | None = None
    max_tokens: int = 8000
    supports_seed: bool = True
    fallback_model: str | None = None  # tried when the model fails or is too slow (see Gateway.call)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    # --- OpenRouter / gateway -------------------------------------------------------
    # llm_api_flavor: "openrouter" (provider pinning, cost accounting, budget guard) or "openai" for self-hosted
    # OpenAI-compatible servers such as SGLang / vLLM in the datacenter (set OPENROUTER_BASE_URL to the server URL
    # and MAIN_MODEL / VERIFIER_MODEL to the served model names).
    llm_api_flavor: str = "openrouter"
    openrouter_api_key: str = Field("", validation_alias=AliasChoices("LLM_API_KEY", "OPENROUTER_API_KEY"))
    openrouter_base_url: str = Field("https://openrouter.ai/api/v1", validation_alias=AliasChoices("LLM_BASE_URL", "OPENROUTER_BASE_URL"))
    llm_stream: bool | None = None  # stream responses (default: on for self-hosted endpoints behind a proxy timeout)
    openrouter_budget_usd: float = 40.0
    budget_reserve_usd: float = 0.75
    request_timeout_s: float = 240.0
    llm_concurrency: int = 12
    llm_max_retries: int = 4
    llm_cache: bool = True
    # an endpoint that thinks even when asked not to (the GPU farm today) spends part of every answer's budget on
    # reasoning: this many completion tokens are added to each "reasoning off" call there (0: the switch works)
    thinking_overhead_tokens: int = 0
    max_tokens_ceiling: int = 32000  # no single call asks for more completion tokens than this
    # tokens one request may use at the endpoint (prompt + reasoning + answer); 0 = large enough for the defaults
    # (32k+). The farm serves 8192 today: answers are capped to what fits, and an input too large is split by the caller
    context_window: int = 0

    main_model: str = "qwen/qwen3.8-27b"
    main_providers: list[str] = Field(default_factory=lambda: ["DeepInfra", "AkashML", "Parasail", "Chutes"])
    main_quantizations: list[str] = Field(default_factory=lambda: ["bf16", "fp8"])
    verifier_model: str = "openai/gpt-oss-120b"
    verifier_providers: list[str] = Field(default_factory=lambda: ["DeepInfra", "AkashML", "DekaLLM", "Crusoe"])
    # verifier fallback: when VERIFIER_MODEL fails, is slower than fallback_min_tok_s after fallback_grace_s, or runs past
    # fallback_deadline_s, the call is repeated on VERIFIER_FALLBACK_MODEL; after fallback_after_failures failures in a
    # row the primary is skipped for fallback_cooldown_s, then tried again
    verifier_fallback_model: str = ""
    fallback_deadline_s: float = 420.0
    fallback_grace_s: float = 90.0
    fallback_min_tok_s: float = 4.0
    fallback_after_failures: int = 2
    fallback_cooldown_s: float = 900.0
    vision_model: str = "qwen/qwen3.8-27b"

    # --- paths ------------------------------------------------------------------------
    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    runs_dir: Path = PROJECT_ROOT / "runs"
    var_dir: Path = PROJECT_ROOT / "var"

    # --- retrieval / embeddings ----------------------------------------------------------
    embed_model: str = "BAAI/bge-m3"
    embed_enabled: bool = True
    embed_batch_size: int = 16
    embed_max_length: int = 512

    # --- pdf ------------------------------------------------------------------------------
    pdf_ocr: bool = True
    pdf_vlm_fallback: bool = False  # vision-model fallback for pages OCR cannot read (costs tokens)
    pdf_threads: int = 8
    pdf_window_pages: int = 40  # long PDFs are converted in windows of this many pages (no page limit)
    vision_enabled: bool = True  # page-level vision: pages without a text layer or mostly image
    vision_max_pages: int = 40  # per document
    vision_min_image_share: float = 0.35  # image area / page area that marks an image-heavy page ...
    vision_max_text_chars: int = 400  # ... when the page also has less text than this
    vision_max_figures: int = 30  # figures on text pages transcribed per document (largest / captioned first)
    figure_min_area: float = 0.06  # an image covering at least this share of its page counts as a figure

    # --- pipeline -------------------------------------------------------------------------
    section_chunk_tokens: int = 3500  # evidence budget for one discovery/audit call
    discovery_max_items: int = 70  # value items per discovery chunk (~70 items -> ~100 facts -> ~9k answer tokens)
    a2_window_sections: int = 150  # page-map outline sections per call
    a3_chunk_tokens: int = 6000  # evidence per entity-finding call
    a4_evidence_tokens: int = 5000  # evidence per role call
    a4_batch: int = 45  # registry entries given roles per call
    a6_batch: int = 40  # facts per attribution call
    a7_batch_blocks: int = 60  # blocks with references ("the system", "its") resolved per call
    audit_max_items: int = 120  # unmatched values (and extracted facts) listed per audit call
    max_recovery_rounds: int = 2
    chunk_concurrency: int = 6  # discovery chunks of one document in flight at once (raise when few documents run)
    doc_budget_usd: float = 0.40  # per-document LLM spend cap: beyond it no new discovery chunks / audit rounds start
    doc_budget_tokens: int = 0  # the same cap in completion tokens, for self-hosted endpoints that report no cost (0 = off)
    verify_batch_size: int = 25

    # --- temporal -------------------------------------------------------------------------
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue_ingest: str = "dx-ingest"
    temporal_task_queue_llm: str = "dx-llm"

    def route(self, role: str) -> ModelRoute:
        if role == "main":
            return ModelRoute(
                model=self.main_model,
                providers=self.main_providers,
                quantizations=self.main_quantizations,
                temperature=0.2,
                top_p=0.9,
                max_tokens=12000,
            )
        if role == "verifier":
            return ModelRoute(
                model=self.verifier_model, providers=self.verifier_providers, temperature=0.2, max_tokens=12000,
                fallback_model=self.verifier_fallback_model or None
            )
        if role == "vision":  # page images: any provider of the main model that accepts image input
            return ModelRoute(model=self.vision_model, providers=self.main_providers, temperature=0.1, max_tokens=8000)
        raise ValueError(f"unknown model role {role!r}")

    def dump_public(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        d.pop("openrouter_api_key", None)
        return d


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
