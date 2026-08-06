"""USD -> IDR exchange rate fetcher with multi-source fallback.

Sources (in priority order):

1. Bank Indonesia JISDOR (official — most credible for the thesis).
   BI's site is a SharePoint app that resists automation (WAF, 302,
   ajax-loaded table), so scraping may fail from some networks; when it
   does, we fall through.
2. Frankfurter API (European Central Bank data) — free, no key, stable.
3. open.er-api.com — free aggregator, no key.

Every fetch has a short timeout; failures raise ``RateFetchError`` so
callers can fall back to the configured/manual rate.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime, timezone

TIMEOUT_SECONDS = 12
_cache: dict = {"rate": None, "source": None, "fetched_at": None, "fetched_epoch": None}
CACHE_TTL_SECONDS = 6 * 3600  # refresh at most every 6 hours

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class RateFetchError(RuntimeError):
    """Raised when no source can provide a rate."""


def _get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
        return resp.read()


def _fetch_bi_jisdor() -> tuple[float, str]:
    """Try to scrape the JISDOR rate from bi.go.id.

    The official table is ajax-loaded; the landing page embeds a JSON
    payload (``jisdorList``) in some deployments. We probe a few known
    shapes and raise RateFetchError on failure.
    """
    url = "https://www.bi.go.id/id/statistik/informasi-kurs/transaksi-bi/Default.aspx"
    html = _get(url).decode("utf-8", errors="ignore")

    # Common patterns BI embeds: {"tanggal":"...","nilai":17871.00,...}
    for pattern in (
        r'US\s?Dolar.{0,120}?"?nilai"?\s*[:=]\s*"?([0-9]{4,5}(?:[.,][0-9]{2,4})?)',
        r'"USD"\s*:\s*([0-9]{4,5}(?:[.,][0-9]{2,4})?)',
        r'JISDOR.{0,80}?([0-9]{4,5}[,.][0-9]{2,3})',
    ):
        m = re.search(pattern, html, re.I | re.S)
        if m:
            raw = m.group(1).replace(",", "")
            return float(raw), "bi.jisdor"

    # Table row fallback: find a row whose label mentions US Dolar and
    # capture the Jual/Beli-style numeric column closest to it.
    rows = re.findall(
        r'<tr[^>]*>(.*?)</tr>', html, re.I | re.S
    )
    for row in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.I | re.S)]
        label = " ".join(cells).lower()
        if "dolar" in label or "usd" in label:
            nums = [float(c.replace(",", "")) for c in cells
                    if re.fullmatch(r"[0-9]{3,5}([.,][0-9]{2,4})?", c)]
            if nums:
                # JISDOR is a mid rate; take the middle-ish value
                return sorted(nums)[len(nums) // 2], "bi.jisdor"

    raise RateFetchError("BI JISDOR page structure not recognized")


def _fetch_frankfurter() -> tuple[float, str]:
    """ECB-based rate via the Frankfurter API (no key)."""
    data = json.loads(_get("https://api.frankfurter.app/latest?from=USD&to=IDR"))
    return float(data["rates"]["IDR"]), "frankfurter(ecb)"


def _fetch_erapi() -> tuple[float, str]:
    """open.er-api.com free aggregator (no key)."""
    data = json.loads(_get("https://open.er-api.com/v6/latest/USD"))
    if data.get("result") != "success":
        raise RateFetchError("open.er-api returned non-success")
    return float(data["rates"]["IDR"]), "open.er-api"


def _source_fetchers() -> tuple:
    """Return source fetchers, resolved lazily so tests can monkeypatch them."""
    return (_fetch_bi_jisdor, _fetch_frankfurter, _fetch_erapi)


def fetch_usd_idr_rate(use_cache: bool = True) -> tuple[float, str, str]:
    """Return (rate, source, iso_timestamp). Falls through sources on failure.

    Results are cached for 6h. Raises RateFetchError if all sources fail.
    """
    now = time.time()
    if (
        use_cache
        and _cache["rate"] is not None
        and _cache["fetched_epoch"] is not None
        and now - _cache["fetched_epoch"] < CACHE_TTL_SECONDS
    ):
        return _cache["rate"], _cache["source"], _cache["fetched_at"]  # type: ignore[return-value]

    errors: list[str] = []
    for fetcher in _source_fetchers():
        try:
            rate, source = fetcher()
            ts = datetime.now(timezone.utc).isoformat()
            _cache.update(rate=rate, source=source, fetched_at=ts, fetched_epoch=now)
            return rate, source, ts
        except Exception as e:  # noqa: BLE001 - try next source
            errors.append(f"{fetcher.__name__}: {e}")

    raise RateFetchError("All exchange-rate sources failed: " + "; ".join(errors))


def clear_cache() -> None:
    """Drop the cached rate (forces a fresh fetch next call)."""
    _cache.update(rate=None, source=None, fetched_at=None, fetched_epoch=None)