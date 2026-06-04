from __future__ import annotations

import json
import math
import os
import re
import time
import html as html_lib
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from xml.etree import ElementTree as ET

import requests


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
HEADERS = {"User-Agent": "Mozilla/5.0"}
NEWS_CACHE: dict[str, tuple[float, list[dict]]] = {}
NEWS_TTL_SECONDS = 30 * 60


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
    last_exc: Exception | None = None
    for _ in range(3):
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_exc = exc
            time.sleep(0.6)
    raise last_exc or RuntimeError("请求失败")


def get_text(url: str) -> str:
    last_exc: Exception | None = None
    for _ in range(3):
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_exc = exc
            time.sleep(0.6)
    raise last_exc or RuntimeError("请求失败")


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


def round_number(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def display_date(value: object) -> str:
    text = str(value or "")
    if re.fullmatch(r"\d{10}", text):
        return datetime.fromtimestamp(int(text)).strftime("%Y-%m-%d")
    return text


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


def recent_history(history: list[dict], limit: int = 80) -> list[dict]:
    rows = history[-limit:]
    return [
        {
            "date": display_date(row.get("date")),
            "open": round_number(row.get("open")),
            "close": round_number(row.get("close")),
            "high": round_number(row.get("high")),
            "low": round_number(row.get("low")),
            "volume": round_number(row.get("volume"), 0),
        }
        for row in rows
    ]


def trend_projection(history: list[dict], days: int = 8) -> dict:
    closes = [row["close"] for row in history if row.get("close") is not None]
    if len(closes) < 8:
        return {"direction": "样本不足", "slope_pct": None, "points": []}

    window = closes[-20:] if len(closes) >= 20 else closes
    n = len(window)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(window) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, window)) / denominator if denominator else 0
    intercept = mean_y - slope * mean_x
    last = window[-1]
    slope_pct = slope / last * 100 if last else None
    points = [
        {"step": idx + 1, "price": round_number(intercept + slope * (n + idx))}
        for idx in range(days)
    ]
    if slope_pct is None:
        direction = "中性"
    elif slope_pct > 0.35:
        direction = "上行延续"
    elif slope_pct < -0.35:
        direction = "下行修复"
    else:
        direction = "横盘震荡"
    return {"direction": direction, "slope_pct": round_number(slope_pct), "points": points}


def key_levels(quote_data: dict, ind: dict) -> dict:
    price = quote_data.get("price")
    support = ind.get("support20")
    resistance = ind.get("resistance20")
    ma20 = ind.get("ma20")

    if price is None:
        return {}

    stop_loss = support * 0.98 if support else price * 0.92
    if stop_loss >= price:
        stop_loss = min(price * 0.94, ma20 * 0.98 if ma20 else price * 0.94)

    take_profit_1 = resistance if resistance and resistance > price else price * 1.08
    take_profit_2 = max(take_profit_1 * 1.05, price * 1.15)
    risk = price - stop_loss
    reward = take_profit_1 - price
    risk_reward = reward / risk if risk > 0 else None

    return {
        "current": round_number(price),
        "support": round_number(support),
        "resistance": round_number(resistance),
        "ma5": round_number(ind.get("ma5")),
        "ma20": round_number(ma20),
        "ma60": round_number(ind.get("ma60")),
        "stop_loss": round_number(stop_loss),
        "take_profit_1": round_number(take_profit_1),
        "take_profit_2": round_number(take_profit_2),
        "risk_reward": round_number(risk_reward),
    }


