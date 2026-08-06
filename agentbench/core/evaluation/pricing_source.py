"""Pricing source for model inference costs (2-layer design).

Layer order (first match wins):

1. Config override — user-set via ``pricing set`` (or editing config.yaml
   ``pricing`` section). Explicit user values always win.
2. OpenRouter catalog (``GET /api/v1/models``) — live prices, cached 6h.
   Free models (prompt=0 & completion=0) resolve to $0.

No hardcoded fallback table: if neither layer knows the model, ``get()``
returns None and callers treat cost as $0 with a visible warning.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Optional

CACHE_TTL_SECONDS = 6 * 3600
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_catalog: dict = {"data": None, "fetched_epoch": None}
_catalog_fetch_errors: list[str] = []


# --------------------------------------------------------------------- #
# OpenRouter catalog
# --------------------------------------------------------------------- #
def _fetch_openrouter_catalog() -> dict:
    """Fetch and normalize OpenRouter /models into {id: pricing_dict}."""
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/models",
        headers={"User-Agent": _UA},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))

    normalized: dict = {}
    for m in payload.get("data", []):
        mid = m.get("id", "")
        p = m.get("pricing", {}) or {}
        try:
            prompt = float(p.get("prompt", 0.0))
            completion = float(p.get("completion", 0.0))
        except (TypeError, ValueError):
            continue
        normalized[mid] = {
            "input_per_million": prompt * 1_000_000,
            "output_per_million": completion * 1_000_000,
            "currency": "USD",
            "pricing_source": "openrouter",
            "pricing_version": m.get("updated") or "",
        }
    return normalized


def get_openrouter_pricing(model_id: str, force_refresh: bool = False) -> Optional[dict]:
    """Return OpenRouter pricing for ``model_id`` (cached 6h), else None."""
    now = time.time()
    if (
        not force_refresh
        and _catalog["data"] is not None
        and _catalog["fetched_epoch"] is not None
        and now - _catalog["fetched_epoch"] < CACHE_TTL_SECONDS
    ):
        return _catalog["data"].get(model_id)

    try:
        _catalog["data"] = _fetch_openrouter_catalog()
        _catalog["fetched_epoch"] = now
        _catalog_fetch_errors.clear()
        return _catalog["data"].get(model_id)
    except Exception as e:  # noqa: BLE001 - catalog must never break a run
        _catalog_fetch_errors.append(str(e))
        # keep stale cache if we have one
        return _catalog["data"].get(model_id) if _catalog["data"] else None


def clear_openrouter_cache() -> None:
    """Drop the cached catalog (forces a fresh fetch next call)."""
    _catalog.update(data=None, fetched_epoch=None)
    _catalog_fetch_errors.clear()


def last_catalog_errors() -> list[str]:
    return list(_catalog_fetch_errors)


# --------------------------------------------------------------------- #
# Model-id normalization (openrouter vs provider-prefixed ids)
# --------------------------------------------------------------------- #
def normalize_model_id(model_id: str) -> str:
    """Strip provider prefixes/variants for OpenRouter lookup.

    Examples:
      oc/deepseek-v4-flash-free -> deepseek/deepseek-v4-flash-free
      oc/deepseek-v4-flash       -> deepseek/deepseek-v4-flash
      ~deepseek/deepseek-v4-flash-latest -> deepseek/deepseek-v4-flash-latest
    """
    mid = model_id.strip()
    if "/" in mid:
        # only strip a leading provider prefix when it is NOT the model org
        # (oc/, opencode/ are providers; deepseek/ is the model org)
        org, _, rest = mid.partition("/")
        if org in ("oc", "opencode", "openrouter"):
            mid = rest
    if mid.startswith("~"):
        mid = mid[1:]
    return mid


def _openrouter_lookup(model_id: str, force_refresh: bool = False) -> Optional[dict]:
    """Lookup in OpenRouter, trying the raw id then the normalized id."""
    direct = get_openrouter_pricing(model_id, force_refresh=force_refresh)
    if direct is not None:
        return direct
    norm = normalize_model_id(model_id)
    if norm != model_id:
        return get_openrouter_pricing(norm)
    return None