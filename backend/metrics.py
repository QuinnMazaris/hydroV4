from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional


def _load_color_palette() -> List[str]:
    """Load color palette from shared configuration file."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "metric-metadata.json")
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
            return config.get("colorPalette", [])
    except (FileNotFoundError, json.JSONDecodeError):
        # Fallback to hardcoded palette if config file is missing
        return [
            "oklch(0.7 0.15 142)",
            "oklch(0.65 0.18 220)",
            "oklch(0.75 0.12 60)",
            "oklch(0.68 0.16 300)",
            "oklch(0.72 0.14 180)",
        ]


PALETTE = _load_color_palette()


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

