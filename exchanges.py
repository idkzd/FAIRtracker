"""
Exchange API modules — fetch fair price, last price, volume, leverage for all
USDT-margined perpetual futures.

Filters:
 - Only USDT perpetuals with status=Trading
 - Only pairs with 24h volume >= MIN_VOLUME_USD (skip dead pairs)
"""
from __future__ import annotations
import asyncio, logging
from dataclasses import dataclass
from typing import Dict, List, Optional
import aiohttp

log = logging.getLogger("exchanges")

# Minimum 24h volume to consider a pair (skip dead/fake pairs)
MIN_VOLUME_USD = 50_000


@dataclass
class TickerInfo:
    symbol: str          # e.g. "BTCUSDT"
    exchange: str        # "bybit" | "binance" | "okx"
    fair_price: float    # mark price
    last_price: float
    max_leverage: Optional[float] = None
    max_size_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None


async def _json(session: aiohttp.ClientSession, url: str, params=None):
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
        r.raise_for_status()
        return await r.json()


# ── Bybit (v5) ───────────────────────────────────────────────────────────────

async def fetch_bybit(session: aiohttp.ClientSession) -> List[TickerInfo]:
    try:
        tickers_data, instruments_data = await asyncio.gather(
            _json(session, "https://api.bybit.com/v5/market/tickers", {"category": "linear"}),
            _json(session, "https://api.bybit.com/v5/market/instruments-info", {"category": "linear"}),
        )
        tickers = {t["symbol"]: t for t in tickers_data.get("result", {}).get("list", [])}
        instruments = {i["symbol"]: i for i in instruments_data.get("result", {}).get("list", [])}

        # only active trading instruments
        active_symbols = set()
        for sym, inst in instruments.items():
            if inst.get("status") == "Trading" and sym.endswith("USDT"):
                active_symbols.add(sym)

        result: List[TickerInfo] = []
        for sym in active_symbols:
            t = tickers.get(sym)
            if not t:
                continue
            fair = float(t.get("markPrice") or 0)
            last = float(t.get("lastPrice") or 0)
            if fair == 0 or last == 0:
                continue
            vol24 = float(t.get("turnover24h", 0))
            if vol24 < MIN_VOLUME_USD:
                continue
            inst = instruments.get(sym, {})
            lev_filter = inst.get("leverageFilter", {})
            max_lev = float(lev_filter.get("maxLeverage", 0)) or None
            lot_filter = inst.get("lotSizeFilter", {})
            max_qty = float(lot_filter.get("maxOrderQty", 0)) or 0
            max_size = max_qty * last if max_qty else None
            result.append(TickerInfo(sym, "bybit", fair, last, max_lev, max_size, vol24))
        return result
    except Exception as e:
        log.error("bybit error: %s", e)
        return []


# ── Binance ───────────────────────────────────────────────────────────────────

async def fetch_binance(session: aiohttp.ClientSession) -> List[TickerInfo]:
    try:
        mark_data, ticker_data, info_data = await asyncio.gather(
            _json(session, "https://fapi.binance.com/fapi/v1/premiumIndex"),
            _json(session, "https://fapi.binance.com/fapi/v1/ticker/24hr"),
            _json(session, "https://fapi.binance.com/fapi/v1/exchangeInfo"),
        )
        marks = {m["symbol"]: float(m["markPrice"]) for m in mark_data}
        tickers = {t["symbol"]: t for t in ticker_data}

        # only TRADING status symbols
        active_symbols = set()
        for s in info_data.get("symbols", []):
            if s.get("status") == "TRADING" and s["symbol"].endswith("USDT") and s.get("contractType") == "PERPETUAL":
                active_symbols.add(s["symbol"])

        result: List[TickerInfo] = []
        for sym in active_symbols:
            t = tickers.get(sym)
            if not t:
                continue
            fair = marks.get(sym, 0)
            last = float(t.get("lastPrice", 0))
            if fair == 0 or last == 0:
                continue
            vol24 = float(t.get("quoteVolume", 0))
            if vol24 < MIN_VOLUME_USD:
                continue
            result.append(TickerInfo(sym, "binance", fair, last, None, None, vol24))
        return result
    except Exception as e:
        log.error("binance error: %s", e)
        return []


# ── OKX ───────────────────────────────────────────────────────────────────────

async def fetch_okx(session: aiohttp.ClientSession) -> List[TickerInfo]:
    try:
        tickers_data, mark_data, instruments_data = await asyncio.gather(
            _json(session, "https://www.okx.com/api/v5/market/tickers", {"instType": "SWAP"}),
            _json(session, "https://www.okx.com/api/v5/public/mark-price", {"instType": "SWAP"}),
            _json(session, "https://www.okx.com/api/v5/public/instruments", {"instType": "SWAP"}),
        )
        tickers = {t["instId"]: t for t in tickers_data.get("data", [])}
        marks = {m["instId"]: float(m["markPx"]) for m in mark_data.get("data", [])}

        # only live USDT-SWAP instruments
        active_ids = set()
        for inst in instruments_data.get("data", []):
            if inst.get("state") == "live" and inst["instId"].endswith("-USDT-SWAP"):
                active_ids.add(inst["instId"])

        result: List[TickerInfo] = []
        for inst_id in active_ids:
            t = tickers.get(inst_id)
            if not t:
                continue
            fair = marks.get(inst_id, 0)
            last = float(t.get("last", 0))
            if fair == 0 or last == 0:
                continue
            vol24 = float(t.get("volCcy24h", 0))
            if vol24 < MIN_VOLUME_USD:
                continue
            sym = inst_id.replace("-USDT-SWAP", "USDT").replace("-", "")
            result.append(TickerInfo(sym, "okx", fair, last, None, None, vol24))
        return result
    except Exception as e:
        log.error("okx error: %s", e)
        return []


# ── Aggregate ─────────────────────────────────────────────────────────────────

async def fetch_all(session: aiohttp.ClientSession) -> List[TickerInfo]:
    groups = await asyncio.gather(
        fetch_bybit(session),
        fetch_binance(session),
        fetch_okx(session),
    )
    all_tickers: List[TickerInfo] = []
    for g in groups:
        all_tickers.extend(g)
    return all_tickers
