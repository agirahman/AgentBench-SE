"""Tests for the exchange-rate fetcher and its multi-source fallback."""

import pytest

from agentbench import exchange_rate as er


@pytest.fixture(autouse=True)
def clear_rate_cache():
    er.clear_cache()
    yield
    er.clear_cache()


def test_fetch_returns_rate_and_source(monkeypatch):
    """Frankfurter (ECB) works -> rate + source + timestamp returned."""
    monkeypatch.setattr(
        er, "_fetch_bi_jisdor", lambda: (_ for _ in ()).throw(er.RateFetchError("x"))
    )
    monkeypatch.setattr(
        er, "_fetch_frankfurter", lambda: (17900.0, "frankfurter(ecb)")
    )
    rate, source, ts = er.fetch_usd_idr_rate()
    assert rate == 17900.0
    assert source == "frankfurter(ecb)"
    assert ts


def test_caches_result(monkeypatch):
    calls = {"n": 0}

    def fake_bi():
        calls["n"] += 1
        return (17800.0, "bi.jisdor")

    monkeypatch.setattr(er, "_fetch_bi_jisdor", fake_bi)
    r1, s1, _ = er.fetch_usd_idr_rate()
    r2, s2, _ = er.fetch_usd_idr_rate()  # cached, no second fetch
    assert calls["n"] == 1
    assert (r1, s1) == (r2, s2) == (17800.0, "bi.jisdor")


def test_clear_cache_forces_refetch(monkeypatch):
    calls = {"n": 0}

    def fake_bi():
        calls["n"] += 1
        return (17800.0, "bi.jisdor")

    monkeypatch.setattr(er, "_fetch_bi_jisdor", fake_bi)
    er.fetch_usd_idr_rate()
    er.clear_cache()
    er.fetch_usd_idr_rate()
    assert calls["n"] == 2


def test_falls_through_sources(monkeypatch):
    """When BI fails, fallback to next source succeeds."""
    calls = []
    monkeypatch.setattr(
        er,
        "_fetch_bi_jisdor",
        lambda: (_ for _ in ()).throw(er.RateFetchError("bi down")),
    )
    monkeypatch.setattr(
        er, "_fetch_frankfurter", lambda: (17900.0, "frankfurter(ecb)")
    )
    rate, source, _ = er.fetch_usd_idr_rate()
    assert source == "frankfurter(ecb)"
    assert rate == 17900.0


def test_raises_when_all_sources_fail(monkeypatch):
    def boom():
        raise er.RateFetchError("down")

    monkeypatch.setattr(er, "_fetch_bi_jisdor", boom)
    monkeypatch.setattr(er, "_fetch_frankfurter", boom)
    monkeypatch.setattr(er, "_fetch_erapi", boom)
    with pytest.raises(er.RateFetchError):
        er.fetch_usd_idr_rate()


def test_bi_jisdor_parser_recognizes_table(monkeypatch):
    """BI's HTML table row with a US Dolar label yields a numeric rate."""
    fake_html = (
        "<table><tr>"
        "<td>US Dolar</td><td>17500</td><td>17800</td><td>17650</td>"
        "</tr></table>"
    )

    def fake_get(url, headers=None):
        return fake_html.encode()

    monkeypatch.setattr(er, "_get", fake_get)
    rate, source = er._fetch_bi_jisdor()
    assert source == "bi.jisdor"
    assert rate == 17650.0  # middle of sorted([17500,17650,17800])