def volume_analysis(quote_data: dict, history: list[dict], ind: dict) -> dict:
    volumes = [row["volume"] for row in history if row.get("volume") is not None]
    closes = [row["close"] for row in history if row.get("close") is not None]
    volume_ratio = ind.get("volume_ratio")
    pct = quote_data.get("pct")
    avg20 = average(volumes[-20:]) if len(volumes) >= 20 else None
    latest_volume = volumes[-1] if volumes else quote_data.get("volume")

    if volume_ratio is None:
        label = "量能样本不足"
    elif volume_ratio >= 1.8 and (pct or 0) > 0:
        label = "放量上攻"
    elif volume_ratio >= 1.8 and (pct or 0) < 0:
        label = "放量回撤"
    elif volume_ratio <= 0.7:
        label = "缩量观望"
    else:
        label = "量能平稳"

    note = "量价关系偏中性，等待更明确的放量方向。"
    if label == "放量上攻":
        note = "成交量明显放大且价格走强，短线资金关注度提升。"
    elif label == "放量回撤":
        note = "放量下跌说明分歧加大，需观察支撑位是否有效。"
    elif label == "缩量观望":
        note = "缩量阶段更适合等待突破或回踩确认。"
    elif len(closes) >= 2 and latest_volume and avg20:
        note = "当前量能接近20日均量，趋势更多取决于价格是否站稳均线。"

    return {
        "label": label,
        "note": note,
        "latest_volume": round_number(latest_volume, 0),
        "avg20_volume": round_number(avg20, 0),
        "volume_ratio": round_number(volume_ratio),
    }


def sector_profile(symbol: Symbol, quote_data: dict) -> dict:
    name = str(quote_data.get("name") or symbol.display_code)
    code = symbol.code.upper()

    rules = [
        {
            "tokens": ["光库", "中际", "新易盛", "天孚", "剑桥", "光迅", "华工", "COHR", "LITE", "MRVL"],
            "sector": "光通信与AI算力",
            "concepts": ["光模块", "CPO", "数据中心", "AI算力基础设施"],
            "us_peers": ["NVDA", "AVGO", "MRVL", "LITE", "COHR"],
        },
        {
            "tokens": ["腾讯", "阿里", "美团", "京东", "BABA", "TCEHY", "PDD"],
            "sector": "互联网平台",
            "concepts": ["云计算", "游戏", "广告", "本地生活", "电商平台"],
            "us_peers": ["META", "GOOGL", "AMZN", "MSFT", "NFLX"],
        },
        {
            "tokens": ["苹果", "AAPL"],
            "sector": "消费电子与AI终端",
            "concepts": ["AI手机", "消费电子", "生态服务", "端侧AI"],
            "us_peers": ["MSFT", "GOOGL", "META", "QCOM", "AVGO"],
        },
        {
            "tokens": ["特斯拉", "TSLA", "比亚迪", "宁德", "理想", "蔚来", "小鹏"],
            "sector": "新能源车与智能驾驶",
            "concepts": ["电动车", "动力电池", "智能驾驶", "储能"],
            "us_peers": ["TSLA", "RIVN", "GM", "F", "ALB"],
        },
        {
            "tokens": ["英伟达", "NVDA", "AMD", "博通", "AVGO", "台积电", "TSM"],
            "sector": "半导体与AI芯片",
            "concepts": ["AI芯片", "GPU", "先进制程", "服务器"],
            "us_peers": ["NVDA", "AMD", "AVGO", "TSM", "ASML"],
        },
        {
            "tokens": ["茅台", "五粮", "泸州", "DEO"],
            "sector": "消费与品牌白酒",
            "concepts": ["高端消费", "品牌护城河", "渠道库存"],
            "us_peers": ["DEO", "BUD", "KO", "PEP", "MNST"],
        },
        {
            "tokens": ["恒瑞", "迈瑞", "药明", "LLY", "PFE", "JNJ", "MRK"],
            "sector": "医药医疗",
            "concepts": ["创新药", "医疗器械", "CXO", "医保政策"],
            "us_peers": ["LLY", "PFE", "JNJ", "MRK", "TMO"],
        },
    ]

    haystack = f"{name} {code}".upper()
    selected = None
    for rule in rules:
        if any(token.upper() in haystack for token in rule["tokens"]):
            selected = rule
            break

    if selected is None:
        if symbol.market == "美股":
            selected = {
                "sector": "美股综合行业",
                "concepts": ["行业龙头", "盈利预期", "利率敏感", "美元资产"],
                "us_peers": ["SPY", "QQQ", "DIA", "IWM"],
            }
        elif symbol.market == "港股":
            selected = {
                "sector": "港股核心资产",
                "concepts": ["南向资金", "估值修复", "港股流动性"],
                "us_peers": ["KWEB", "FXI", "MCHI", "ASHR"],
            }
        else:
            selected = {
                "sector": "A股综合行业",
                "concepts": ["产业政策", "资金风格", "业绩预期"],
                "us_peers": ["ASHR", "MCHI", "KWEB", "FXI"],
            }

    query = f"{selected['sector']} {name}"
    return {
        "sector": selected["sector"],
        "concepts": selected["concepts"],
        "us_peers": selected["us_peers"],
        "mapping_note": "美股映射用于寻找全球同产业链或同商业模式参照，不代表估值完全可比。",
        "news_query": query,
        "links": {
            "板块新闻": f"https://www.bing.com/news/search?q={quote(selected['sector'])}&mkt=zh-CN",
            "板块排行": f"https://quote.eastmoney.com/center/boardlist.html#industry_board",
            "美股映射": f"https://finance.yahoo.com/lookup?s={quote(' '.join(selected['us_peers']))}",
        },
    }


