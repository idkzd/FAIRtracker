"""
Core tracker logic:
- detect divergence >= threshold
- track history at 10s/20s/30s/60s
- detect equalization
- format Telegram messages
- returns ACTION objects (send new / edit existing) instead of raw strings
"""
from __future__ import annotations
import time, logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from exchanges import TickerInfo
from config import DIVERGENCE_THRESHOLD

log = logging.getLogger("tracker")


def _fmt_num(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M$"
    if n >= 1_000:
        return f"{n:,.0f}$"
    return f"{n:.2f}$"


def _fmt_price(p: float) -> str:
    if p >= 100:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.4f}"
    return f"{p:.5f}"


@dataclass
class Action:
    """What to do in Telegram."""
    kind: str              # "send" or "edit"
    key: Tuple[str, str]   # (symbol, exchange)
    text: str
    message_id: Optional[int] = None  # for edit actions


@dataclass
class AlertState:
    symbol: str
    exchange: str
    direction: str
    initial_pct: float
    first_seen: float
    tg_message_id: Optional[int] = None   # filled after TG send
    equalized: bool = False
    removed: bool = False
    last_edit_text: str = ""               # avoid editing with same text
    # history: list of (timestamp, pct)
    history: List[Tuple[float, float]] = field(default_factory=list)
    # snapshot at alert time
    fair_price: float = 0.0
    last_price: float = 0.0
    max_size_usd: Optional[float] = None
    max_leverage: Optional[float] = None
    volume_24h_usd: Optional[float] = None


# Cooldown: after equalization, ignore same key for this many seconds
EQUALIZE_COOLDOWN = 300


class Tracker:
    def __init__(self):
        self.active: Dict[Tuple[str, str], AlertState] = {}
        # cooldown: key -> timestamp when cooldown expires
        self.cooldowns: Dict[Tuple[str, str], float] = {}

    def process(self, tickers: List[TickerInfo]) -> List[Action]:
        now = time.time()
        actions: List[Action] = []

        # clean expired cooldowns
        expired = [k for k, v in self.cooldowns.items() if now > v]
        for k in expired:
            del self.cooldowns[k]

        for t in tickers:
            key = (t.symbol, t.exchange)
            pct = ((t.fair_price - t.last_price) / t.last_price) * 100

            if abs(pct) >= DIVERGENCE_THRESHOLD:
                direction = "FAIR &gt; LAST" if pct > 0 else "FAIR &lt; LAST"

                if key in self.cooldowns:
                    continue  # still in cooldown after last equalization

                if key not in self.active:
                    # ── NEW alert ──
                    state = AlertState(
                        symbol=t.symbol,
                        exchange=t.exchange,
                        direction=direction,
                        initial_pct=pct,
                        first_seen=now,
                        fair_price=t.fair_price,
                        last_price=t.last_price,
                        max_size_usd=t.max_size_usd,
                        max_leverage=t.max_leverage,
                        volume_24h_usd=t.volume_24h_usd,
                    )
                    state.history.append((now, pct))
                    self.active[key] = state
                    text = self._build_text(state, now)
                    state.last_edit_text = text
                    actions.append(Action("send", key, text))
                else:
                    # ── UPDATE existing alert ──
                    state = self.active[key]
                    state.history.append((now, pct))
                    # update current prices
                    state.fair_price = t.fair_price
                    state.last_price = t.last_price
                    state.initial_pct = pct
                    state.equalized = False
                    # only edit if we have a message_id and text changed
                    if state.tg_message_id:
                        text = self._build_text(state, now)
                        if text != state.last_edit_text:
                            state.last_edit_text = text
                            actions.append(Action("edit", key, text, state.tg_message_id))
            else:
                # ── below threshold → equalized ──
                if key in self.active:
                    state = self.active[key]
                    if not state.equalized:
                        state.equalized = True
                        state.history.append((now, pct))
                        msg_id = state.tg_message_id
                        if msg_id:
                            text = self._build_text(state, now, equalized=True)
                            if text != state.last_edit_text:
                                state.last_edit_text = text
                                actions.append(Action("edit", key, text, msg_id))
                        # set cooldown and remove from active
                        self.cooldowns[key] = now + EQUALIZE_COOLDOWN
                        del self.active[key]

        return actions

    # ── message builder ───────────────────────────────────────────────────

    def _build_text(self, s: AlertState, now: float, equalized: bool = False) -> str:
        emoji = "🟢" if s.initial_pct > 0 else "🔴"
        sign = "+" if s.initial_pct > 0 else ""
        lines = [
            f"{emoji} <b>{s.symbol}</b>  [{s.exchange.upper()}]",
            "",
            f"{s.direction}   {sign}{abs(s.initial_pct):.1f}%",
            "",
            f"FAIR  →  {_fmt_price(s.fair_price)}",
            f"LAST  →  {_fmt_price(s.last_price)}",
        ]
        if s.max_size_usd:
            lines.append(f"Max size: {_fmt_num(s.max_size_usd)}")
        if s.max_leverage:
            lines.append(f"Max lev: {int(s.max_leverage)}x")
        if s.volume_24h_usd:
            lines.append(f"24h vol: {_fmt_num(s.volume_24h_usd)}")

        # time-based changes (only if we have enough history)
        elapsed = now - s.first_seen
        if elapsed >= 8:
            lines.append("")
            lines.append("────────────")
            lines.append("⏱ Изменение")
            for secs, label in [(10, "10сек"), (20, "20сек"), (30, "30сек"), (60, "60сек")]:
                val = self._pct_at_offset(s, now, secs)
                if val is not None:
                    sv = "+" if val > 0 else ""
                    lines.append(f"{label}: {sv}{val:.1f}%")

        if equalized:
            lines.append("")
            lines.append("⚠️ <b>Сравнялся</b>")

        return "\n".join(lines)

    def _pct_at_offset(self, s: AlertState, now: float, secs: int) -> Optional[float]:
        target = now - secs
        best = None
        best_diff = 999999.0
        for ts, pct in s.history:
            d = abs(ts - target)
            if d < best_diff:
                best_diff = d
                best = pct
        if best is not None and best_diff < secs * 0.6:
            return best
        return None
