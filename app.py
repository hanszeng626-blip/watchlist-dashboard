from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import requests


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
HEADERS = {"User-Agent": "Mozilla/5.0"}


@dataclass(frozen=True)
class Symbol:
    raw: str
    market: str
    code: str
    quote_code: str
    yahoo_code: str
    display_code: str


def to_float(value: object) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def split_symbols(raw: str) -> list[str]:
    parts = re.split(r"[\s,;，；、]+", raw.strip())
    return [part.strip() for part in parts if part.strip()]


def parse_symbol(raw: str) -> Symbol:
    text = raw.strip().upper()
    text = text.replace("$", "")

    if re.fullmatch(r"(SH|SZ)\d{6}", text):
        prefix = text[:2].lower()
        code = text[2:]
        yahoo_suffix = "SS" if prefix == "sh" else "SZ"
        return Symbol(raw, "A股", code, prefix + code, f"{code}.{yahoo_suffix}", prefix.upper() + code)

    if re.fullmatch(r"\d{6}", text):
        prefix = "sh" if text.startswith(("5", "6", "9")) else "sz"
        yahoo_suffix = "SS" if prefix == "sh" else "SZ"
        return Symbol(raw, "A股", text, prefix + text, f"{text}.{yahoo_suffix}", prefix.upper() + text)

    hk_match = re.fullmatch(r"(?:HK)?(\d{1,5})(?:\.HK)?", text)
    if hk_match:
        code = hk_match.group(1).zfill(5)
        return Symbol(raw, "港股", code, "hk" + code, f"{code}.HK", "HK" + code)

    us_match = re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", text)
    if us_match:
        code = text.split(".")[0]
        return Symbol(raw, "美股", code, "us" + code, code, code)

    raise ValueError(f"无法识别股票代码：{raw}")


def get_json(url: str) -> dict:
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def get_text(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.text


def fetch_quote(symbol: Symbol) -> dict:
    text = get_text(f"https://qt.gtimg.cn/q={symbol.quote_code}")
    if "v_pv_none_match" in text:
        raise ValueError("腾讯行情接口未匹配到该代码")
    raw = text.split('="', 1)[1].rsplit('"', 1)[0]
    arr = raw.split("~")

    price = to_float(arr[3] if len(arr) > 3 else None)
    prev_close = to_float(arr[4] if len(arr) > 4 else None)
    change = to_float(arr[31] if len(arr) > 31 else None)
    pct = to_float(arr[32] if len(arr) > 32 else None)
    amount = to_float(arr[37] if len(arr) > 37 else None)
    volume = to_float(arr[36] if len(arr) > 36 else None) or to_float(arr[6] if len(arr) > 6 else None)

    return {
        "name": arr[1] if len(arr) > 1 else symbol.display_code,
        "code": symbol.display_code,
        "market": symbol.market,
        "price": price,
        "prev_close": prev_close,
        "open": to_float(arr[5] if len(arr) > 5 else None),
        "high": to_float(arr[33] if len(arr) > 33 else None),
        "low": to_float(arr[34] if len(arr) > 34 else None),
        "change": change,
        "pct": pct,
        "volume": volume,
        "amount": amount,
        "time": arr[30] if len(arr) > 30 else "",
        "pe": to_float(arr[39] if len(arr) > 39 else None),
        "pb": to_float(arr[46] if len(arr) > 46 else None),
        "source": f"https://qt.gtimg.cn/q={symbol.quote_code}",
    }


def fetch_tencent_history(symbol: Symbol, days: int = 120) -> list[dict]:
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol.quote_code},day,,,{days},qfq"
    data = get_json(url)
    block = data.get("data", {}).get(symbol.quote_code, {})
    rows = block.get("qfqday") or block.get("day") or []
    return [
        {
            "date": row[0],
            "open": to_float(row[1]),
            "close": to_float(row[2]),
            "high": to_float(row[3]),
            "low": to_float(row[4]),
            "volume": to_float(row[5]),
        }
        for row in rows
        if len(row) >= 6
    ]