def news_search_link(query: str) -> str:
    return f"https://www.bing.com/news/search?q={quote(query)}&mkt=zh-CN"


def google_news_rss_link(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote(query + ' when:30d')}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"


def bing_news_rss_link(query: str) -> str:
    return f"https://www.bing.com/news/search?q={quote(query)}&format=RSS&mkt=en-US"


def yahoo_finance_rss_link(symbol_code: str) -> str:
    return f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote(symbol_code)}&region=US&lang=en-US"


def clean_news_text(value: str | None) -> str:
    text = html_lib.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def trim_text(value: str, length: int = 92) -> str:
    if len(value) <= length:
        return value
    return value[: length - 1].rstrip() + "…"


def news_impact_label(text: str) -> str:
    upper = text.upper()
    rules = [
        (("AI", "算力", "芯片", "数据中心", "GPU"), "产业催化"),
        (("财报", "业绩", "收入", "利润", "指引", "盈利"), "业绩信号"),
        (("政策", "监管", "关税", "降息", "利率"), "宏观政策"),
        (("增持", "回购", "分红", "减持"), "资本动作"),
        (("合作", "订单", "发布", "新品", "量产"), "经营进展"),
        (("下跌", "调查", "诉讼", "风险", "亏损"), "风险事件"),
    ]
    for keywords, label in rules:
        if any(keyword.upper() in upper for keyword in keywords):
            return label
    return "市场动态"


def news_takeaway(title: str, impact: str) -> str:
    prefixes = {
        "产业催化": "关注产业催化和相关产业链扩散效应",
        "业绩信号": "关注财报、盈利预期或机构评级变化",
        "宏观政策": "关注政策、利率或监管变量对估值的影响",
        "资本动作": "关注回购、分红、增减持等资本动作",
        "经营进展": "关注订单、合作、新品或业务进展",
        "风险事件": "关注事件风险是否影响趋势和仓位纪律",
    }
    prefix = prefixes.get(impact, "关注市场情绪和资金关注度变化")
    return trim_text(f"{prefix}：{clean_news_text(title)}")


def build_news_summary(title: str, description: str, impact: str = "市场动态", source: str = "") -> str:
    clean_title = clean_news_text(title)
    clean_description = clean_news_text(description)
    clean_source = clean_news_text(source)
    if clean_source:
        clean_description = clean_description.replace(clean_source, "").strip(" -_｜|")
    if clean_description.startswith(clean_title):
        clean_description = clean_description[len(clean_title) :].strip(" -_｜|")
    if clean_description and clean_description != clean_title and len(clean_description) >= 18:
        return trim_text(clean_description)
    return news_takeaway(clean_title, impact)


def source_from_title(title: str, source: str) -> str:
    if source:
        return clean_news_text(source)
    if " - " in title:
        return clean_news_text(title.rsplit(" - ", 1)[-1])
    return ""


