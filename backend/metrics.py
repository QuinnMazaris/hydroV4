from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

PALETTE = [
    "oklch(0.7 0.15 142)",
    "oklch(0.65 0.18 220)",
    "oklch(0.75 0.12 60)",
    "oklch(0.68 0.16 300)",
    "oklch(0.72 0.14 180)",
]


@dataclass(frozen=True)
class MetricMeta:
    id: str
    label: str
    color: str
    unit: Optional[str] = None


def title_case(metric_id: str) -> str:
    return " ".join(part.capitalize() for part in metric_id.split("_")) or metric_id


def color_for_id(metric_id: str) -> str:
    hash_value = sum(ord(ch) for ch in metric_id)
    return PALETTE[hash_value % len(PALETTE)]


def build_metric_meta(metric_id: str, overrides: Optional[Dict[str, str]] = None) -> MetricMeta:
    overrides = overrides or {}
    return MetricMeta(
        id=metric_id,
        label=overrides.get("label") or title_case(metric_id),
        color=overrides.get("color") or color_for_id(metric_id),
        unit=overrides.get("unit"),
    )