def fetch_yahoo_history(symbol: Symbol) -> list[dict]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol.yahoo_code)}?range=6mo&interval=1d"
    data = get_json(url)
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        return []
    timestamps = result.get("timestamp") or []
    quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
    opens = quote_data.get("open") or []
    closes = quote_data.get("close") or []
    highs = quote_data.get("high") or []
    lows = quote_data.get("low") or []
    volumes = quote_data.get("volume") or []

    rows = []
    for idx, timestamp in enumerate(timestamps):
        close = to_float(closes[idx] if idx < len(closes) else None)
        if close is None:
            continue
        rows.append(
            {
                "date": str(timestamp),
                "open": to_float(opens[idx] if idx < len(opens) else None),
                "close": close,
                "high": to_float(highs[idx] if idx < len(highs) else None),
                "low": to_float(lows[idx] if idx < len(lows) else None),
                "volume": to_float(volumes[idx] if idx < len(volumes) else None),
            }
        )
    return rows


def fetch_history(symbol: Symbol) -> list[dict]:
    if symbol.market in {"A股", "港股"}:
        rows = fetch_tencent_history(symbol)
        if len(rows) >= 30:
            return rows
    return fetch_yahoo_history(symbol)


def average(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def moving_average(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return average(closes[-period:])


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, current in zip(closes[-period - 1 : -1], closes[-period:]):
        delta = current - prev
        gains.append(max(delta, 0))
        losses.append(abs(min(delta, 0)))
    avg_gain = average(gains) or 0
    avg_loss = average(losses) or 0
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def pct_delta(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline in (None, 0):
        return None
    return (current - baseline) / baseline * 100


def indicators(history: list[dict], price: float | None) -> dict:
    closes = [row["close"] for row in history if row.get("close") is not None]
    highs = [row["high"] for row in history if row.get("high") is not None]
    lows = [row["low"] for row in history if row.get("low") is not None]
    volumes = [row["volume"] for row in history if row.get("volume") is not None]

    current = price or (closes[-1] if closes else None)
    ma5 = moving_average(closes, 5)
    ma10 = moving_average(closes, 10)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    support = min(lows[-20:]) if len(lows) >= 20 else None
    resistance = max(highs[-20:]) if len(highs) >= 20 else None
    vol5 = average(volumes[-5:]) if len(volumes) >= 5 else None
    vol20 = average(volumes[-20:]) if len(volumes) >= 20 else None
    volume_ratio = vol5 / vol20 if vol5 and vol20 else None

    return {
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "rsi14": calc_rsi(closes),
        "support20": support,
        "resistance20": resistance,
        "distance_ma20_pct": pct_delta(current, ma20),
        "distance_support_pct": pct_delta(current, support),
        "distance_resistance_pct": pct_delta(current, resistance),
        "volume_ratio": volume_ratio,
        "history_points": len(history),
    }


def build_score(quote_data: dict, ind: dict) -> int:
    score = 50
    price = quote_data.get("price")
    pct = quote_data.get("pct")
    ma5 = ind.get("ma5")
    ma10 = ind.get("ma10")
    ma20 = ind.get("ma20")
    rsi = ind.get("rsi14")
    vol_ratio = ind.get("volume_ratio")
    support_distance = ind.get("distance_support_pct")

    if price and ma20 and price > ma20:
        score += 10
    if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
        score += 12
    if pct is not None and pct > 0:
        score += 5
    if rsi is not None and 45 <= rsi <= 65:
        score += 8
    if vol_ratio is not None and 1 <= vol_ratio <= 2:
        score += 5
    if support_distance is not None and 0 <= support_distance <= 6:
        score += 5

    if price and ma20 and price < ma20:
        score -= 10
    if rsi is not None and rsi > 75:
        score -= 8
    if rsi is not None and rsi < 30:
        score -= 5
    if pct is not None and pct < -3:
        score -= 8
    if ind.get("history_points", 0) < 20:
        score -= 6

    return max(0, min(100, score))


def build_risks(quote_data: dict, ind: dict) -> list[str]:
    risks: list[str] = []
    price = quote_data.get("price")
    pct = quote_data.get("pct")
    ma20 = ind.get("ma20")
    rsi = ind.get("rsi14")
    support_distance = ind.get("distance_support_pct")

    if ind.get("history_points", 0) < 20:
        risks.append("历史K线不足，评分参考性下降")
    if price and ma20 and price < ma20:
        risks.append("收盘价低于20日均线，趋势修复仍需观察")
    if support_distance is not None and 0 <= support_distance <= 3:
        risks.append("价格临近20日低点，需防止支撑失效")
    if rsi is not None and rsi > 75:
        risks.append("RSI偏高，短线追高风险上升")
    if pct is not None and pct <= -4:
        risks.append("当日跌幅较大，短线情绪偏弱")
    return risks or ["未见突出的技术风险，仍需结合仓位和消息面跟踪"]


def build_catalysts(quote_data: dict, ind: dict, score: int) -> list[str]:
    catalysts: list[str] = []
    pct = quote_data.get("pct")
    price = quote_data.get("price")
    ma20 = ind.get("ma20")
    vol_ratio = ind.get("volume_ratio")
    resistance_distance = ind.get("distance_resistance_pct")

    if score >= 70:
        catalysts.append("趋势评分较高，适合重点跟踪延续性")
    if price and ma20 and price > ma20 and vol_ratio and vol_ratio >= 1:
        catalysts.append("价格位于20日均线上方且近期量能不弱")
    if resistance_distance is not None and -5 <= resistance_distance <= 0:
        catalysts.append("接近20日高点区域，关注放量突破")
    if pct is not None and pct >= 3:
        catalysts.append("当日涨幅较强，可能带来短线关注度")
    return catalysts or ["暂无明确技术催化，优先等待量价或消息面确认"]


def external_links(symbol: Symbol) -> dict:
    if symbol.market == "A股":
        prefix = symbol.quote_code[:2]
        code = symbol.code
        return {
            "东方财富": f"https://quote.eastmoney.com/{prefix}{code}.html",
            "同花顺": f"https://stockpage.10jqka.com.cn/{code}/",
            "雪球": f"https://xueqiu.com/S/{prefix.upper()}{code}",
            "新浪财经": f"https://finance.sina.com.cn/realstock/company/{prefix}{code}/nc.shtml",
        }
    if symbol.market == "港股":
        code = symbol.code
        ths_code = f"HK{int(code):04d}"
        return {
            "东方财富": f"https://quote.eastmoney.com/hk/{code}.html",
            "同花顺": f"https://stockpage.10jqka.com.cn/{ths_code}/",
            "雪球": f"https://xueqiu.com/S/HK{code}",
            "新浪财经": f"https://stock.finance.sina.com.cn/hkstock/quotes/{code}.html",
        }
    code = symbol.code
    return {
        "东方财富": f"https://quote.eastmoney.com/us/{code}.html",
        "同花顺": f"https://stockpage.10jqka.com.cn/{code}/",
        "雪球": f"https://xueqiu.com/S/{code}",
        "新浪财经": f"https://stock.finance.sina.com.cn/usstock/quotes/{code}.html",
    }


def analyze_one(raw: str) -> dict:
    symbol = parse_symbol(raw)
    quote_data = fetch_quote(symbol)
    history = fetch_history(symbol)
    ind = indicators(history, quote_data.get("price"))
    score = build_score(quote_data, ind)
    return {
        **quote_data,
        "raw": raw,
        "score": score,
        "risk": build_risks(quote_data, ind),
        "catalysts": build_catalysts(quote_data, ind, score),
        "indicators": ind,
        "links": external_links(symbol),
    }


def analyze_symbols(raw_symbols: str) -> dict:
    symbols = split_symbols(raw_symbols)
    records: list[dict] = []
    errors: list[dict] = []
    seen: set[str] = set()

    for raw in symbols:
        try:
            parsed = parse_symbol(raw)
            key = f"{parsed.market}:{parsed.code}"
            if key in seen:
                continue
            seen.add(key)
            records.append(analyze_one(raw))
        except Exception as exc:
            errors.append({"symbol": raw, "message": str(exc)})

    grouped = {"A股": [], "港股": [], "美股": []}
    for record in records:
        grouped.setdefault(record["market"], []).append(record)

    return {
        "records": records,
        "grouped": grouped,
        "errors": errors,
        "summary": {
            "count": len(records),
            "avg_score": round(sum(item["score"] for item in records) / len(records), 1) if records else None,
            "strong": max(records, key=lambda item: item["score"]) if records else None,
            "weak": min(records, key=lambda item: item["score"]) if records else None,
        },
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/analyze":
            params = parse_qs(parsed.query)
            symbols = (params.get("symbols") or [""])[0]
            self.send_json(200, analyze_symbols(symbols))
            return

        if parsed.path in {"/", "/index.html"}:
            self.send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        static_files = {
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "application/javascript; charset=utf-8"),
        }
        if parsed.path in static_files:
            filename, content_type = static_files[parsed.path]
            self.send_file(STATIC_DIR / filename, content_type)
            return

        self.send_json(404, {"error": "Not found"})


def run(host: str | None = None, port: int | None = None) -> None:
    host = host or os.environ.get("HOST", "127.0.0.1")
    port = port or int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Watchlist dashboard: http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
