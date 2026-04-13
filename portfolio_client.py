"""
持倉損益查詢 — 供 LINE Bot 使用，回傳純文字
"""

import json
import os
import ssl
import urllib.request
from datetime import datetime

PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "portfolio.json")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://mis.twse.com.tw/",
}


def _get_price(code: str, market: str) -> tuple[float | None, str]:
    try:
        url = (
            f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
            f"?ex_ch={market}_{code}.tw&json=1&delay=0"
        )
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        msgs = data.get("msgArray", [])
        if msgs:
            q = msgs[0]
            name = q.get("n") or code
            z = q.get("z", "-")
            y = q.get("y", "-")
            price_str = z if z and z != "-" else y
            price = float(price_str)
            if price > 0:
                return price, name
    except Exception:
        pass

    # Fallback: Yahoo Finance（處理興櫃）
    try:
        import re
        suffix = ".TW" if market == "tse" else ".TWO"
        url = f"https://tw.stock.yahoo.com/quote/{code}{suffix}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            html = resp.read().decode("utf-8")
        m = re.search(r'"regularMarketPrice":\{"raw":([\d.]+)', html)
        if m:
            return float(m.group(1)), code
    except Exception:
        pass

    return None, code


def get_portfolio_summary() -> str:
    if not os.path.exists(PORTFOLIO_FILE):
        return "找不到持倉資料。"
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return "讀取持倉資料失敗。"

    positions = data.get("positions", [])
    if not positions:
        return "目前沒有持倉。"

    lines = [f"【持倉損益】{datetime.now().strftime('%m/%d %H:%M')}"]
    total_cost = total_value = 0.0

    for pos in positions:
        code   = pos["code"]
        market = pos.get("market", "tse")
        shares = pos.get("shares", 1000)
        cost   = pos["cost_per_share"]
        name   = pos.get("name", code)
        lots   = shares // 1000

        price, fetched = _get_price(code, market)
        if fetched and fetched != code:
            name = fetched

        cost_total = cost * shares

        if price is not None:
            change    = price - cost
            pct       = change / cost * 100
            pnl       = change * shares
            sign      = "+" if change >= 0 else ""
            trend     = "▲" if change > 0 else ("▼" if change < 0 else "─")
            cur_total = price * shares
            total_cost  += cost_total
            total_value += cur_total

            lines.append(
                f"\n{name}（{code}）{lots}張\n"
                f"現價 {price:.2f}  {trend} {sign}{change:.2f}（{sign}{pct:.1f}%）\n"
                f"成本 {cost:.2f}  損益 {sign}{pnl:,.0f} 元"
            )
        else:
            total_cost += cost_total
            lines.append(
                f"\n{name}（{code}）{lots}張\n"
                f"現價 無法取得\n"
                f"成本 {cost:.2f}"
            )

    if total_cost > 0:
        total_pnl = total_value - total_cost
        total_pct = total_pnl / total_cost * 100 if total_cost else 0
        sign = "+" if total_pnl >= 0 else ""
        lines.append(
            f"\n──────────\n"
            f"總損益：{sign}{total_pnl:,.0f} 元（{sign}{total_pct:.1f}%）"
        )

    return "\n".join(lines)
