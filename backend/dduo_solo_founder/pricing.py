"""Immutable list-price snapshots for hypothetical API-equivalent cost.

The subscription clients do not expose an invoice.  This module prices only
token usage that a provider reported (or dDuo explicitly estimated) against an
exact, versioned model entry.  Unknown models and inconsistent usage stay
unpriced instead of being guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, Mapping

MILLION = Decimal(1_000_000)
PICO_USD = Decimal(1_000_000_000_000)

API_PRICING_VERSION = "api-list-prices-2026-08-29"
SEPTEMBER_API_PRICING_VERSION = "api-list-prices-2026-09-05"
EMBEDDING_PRICING_VERSION = "openai-text-embedding-3-large-2026-08"
EMBEDDING_PRICE_PER_MILLION = Decimal("0.13")

Provider = Literal["openai", "anthropic"]


@dataclass(frozen=True, slots=True)
class ModelPrice:
    provider: Provider
    canonical_model: str
    input_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal
    cache_write_input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    context_window_tokens: int
    long_context_threshold_input_tokens: int | None = None
    long_context_input_multiplier: Decimal = Decimal(1)
    long_context_output_multiplier: Decimal = Decimal(1)
    pricing_version: str = API_PRICING_VERSION


@dataclass(frozen=True, slots=True)
class EquivalentApiCost:
    cost_usd: Decimal
    uncached_input_cost_usd: Decimal
    cached_input_cost_usd: Decimal
    cache_write_input_cost_usd: Decimal
    output_cost_usd: Decimal
    pricing_version: str
    canonical_provider: Provider
    canonical_model: str
    unit_price_usd_per_million: Decimal | None

    def numeric_details(self) -> dict[str, int]:
        """Return immutable component costs as content-free integer picodollars."""
        return {
            "uncached_input_cost_pico_usd": _pico_usd(self.uncached_input_cost_usd),
            "cached_input_cost_pico_usd": _pico_usd(self.cached_input_cost_usd),
            "cache_write_input_cost_pico_usd": _pico_usd(
                self.cache_write_input_cost_usd
            ),
            "output_cost_pico_usd": _pico_usd(self.output_cost_usd),
        }


def _pico_usd(value: Decimal) -> int:
    scaled = value * PICO_USD
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError("API-equivalent cost exceeds persisted decimal precision")
    return int(integral)


def _price(
    provider: Provider,
    model: str,
    *,
    input_rate: str,
    cached_rate: str,
    cache_write_rate: str,
    output_rate: str,
    context_window_tokens: int,
    long_context_threshold_input_tokens: int | None = None,
    long_context_input_multiplier: str = "1",
    long_context_output_multiplier: str = "1",
    pricing_version: str = API_PRICING_VERSION,
) -> ModelPrice:
    return ModelPrice(
        provider=provider,
        canonical_model=model,
        input_usd_per_million=Decimal(input_rate),
        cached_input_usd_per_million=Decimal(cached_rate),
        cache_write_input_usd_per_million=Decimal(cache_write_rate),
        output_usd_per_million=Decimal(output_rate),
        context_window_tokens=context_window_tokens,
        long_context_threshold_input_tokens=long_context_threshold_input_tokens,
        long_context_input_multiplier=Decimal(long_context_input_multiplier),
        long_context_output_multiplier=Decimal(long_context_output_multiplier),
        pricing_version=pricing_version,
    )


# Standard API equivalents, not subscription credits or Fast-mode billing.
# https://developers.openai.com/api/docs/models/gpt-6-astra
# https://developers.openai.com/api/docs/pricing
_OPENAI_ASTRA = _price(
    "openai",
    "gpt-6-astra",
    input_rate="10",
    cached_rate="1",
    cache_write_rate="12.50",
    output_rate="50",
    context_window_tokens=1_050_000,
    long_context_threshold_input_tokens=272_000,
    long_context_input_multiplier="2",
    long_context_output_multiplier="1.5",
    pricing_version=SEPTEMBER_API_PRICING_VERSION,
)
_OPENAI_SOL = _price(
    "openai",
    "gpt-5.6-sol",
    input_rate="4",
    cached_rate="0.40",
    cache_write_rate="5",
    output_rate="20",
    context_window_tokens=1_050_000,
    long_context_threshold_input_tokens=272_000,
    long_context_input_multiplier="2",
    long_context_output_multiplier="1.5",
)
_OPENAI_TERRA = _price(
    "openai",
    "gpt-5.6-terra",
    input_rate="2",
    cached_rate="0.20",
    cache_write_rate="2.50",
    output_rate="12",
    context_window_tokens=1_050_000,
    long_context_threshold_input_tokens=272_000,
    long_context_input_multiplier="2",
    long_context_output_multiplier="1.5",
)
_OPENAI_LUNA = _price(
    "openai",
    "gpt-5.6-luna",
    input_rate="0.20",
    cached_rate="0.02",
    cache_write_rate="0.25",
    output_rate="1.20",
    context_window_tokens=1_050_000,
    long_context_threshold_input_tokens=272_000,
    long_context_input_multiplier="2",
    long_context_output_multiplier="1.5",
)

# Fable 5.1 cache hits are 0.025x base input, not the older 0.1x.
# https://platform.claude.com/docs/en/about-claude/pricing
_CLAUDE_FABLE_51 = _price(
    "anthropic",
    "claude-fable-5-1",
    input_rate="10",
    cached_rate="0.25",
    cache_write_rate="12.50",
    output_rate="50",
    context_window_tokens=1_000_000,
    pricing_version=SEPTEMBER_API_PRICING_VERSION,
)
_CLAUDE_FABLE_5 = _price(
    "anthropic",
    "claude-fable-5",
    input_rate="10",
    cached_rate="1",
    cache_write_rate="12.50",
    output_rate="50",
    context_window_tokens=1_000_000,
)
_CLAUDE_OPUS_5 = _price(
    "anthropic",
    "claude-opus-5",
    input_rate="5",
    cached_rate="0.50",
    cache_write_rate="6.25",
    output_rate="25",
    context_window_tokens=1_000_000,
)
_CLAUDE_OPUS_48 = _price(
    "anthropic",
    "claude-opus-4-8",
    input_rate="5",
    cached_rate="0.50",
    cache_write_rate="6.25",
    output_rate="25",
    context_window_tokens=1_000_000,
)
_CLAUDE_OPUS_47 = _price(
    "anthropic",
    "claude-opus-4-7",
    input_rate="5",
    cached_rate="0.50",
    cache_write_rate="6.25",
    output_rate="25",
    context_window_tokens=1_000_000,
)
_CLAUDE_OPUS_46 = _price(
    "anthropic",
    "claude-opus-4-6",
    input_rate="5",
    cached_rate="0.50",
    cache_write_rate="6.25",
    output_rate="25",
    context_window_tokens=1_000_000,
)
_CLAUDE_OPUS_45 = _price(
    "anthropic",
    "claude-opus-4-5-20251101",
    input_rate="5",
    cached_rate="0.50",
    cache_write_rate="6.25",
    output_rate="25",
    context_window_tokens=200_000,
)
_CLAUDE_SONNET_5 = _price(
    "anthropic",
    "claude-sonnet-5",
    input_rate="2",
    cached_rate="0.20",
    cache_write_rate="2.50",
    output_rate="10",
    context_window_tokens=1_000_000,
)
_CLAUDE_SONNET_46 = _price(
    "anthropic",
    "claude-sonnet-4-6",
    input_rate="3",
    cached_rate="0.30",
    cache_write_rate="3.75",
    output_rate="15",
    context_window_tokens=1_000_000,
)
_CLAUDE_SONNET_45 = _price(
    "anthropic",
    "claude-sonnet-4-5-20250929",
    input_rate="3",
    cached_rate="0.30",
    cache_write_rate="3.75",
    output_rate="15",
    context_window_tokens=200_000,
)
_CLAUDE_HAIKU_45 = _price(
    "anthropic",
    "claude-haiku-4-5-20251001",
    input_rate="1",
    cached_rate="0.10",
    cache_write_rate="1.25",
    output_rate="5",
    context_window_tokens=200_000,
)

# Every accepted identifier is explicit.  There is intentionally no prefix or
# fuzzy matching: a new provider model must ship with a new immutable snapshot.
MODEL_PRICES: Mapping[tuple[Provider, str], ModelPrice] = MappingProxyType(
    {
        ("openai", "gpt-6-astra"): _OPENAI_ASTRA,
        ("openai", "gpt-5.6"): _OPENAI_SOL,
        ("openai", "gpt-5.6-sol"): _OPENAI_SOL,
        ("openai", "gpt-5.6-terra"): _OPENAI_TERRA,
        ("openai", "gpt-5.6-luna"): _OPENAI_LUNA,
        ("anthropic", "claude-fable-5"): _CLAUDE_FABLE_5,
        ("anthropic", "claude-fable-5-1"): _CLAUDE_FABLE_51,
        ("anthropic", "claude-opus-5"): _CLAUDE_OPUS_5,
        ("anthropic", "claude-opus-4-8"): _CLAUDE_OPUS_48,
        ("anthropic", "claude-opus-4-7"): _CLAUDE_OPUS_47,
        ("anthropic", "claude-opus-4-6"): _CLAUDE_OPUS_46,
        ("anthropic", "claude-opus-4-5"): _CLAUDE_OPUS_45,
        ("anthropic", "claude-opus-4-5-20251101"): _CLAUDE_OPUS_45,
        ("anthropic", "claude-sonnet-5"): _CLAUDE_SONNET_5,
        ("anthropic", "claude-sonnet-4-6"): _CLAUDE_SONNET_46,
        ("anthropic", "claude-sonnet-4-5"): _CLAUDE_SONNET_45,
        ("anthropic", "claude-sonnet-4-5-20250929"): _CLAUDE_SONNET_45,
        ("anthropic", "claude-haiku-4-5"): _CLAUDE_HAIKU_45,
        ("anthropic", "claude-haiku-4-5-20251001"): _CLAUDE_HAIKU_45,
    }
)

_PROVIDER_ALIASES: Mapping[str, Provider] = MappingProxyType(
    {
        "openai": "openai",
        "codex": "openai",
        "anthropic": "anthropic",
        "claude": "anthropic",
    }
)


def resolve_model_price(
    provider: str | None,
    model: str | None,
    *,
    occurred_at: datetime | None = None,
) -> ModelPrice | None:
    """Resolve only exact, explicitly catalogued provider and model identifiers."""
    if not isinstance(provider, str) or not isinstance(model, str):
        return None
    canonical_provider = _PROVIDER_ALIASES.get(provider)
    if canonical_provider is None:
        return None
    # New model identities have their own snapshot; existing identities retain
    # the original rates and version. ``occurred_at`` stays in the contract for
    # future effective-dated rate changes. Never reprice persisted observations.
    del occurred_at
    return MODEL_PRICES.get((canonical_provider, model))


def _tokens(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def api_equivalent_cost(
    *,
    provider: str | None,
    model: str | None,
    input_tokens: int | None,
    cached_input_tokens: int | None = None,
    cache_write_input_tokens: int | None = None,
    output_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    cache_write_ttl: Literal["5m", "1h"] | None = None,
    occurred_at: datetime | None = None,
) -> EquivalentApiCost | None:
    """Price one normalized usage sample without double counting token subsets.

    OpenAI reports cached/cache-write prompt tokens as subsets of total input;
    Anthropic reports base input, cache reads, and cache writes as disjoint
    counters.  Reasoning tokens are already included in output for both and are
    validated, but never charged a second time.
    """
    price = resolve_model_price(provider, model, occurred_at=occurred_at)
    if price is None:
        return None
    if cache_write_ttl not in {None, "5m", "1h"}:
        return None
    raw_values = (
        input_tokens,
        cached_input_tokens,
        cache_write_input_tokens,
        output_tokens,
        reasoning_tokens,
    )
    # A missing billable class is unknown, not zero. Pricing an unreported
    # cache class as ordinary input could silently over- or understate the
    # API-equivalent cost.
    if any(value is None for value in raw_values[:4]):
        return None
    parsed = tuple(_tokens(value) for value in raw_values)
    if any(raw is not None and normalized is None for raw, normalized in zip(raw_values, parsed)):
        return None
    input_count, cached_count, cache_write_count, output_count, reasoning_count = (
        value or 0 for value in parsed
    )
    if reasoning_count > output_count:
        return None

    if price.provider == "openai":
        # Provider usage counts the cached portions inside total prompt input.
        if cached_count + cache_write_count > input_count:
            return None
        base_input_count = input_count - cached_count - cache_write_count
        context_tokens = input_count + output_count
    else:
        # Anthropic's usage envelope exposes these as separate token classes.
        # Cache creation has two prices. The aggregate counter alone cannot tell
        # whether Claude used the five-minute or one-hour TTL.
        if cache_write_count and cache_write_ttl is None:
            return None
        base_input_count = input_count
        context_tokens = input_count + cached_count + cache_write_count + output_count
    if context_tokens > price.context_window_tokens:
        # Do not silently apply a premium or price telemetry that could not be
        # one request under the catalogued model contract.
        return None

    uses_long_context_rate = (
        price.long_context_threshold_input_tokens is not None
        and input_count > price.long_context_threshold_input_tokens
    )
    input_multiplier = price.long_context_input_multiplier if uses_long_context_rate else Decimal(1)
    output_multiplier = (
        price.long_context_output_multiplier if uses_long_context_rate else Decimal(1)
    )
    cache_write_rate = price.cache_write_input_usd_per_million
    if price.provider == "anthropic" and cache_write_count and cache_write_ttl == "1h":
        cache_write_rate = price.input_usd_per_million * Decimal(2)
    billable = (
        (base_input_count, price.input_usd_per_million * input_multiplier),
        (cached_count, price.cached_input_usd_per_million * input_multiplier),
        (
            cache_write_count,
            cache_write_rate * input_multiplier,
        ),
        (output_count, price.output_usd_per_million * output_multiplier),
    )
    component_costs = tuple(Decimal(count) * rate / MILLION for count, rate in billable)
    cost = sum(component_costs, Decimal(0))
    used_rates = {rate for count, rate in billable if count > 0}
    return EquivalentApiCost(
        cost_usd=cost,
        uncached_input_cost_usd=component_costs[0],
        cached_input_cost_usd=component_costs[1],
        cache_write_input_cost_usd=component_costs[2],
        output_cost_usd=component_costs[3],
        pricing_version=price.pricing_version,
        canonical_provider=price.provider,
        canonical_model=price.canonical_model,
        unit_price_usd_per_million=(next(iter(used_rates)) if len(used_rates) == 1 else None),
    )


def embedding_price(
    model: str | None, input_tokens: int | None
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """Preserve the original embedding list-price contract and snapshot."""
    if model != "text-embedding-3-large" or _tokens(input_tokens) is None:
        return None, None, None
    return (
        Decimal(input_tokens) * EMBEDDING_PRICE_PER_MILLION / MILLION,
        EMBEDDING_PRICE_PER_MILLION,
        EMBEDDING_PRICING_VERSION,
    )
