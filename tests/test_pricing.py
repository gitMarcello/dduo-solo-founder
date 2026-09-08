from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from dduo_solo_founder.pricing import (
    API_PRICING_VERSION,
    MODEL_PRICES,
    api_equivalent_cost,
    embedding_price,
    resolve_model_price,
)


def test_openai_cached_tokens_are_subsets_and_reasoning_is_not_double_charged():
    result = api_equivalent_cost(
        provider="codex",
        model="gpt-5.6-terra",
        input_tokens=200_000,
        cached_input_tokens=40_000,
        cache_write_input_tokens=20_000,
        output_tokens=10_000,
        reasoning_tokens=8_000,
    )
    assert result is not None
    # 140k base input + 40k cache read + 20k cache write + 10k output.
    assert result.cost_usd == Decimal("0.458")
    assert result.uncached_input_cost_usd == Decimal("0.28")
    assert result.cached_input_cost_usd == Decimal("0.008")
    assert result.cache_write_input_cost_usd == Decimal("0.05")
    assert result.output_cost_usd == Decimal("0.12")
    assert result.numeric_details() == {
        "uncached_input_cost_pico_usd": 280_000_000_000,
        "cached_input_cost_pico_usd": 8_000_000_000,
        "cache_write_input_cost_pico_usd": 50_000_000_000,
        "output_cost_pico_usd": 120_000_000_000,
    }
    assert result.unit_price_usd_per_million is None
    assert result.pricing_version == API_PRICING_VERSION
    assert result.canonical_provider == "openai"
    assert result.canonical_model == "gpt-5.6-terra"


def test_anthropic_input_cache_classes_are_distinct():
    result = api_equivalent_cost(
        provider="claude",
        model="claude-sonnet-4-6",
        input_tokens=100_000,
        cached_input_tokens=100_000,
        cache_write_input_tokens=100_000,
        output_tokens=100_000,
        reasoning_tokens=50_000,
        cache_write_ttl="5m",
    )
    assert result is not None
    assert result.cost_usd == Decimal("2.205")
    assert result.canonical_provider == "anthropic"

    one_hour = api_equivalent_cost(
        provider="claude",
        model="claude-sonnet-4-6",
        input_tokens=100_000,
        cached_input_tokens=100_000,
        cache_write_input_tokens=100_000,
        output_tokens=100_000,
        reasoning_tokens=50_000,
        cache_write_ttl="1h",
    )
    assert one_hour is not None
    assert one_hour.cost_usd == Decimal("2.43")


def test_missing_billable_classes_and_unknown_claude_cache_ttl_are_unavailable():
    assert (
        api_equivalent_cost(
            provider="codex",
            model="gpt-5.6-terra",
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=10,
        )
        is None
    )
    assert (
        api_equivalent_cost(
            provider="claude",
            model="claude-sonnet-4-6",
            input_tokens=100,
            cached_input_tokens=0,
            cache_write_input_tokens=1,
            output_tokens=10,
            cache_write_ttl="invalid",  # type: ignore[arg-type]
        )
        is None
    )
    assert (
        api_equivalent_cost(
            provider="claude",
            model="claude-sonnet-4-6",
            input_tokens=100,
            cached_input_tokens=0,
            cache_write_input_tokens=1,
            output_tokens=10,
        )
        is None
    )


def test_exact_model_catalog_uses_only_explicit_aliases_and_is_immutable():
    alias = resolve_model_price("openai", "gpt-5.6")
    assert alias is resolve_model_price("codex", "gpt-5.6-sol")
    haiku_alias = resolve_model_price("anthropic", "claude-haiku-4-5")
    assert haiku_alias is resolve_model_price("claude", "claude-haiku-4-5-20251001")
    sonnet_alias = resolve_model_price("anthropic", "claude-sonnet-4-5")
    assert sonnet_alias is resolve_model_price("claude", "claude-sonnet-4-5-20250929")
    assert resolve_model_price("claude", "claude-sonnet") is None
    assert resolve_model_price("claude", "Claude-Sonnet-4-6") is None
    assert resolve_model_price("unknown", "gpt-5.6-terra") is None
    assert resolve_model_price(None, "gpt-5.6-terra") is None
    assert resolve_model_price("codex", None) is None
    with pytest.raises(TypeError):
        MODEL_PRICES[("openai", "invented")] = alias  # type: ignore[index]


def test_inconsistent_or_unknown_usage_is_not_priced():
    assert (
        api_equivalent_cost(
            provider="codex",
            model="unknown",
            input_tokens=100,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            output_tokens=10,
        )
        is None
    )
    assert (
        api_equivalent_cost(
            provider="codex",
            model="gpt-5.6-terra",
            input_tokens=100,
            cached_input_tokens=101,
            cache_write_input_tokens=0,
            output_tokens=0,
        )
        is None
    )
    assert (
        api_equivalent_cost(
            provider="claude",
            model="claude-sonnet-4-6",
            input_tokens=100,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            output_tokens=5,
            reasoning_tokens=6,
        )
        is None
    )
    assert (
        api_equivalent_cost(
            provider="claude",
            model="claude-sonnet-4-6",
            input_tokens=-1,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            output_tokens=0,
        )
        is None
    )


def test_zero_usage_is_a_measured_zero_and_single_rate_is_exposed():
    zero = api_equivalent_cost(
        provider="codex",
        model="gpt-5.6-luna",
        input_tokens=0,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        output_tokens=0,
    )
    assert zero is not None and zero.cost_usd == Decimal(0)
    assert zero.unit_price_usd_per_million is None

    output_only = api_equivalent_cost(
        provider="codex",
        model="gpt-5.6-luna",
        input_tokens=0,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        output_tokens=1,
    )
    assert output_only is not None
    assert output_only.cost_usd == Decimal("0.0000012")
    assert output_only.unit_price_usd_per_million == Decimal("1.20")


