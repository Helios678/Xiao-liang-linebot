"""
股票查詢模組 — 供 LINE Bot 使用，回傳純文字（無 ANSI 顏色）
"""

import json
import re
import ssl
import urllib.request

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://mis.twse.com.tw/",
}

# 常見股票名稱對照表（名稱 → (代碼, 市場)）
NAME_MAP = {
    "台積電": ("2330", "tse"),
    "鴻海": ("2317", "tse"),
    "聯發科": ("2454", "tse"),
    "台達電": ("2308", "tse"),
    "富邦金": ("2881", "tse"),
    "國泰金": ("2882", "tse"),
    "中華電": ("2412", "tse"),
    "統一": ("1216", "tse"),
    "台塑": ("1301", "tse"),
    "南亞": ("1303", "tse"),
    "玉山金": ("2884", "tse"),
    "元大金": ("2885", "tse"),
    "安基生技": ("7754", "otc"),
}


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_quote(code: str, market: str) -> dict | None:
    ex_ch = f"{market}_{code}.tw"
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex_ch}&json=1&delay=0"
    try:
        data = _fetch(url)
        msgs = data.get("msgArray", [])
        q = msgs[0] if msgs else None
        return q if (q and q.get("n")) else None
    except Exception:
        return None


def _pf(s) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def query_news(code: str) -> str:
    """查詢公司重大訊息（公開資訊觀測站），回傳純文字。"""
    import re
    url = (
        f"https://mops.twse.com.tw/mops/web/ajax_t05st01"
        f"?encodeURIComponent=1&step=1&firstin=1&off=1"
        f"&keyword4=&code1=&TYPEK2=&checkbtn=&queryName=co_id"
        f"&inpuType=co_id&TYPEK=all&isnew=false&co_id={code}&year=&season=&mtk="
    )
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            text = resp.read().decode("utf-8")
        pattern = r'<td[^>]*>\s*(\d{3}/\d{2}/\d{2})\s*</td>.*?<a[^>]*>([^<]+)</a>'
        matches = re.findall(pattern, text, re.DOTALL)
        if not matches:
            return f"查不到 {code} 的重大訊息，或目前無公告。"
        lines = [f"【{code} 重大訊息】"]
        for date, title in matches[:5]:
            lines.append(f"・{date}  {title.strip()}")
        return "\n".join(lines)
    except Exception as e:
        return f"無法取得 {code} 的新聞：{e}"


def query_stock(query: str) -> str:
    """查詢股票，回傳純文字結果。"""
    query = query.strip()
    q = None
    market = None

    # 先查名稱對照表
    if query in NAME_MAP:
        code, market = NAME_MAP[query]
        q = _get_quote(code, market)

    # 數字代碼：先試上市，再試上櫃
    elif re.match(r"^\d{4,6}$", query):
        code = query
        q = _get_quote(code, "tse")
        if q:
            market = "tse"
        else:
            q = _get_quote(code, "otc")
            if q:
                market = "otc"

    if not q:
        return (
            f"找不到股票「{query}」。\n"
            "請使用股票代碼查詢，例如：查2330、查7754\n"
            f"或常見名稱：{', '.join(NAME_MAP.keys())}"
        )

    name     = q.get("n", "?")
    code     = q.get("c", "?")
    price    = _pf(q.get("z"))
    prev     = _pf(q.get("y"))
    open_p   = _pf(q.get("o"))
    high     = _pf(q.get("h"))
    low      = _pf(q.get("l"))
    volume   = _pf(q.get("v"))
    limit_up = _pf(q.get("u"))
    limit_dn = _pf(q.get("w"))
    time_str = q.get("t", "-")
    date_str = q.get("d", "")

    lines = [f"【{name} {code}】"]
    if date_str and len(date_str) == 8:
        lines.append(f"更新時間：{date_str[:4]}/{date_str[4:6]}/{date_str[6:]} {time_str}")

    if price and prev:
        change = price - prev
        pct = change / prev * 100
        sign = "+" if change >= 0 else ""
        trend = "▲" if change > 0 else ("▼" if change < 0 else "─")
        status = ""
        if limit_up and abs(price - limit_up) < 0.01:
            status = "【漲停】"
        elif limit_dn and abs(price - limit_dn) < 0.01:
            status = "【跌停】"
        price_line = f"成交價：{price:.2f}  {trend}{sign}{change:.2f}（{sign}{pct:.2f}%）"
        if status:
            price_line += f"  {status}"
        lines.append(price_line)
        lines.append(f"昨收：{prev:.2f}")
    else:
        lines.append("成交價：尚無成交")

    row = []
    if open_p: row.append(f"開盤 {open_p:.2f}")
    if high:   row.append(f"最高 {high:.2f}")
    if low:    row.append(f"最低 {low:.2f}")
    if row:    lines.append("  ".join(row))

    if volume:
        lines.append(f"成交量：{volume:,.0f} 張")

    return "\n".join(lines)