def title_without_source(title: str, source: str) -> str:
    clean_title = clean_news_text(title)
    clean_source = clean_news_text(source)
    if clean_source and clean_title.endswith(f" - {clean_source}"):
        return clean_title[: -len(clean_source) - 3].strip()
    return clean_title


def parse_rss_items(url: str, query: str, limit: int) -> list[dict]:
    response = requests.get(
        url,
        headers={
            **HEADERS,
            "Accept": "application/rss+xml, application/xml, text/xml, text/html",
        },
        timeout=10,
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "html" in content_type.lower() and not response.text.lstrip().startswith("<?xml"):
        return []

    root = ET.fromstring(response.content)
    items: list[dict] = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or "新闻"
        description = item.findtext("description") or ""
        source = source_from_title(title, item.findtext("source") or "")
        clean_title = title_without_source(title, source)
        impact = news_impact_label(f"{clean_title} {description}")
        summary = build_news_summary(clean_title, description, impact, source)
        if not clean_title or clean_title == "新闻":
            continue
        items.append(
            {
                "title": clean_title,
                "summary": summary,
                "impact": impact,
                "url": item.findtext("link") or news_search_link(query),
                "date": item.findtext("pubDate") or "",
                "source": source,
            }
        )
        if len(items) >= limit:
            break
    return items


def dedupe_news(items: list[dict], limit: int) -> list[dict]:
    seen: set[str] = set()
    clean: list[dict] = []
    for item in items:
        key = re.sub(r"\W+", "", item.get("title", "").lower())
        if not key or key in seen:
            continue
        seen.add(key)
        clean.append(item)
        if len(clean) >= limit:
            break
    return clean


def fetch_news_items(query: str, limit: int = 3, symbol: Symbol | None = None) -> list[dict]:
    key = f"{symbol.market if symbol else ''}:{symbol.code if symbol else ''}:{query}".strip().lower()
    now = time.time()
    cached = NEWS_CACHE.get(key)
    if cached and now - cached[0] < NEWS_TTL_SECONDS:
        return cached[1][:limit]

    urls: list[str] = []
    if symbol and symbol.market == "美股":
        urls.append(yahoo_finance_rss_link(symbol.code))
    urls.append(google_news_rss_link(query))
    urls.append(bing_news_rss_link(query))

    items: list[dict] = []
    for url in urls:
        try:
            items.extend(parse_rss_items(url, query, limit))
            items = dedupe_news(items, limit)
            if len(items) >= limit:
                break
        except Exception:
            continue

    if not items:
        items = [
            {
                "title": f"查看 {query} 最新新闻",
                "summary": f"暂未抓取到可提炼摘要，点击查看 {query} 的实时新闻列表。",
                "impact": "新闻入口",
                "url": news_search_link(query),
                "date": "",
                "source": "News Search",
            }
        ]

    NEWS_CACHE[key] = (now, items)
    return items[:limit]


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
    sector = sector_profile(symbol, quote_data)
    if symbol.market == "美股":
        news_query = f"{symbol.code} stock news latest earnings"
    else:
        news_query = f"{quote_data.get('name') or symbol.display_code} 股票 最新 动态"
    return {
        **quote_data,
        "raw": raw,
        "score": score,
        "risk": build_risks(quote_data, ind),
        "catalysts": build_catalysts(quote_data, ind, score),
        "indicators": ind,
        "levels": key_levels(quote_data, ind),
        "volume_analysis": volume_analysis(quote_data, history, ind),
        "projection": trend_projection(history),
        "history": recent_history(history),
        "sector": sector,
        "news": fetch_news_items(news_query, symbol=symbol),
        "sector_news": fetch_news_items(sector["news_query"]),
        "news_links": {
            "个股新闻": news_search_link(news_query),
            "板块新闻": sector["links"]["板块新闻"],
            "板块排行": sector["links"]["板块排行"],
        },
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
            "/lone-wolf-logo.png": ("lone-wolf-logo.png", "image/png"),
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