def test_openai_long_context_prices_the_full_request_at_official_multipliers():
    at_threshold = api_equivalent_cost(
        provider="codex",
        model="gpt-5.6-terra",
        input_tokens=272_000,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        output_tokens=10_000,
    )
    above_threshold = api_equivalent_cost(
        provider="codex",
        model="gpt-5.6-terra",
        input_tokens=300_000,
        cached_input_tokens=60_000,
        cache_write_input_tokens=30_000,
        output_tokens=10_000,
        reasoning_tokens=5_000,
    )
    assert at_threshold is not None
    assert at_threshold.cost_usd == Decimal("0.664")
    assert above_threshold is not None
    # 210k base, 60k read and 30k write all receive the 2x input
    # multiplier; output receives 1.5x. Reasoning stays within output.
    assert above_threshold.cost_usd == Decimal("1.194")
    assert above_threshold.pricing_version == API_PRICING_VERSION


def test_catalog_rejects_usage_beyond_the_model_context_window():
    openai_limit = api_equivalent_cost(
        provider="codex",
        model="gpt-5.6-terra",
        input_tokens=1_000_000,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        output_tokens=50_000,
    )
    assert openai_limit is not None
    assert (
        api_equivalent_cost(
            provider="codex",
            model="gpt-5.6-terra",
            input_tokens=1_000_000,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            output_tokens=50_001,
        )
        is None
    )

    # Current 1M Claude models keep standard rates throughout their window.
    current_long_context = api_equivalent_cost(
        provider="claude",
        model="claude-sonnet-4-6",
        input_tokens=900_000,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        output_tokens=0,
    )
    assert current_long_context is not None
    assert current_long_context.cost_usd == Decimal("2.7")

    # Legacy 4.5 and Haiku 4.5 expose a 200k window, not a premium tier.
    assert (
        api_equivalent_cost(
            provider="claude",
            model="claude-sonnet-4-5-20250929",
            input_tokens=200_001,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            output_tokens=0,
        )
        is None
    )


def test_sonnet_5_cancelled_price_increase_keeps_one_immutable_snapshot():
    before = api_equivalent_cost(
        provider="claude",
        model="claude-sonnet-5",
        input_tokens=1_000,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        output_tokens=100,
        occurred_at=datetime(2026, 8, 31, 23, 59, 59, tzinfo=timezone.utc),
    )
    after = api_equivalent_cost(
        provider="claude",
        model="claude-sonnet-5",
        input_tokens=1_000,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        output_tokens=100,
        occurred_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert before is not None and before.cost_usd == Decimal("0.003")
    assert after is not None and after.cost_usd == Decimal("0.003")
    assert before.pricing_version == API_PRICING_VERSION
    assert after.pricing_version == API_PRICING_VERSION


def test_embedding_snapshot_remains_backward_compatible():
    assert embedding_price("text-embedding-3-large", 1_000_000) == (
        Decimal("0.13"),
        Decimal("0.13"),
        "openai-text-embedding-3-large-2026-08",
    )
    assert embedding_price("text-embedding-3-small", 1_000_000) == (None, None, None)
    assert embedding_price("text-embedding-3-large", -1) == (None, None, None)


def test_september_models_have_separate_prices_and_keep_legacy_snapshot():
    from dduo_solo_founder.pricing import SEPTEMBER_API_PRICING_VERSION

    astra = api_equivalent_cost(
        provider="codex", model="gpt-6-astra", input_tokens=100_000,
        cached_input_tokens=50_000, cache_write_input_tokens=20_000,
        output_tokens=10_000,
    )
    assert astra is not None
    assert astra.cost_usd == Decimal("1.10")
    assert astra.pricing_version == SEPTEMBER_API_PRICING_VERSION
    assert astra.cached_input_cost_usd == Decimal("0.05")
    fable = api_equivalent_cost(
        provider="claude", model="claude-fable-5-1", input_tokens=30_000,
        cached_input_tokens=50_000, cache_write_input_tokens=20_000,
        output_tokens=10_000, cache_write_ttl="5m",
    )
    assert fable is not None and fable.cost_usd == Decimal("1.0625")
    assert fable.pricing_version == SEPTEMBER_API_PRICING_VERSION
    one_hour = api_equivalent_cost(
        provider="claude", model="claude-fable-5-1", input_tokens=30_000,
        cached_input_tokens=50_000, cache_write_input_tokens=20_000,
        output_tokens=10_000, cache_write_ttl="1h",
    )
    assert one_hour is not None and one_hour.cost_usd == Decimal("1.2125")
    assert resolve_model_price("claude", "claude-fable-5").pricing_version == API_PRICING_VERSION
    assert resolve_model_price("codex", "gpt-5.6-terra").pricing_version == API_PRICING_VERSION
    assert resolve_model_price("codex", "gpt-6-astra-future") is None


def test_astra_long_context_prices_the_whole_request_without_double_counting():
    base = dict(provider="openai", model="gpt-6-astra", cached_input_tokens=100_000,
                cache_write_input_tokens=20_000, output_tokens=10_000)
    short = api_equivalent_cost(input_tokens=272_000, **base)
    long = api_equivalent_cost(input_tokens=272_001, **base)
    assert short is not None and short.cost_usd == Decimal("2.37")
    assert long is not None and long.cost_usd == Decimal("4.49002")
    assert long.output_cost_usd == Decimal("0.75")
    assert long.cached_input_cost_usd == Decimal("0.20")
