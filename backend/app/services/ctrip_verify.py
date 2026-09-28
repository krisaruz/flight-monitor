from __future__ import annotations

"""OTA 真价核验：按 OTA_VERIFY_PROVIDERS 顺序（默认 Google），可选代理出站。"""

import json
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

from app.config import settings
from app.services.deeplinks import (
    city_display_name,
    ctrip_round_trip_url,
    fliggy_round_trip_url,
    qunar_round_trip_url,
)
from app.services.flight_search import resolve_airport

_log = logging.getLogger(__name__)

_PRICE_RE = re.compile(
    r"(?:¥|￥|CNY\s*)\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,6})(?:\.[0-9]+)?",
    re.IGNORECASE,
)

# 往返核验价合理下限（过低多为辅营/营销条/噪声）
_MIN_OTA_PRICE = 450.0
_MAX_OTA_PRICE = 200_000.0
# 国内 H5 单程段价格带（去+回再相加）
_MIN_H5_LEG_PRICE = 200.0
_MAX_H5_LEG_PRICE = 20000.0

_H5_FLIGHT_RE = re.compile(
    # 允许中间换行：Playwright inner_text 常把时刻/机场拆到多行
    r"(\d{1,2}:\d{2})\s+[^¥￥]{0,80}?(\d{1,2}:\d{2})\s+[^¥￥]{0,40}?[¥￥]\s*(\d{3,5})"
    r"\s*([^¥￥\n]{0,48})",
)
_H5_FLIGHT_NO_RE = re.compile(r"([A-Z0-9]{2}\d{3,4})")
_GOOGLE_FLIGHT_NO_RE = re.compile(
    r"(?:航班\s*(?:号)?\s*)?([A-Z][A-Z0-9]\d{2,4})\b"
)

# IATA 航司码 → 中文简称（展示用）
_IATA_AIRLINE_CN = {
    "MU": "东航",
    "FM": "上航",
    "CZ": "南航",
    "CA": "国航",
    "HU": "海航",
    "3U": "川航",
    "GS": "天航",
    "ZH": "深航",
    "KN": "联航",
    "JD": "首航",
    "PN": "西部航",
    "HO": "吉祥",
    "9C": "春秋",
    "AQ": "九元",
    "G5": "华夏",
    "TV": "西藏航",
    "SC": "山航",
    "MF": "厦航",
    "BK": "奥凯",
    "NS": "河北航",
    "8L": "祥鹏",
    "KY": "昆航",
    "EU": "成航",
    "GJ": "长龙",
    "DZ": "东海航",
}


def _airline_cn_from_flight_no(flight_no: str) -> str:
    code = (flight_no or "").strip().upper()
    if len(code) < 2:
        return ""
    return _IATA_AIRLINE_CN.get(code[:2], "")

_IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 "
    "Mobile/15E148 Safari/604.1"
)

# 降低 Playwright/Chromium 常见自动化指纹（无法保证过 WhaleGuard，仅提高通过概率）
_STEALTH_JS = """
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  } catch (e) {}
  try {
    window.chrome = window.chrome || { runtime: {}, loadTimes: function() {}, csi: function() {} };
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'plugins', {
      get: () => [1, 2, 3, 4, 5],
    });
  } catch (e) {}
  try {
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
      parameters && parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
    );
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
  } catch (e) {}
})();
"""


@dataclass
class OtaVerifyResult:
    price: float
    currency: str = "CNY"
    summary: str = ""
    summary_return: str = ""
    source: str = "Ctrip"
    airline: str = ""
    dep_time: str = ""
    arr_time: str = ""
    return_airline: str = ""
    return_dep_time: str = ""
    return_arr_time: str = ""


_SCHEDULE_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_PLACEHOLDER_SUMMARIES = {
    "",
    "待核验",
    "见核对",
    "回程见核对链接",
    "回程待核验",
}


# 兼容旧名
CtripVerifyResult = OtaVerifyResult


class CtripVerifyError(RuntimeError):
    """OTA 核验失败（超时、验证码、结构变更等）。"""


def playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        return True
    except ImportError:
        return False


def extract_prices_from_text(text: str) -> list[float]:
    """从页面文本提取合理机票价区间（测试可直接调用）。"""
    if not text:
        return []
    found: list[float] = []
    for m in _PRICE_RE.finditer(text):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if _MIN_OTA_PRICE <= v <= _MAX_OTA_PRICE:
            found.append(v)
    return found


def _walk_prices(obj: Any, out: list[float], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if key in {
                "price",
                "lowestprice",
                "totalprice",
                "saleprice",
                "adultprice",
                "printprice",
                "total",
            } or key.endswith("price"):
                try:
                    n = float(v)
                    if _MIN_OTA_PRICE <= n <= _MAX_OTA_PRICE:
                        out.append(n)
                except (TypeError, ValueError):
                    pass
            _walk_prices(v, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:200]:
            _walk_prices(item, out, depth + 1)


def extract_prices_from_json(data: Any) -> list[float]:
    prices: list[float] = []
    _walk_prices(data, prices)
    return prices


def pick_lowest(prices: list[float]) -> float | None:
    if not prices:
        return None
    return float(min(prices))


def pick_ota_price(api_prices: list[float], dom_prices: list[float]) -> float | None:
    """
    优先接口价；DOM 价需足够多样且剔除离群低价，避免营销条 ¥2xx 冒充机票。
    """
    api = [p for p in api_prices if _MIN_OTA_PRICE <= p <= _MAX_OTA_PRICE]
    if api:
        return float(min(api))

    dom = sorted(p for p in dom_prices if _MIN_OTA_PRICE <= p <= _MAX_OTA_PRICE)
    if len(dom) < 2:
        return None
    uniq = sorted(set(dom))
    if len(uniq) == 1:
        # 单一重复价较可信（列表多条同价）
        return float(uniq[0]) if dom.count(uniq[0]) >= 3 else None
    med = uniq[len(uniq) // 2]
    robust = [p for p in uniq if p >= max(_MIN_OTA_PRICE, med * 0.4)]
    if not robust:
        return None
    return float(min(robust))


def _normalize_google_text(text: str) -> str:
    return (
        (text or "")
        .replace("\xa0", " ")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )


def _looks_like_schedule_summary(text: str) -> bool:
    s = (text or "").strip()
    if not s or s in _PLACEHOLDER_SUMMARIES:
        return False
    if "见核对" in s or s == "待核验":
        return False
    return bool(_SCHEDULE_TIME_RE.search(s))


def parse_google_flight_cards(text: str) -> list[dict[str, Any]]:
    """
    从 Google Flights 正文解析航班卡片：
    14:45 – 19:20 / 香港快运航空 / … / ¥1,547
    并尽量从邻近文本提取航班号（MU2151）。
    """
    if not text:
        return []
    norm = _normalize_google_text(text)
    pattern = re.compile(
        r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*\n\s*([^\n]{2,40}?)\s*\n"
        r".{0,220}?(?:¥|￥)\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,6})",
        re.S,
    )
    skip_airlines = {
        "最佳",
        "价格最低",
        "热门去程航班",
        "热门回程航班",
        "按热门航班排序",
        "按价格排序",
        "往返票价",
    }
    cards: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, float]] = set()
    for m in pattern.finditer(norm):
        try:
            price = float(m.group(4).replace(",", ""))
        except ValueError:
            continue
        if not (_MIN_OTA_PRICE <= price <= _MAX_OTA_PRICE):
            continue
        airline = m.group(3).strip()
        if airline in skip_airlines:
            continue
        window = norm[m.start() : min(len(norm), m.end() + 120)]
        fn_m = _GOOGLE_FLIGHT_NO_RE.search(window)
        flight_no = (fn_m.group(1) if fn_m else "").upper()
        key = (m.group(1), m.group(2), airline, price)
        if key in seen:
            continue
        seen.add(key)
        cards.append(
            {
                "dep_time": m.group(1),
                "arr_time": m.group(2),
                "airline": airline,
                "price": price,
                "flight_no": flight_no,
            }
        )
    cards.sort(key=lambda c: c["price"])
    return cards


def parse_google_cards_from_aria(page: Any) -> list[dict[str, Any]]:
    """从 aria-label 补解析班次（常含航班号，比纯正文稳）。"""
    try:
        labels = page.eval_on_selector_all(
            "[aria-label]",
            """els => els.map(e => e.getAttribute('aria-label') || '')
               .filter(t => /\\d{1,2}:\\d{2}/.test(t) && t.length < 500)
               .slice(0, 48)""",
        )
    except Exception:
        return []
    cards: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()
    time_re = re.compile(r"(\d{1,2}:\d{2})")
    price_re = re.compile(r"(?:¥|￥)\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,6})")
    for lab in labels or []:
        norm = _normalize_google_text(lab)
        times = time_re.findall(norm)
        if len(times) < 2:
            continue
        pm = price_re.search(norm)
        if not pm:
            continue
        try:
            price = float(pm.group(1).replace(",", ""))
        except ValueError:
            continue
        if not (_MIN_OTA_PRICE <= price <= _MAX_OTA_PRICE):
            continue
        fn_m = _GOOGLE_FLIGHT_NO_RE.search(norm)
        flight_no = (fn_m.group(1) if fn_m else "").upper()
        airline = _airline_cn_from_flight_no(flight_no)
        if not airline:
            for name in (
                "东航",
                "南航",
                "国航",
                "海航",
                "川航",
                "天航",
                "深航",
                "吉祥",
                "春秋",
                "厦航",
                "山航",
                "上航",
                "联航",
            ):
                if name in norm:
                    airline = name
                    break
        key = (times[0], times[1], price)
        if key in seen:
            continue
        seen.add(key)
        cards.append(
            {
                "dep_time": times[0],
                "arr_time": times[1],
                "airline": airline or "航班",
                "price": price,
                "flight_no": flight_no,
            }
        )
    cards.sort(key=lambda c: c["price"])
    return cards


def parse_google_outbound_cards(text: str) -> list[dict[str, Any]]:
    """兼容旧名：解析当前列表页航班卡片（去程或回程阶段均可）。"""
    return parse_google_flight_cards(text)


def _split_google_return_section(text: str) -> str:
    """截取回程选择区正文；找不到分区标记时返回空串。"""
    norm = _normalize_google_text(text)
    markers = (
        "选择回程航班",
        "选择返程航班",
        "选择回程",
        "返程航班",
        "回程航班",
        "Returning flights",
        "Choose returning flight",
        "Choose return flight",
        "Select return",
    )
    lower = norm.lower()
    best_idx = -1
    for marker in markers:
        idx = lower.find(marker.lower())
        if idx >= 0 and (best_idx < 0 or idx < best_idx):
            best_idx = idx
    if best_idx < 0:
        # 弱标记：页面从「去程」切到「回程」标题
        for marker in ("回程", "返程", "return"):
            idx = lower.find(marker)
            if idx >= 0 and (best_idx < 0 or idx < best_idx):
                best_idx = idx
    if best_idx < 0:
        return ""
    return norm[best_idx:]


def parse_google_return_cards(text: str) -> list[dict[str, Any]]:
    section = _split_google_return_section(text)
    if not section:
        return []
    return parse_google_flight_cards(section)


def _format_leg_summary(card: dict[str, Any]) -> str:
    """统一展示：航司 航班号 时刻，如「东航 MU2151 08:30→10:55」。"""
    airline = str(card.get("airline") or "").strip()
    flight_no = str(card.get("flight_no") or "").strip().upper()
    if flight_no == "N/A":
        flight_no = ""
    dep = str(card.get("dep_time") or card.get("dep") or "").strip()
    arr = str(card.get("arr_time") or card.get("arr") or "").strip()
    if not airline and flight_no:
        airline = _airline_cn_from_flight_no(flight_no)
    head_parts = []
    if airline:
        head_parts.append(airline)
    if flight_no and flight_no not in airline:
        head_parts.append(flight_no)
    head = " ".join(head_parts).strip()
    if head and dep and arr:
        return f"{head} {dep}→{arr}"
    if dep and arr:
        return f"{dep}→{arr}"
    return head


def _pick_return_card(
    return_cards: list[dict[str, Any]],
    outbound: dict[str, Any],
) -> dict[str, Any] | None:
    if not return_cards:
        return None
    target = float(outbound["price"])
    same_price = [c for c in return_cards if abs(float(c["price"]) - target) < 0.5]
    pool = same_price or return_cards
    # 避开与去程完全相同的卡片（偶发仍解析到去程列表）
    filtered = [
        c
        for c in pool
        if not (
            c["dep_time"] == outbound["dep_time"]
            and c["arr_time"] == outbound["arr_time"]
            and c["airline"] == outbound["airline"]
        )
    ]
    return (filtered or pool)[0]


def google_flights_url(
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int = 1,
) -> str:
    from app.services.flight_search import resolve_google_airport

    o = resolve_google_airport(origin)
    d = resolve_google_airport(dest)
    q = f"Flights to {d} from {o} on {outbound_date} through {return_date}"
    if adults > 1:
        q += f" for {adults} adults"
    return f"https://www.google.com/travel/flights?hl=zh-CN&curr=CNY&q={quote(q)}"


def google_flights_one_way_url(
    origin: str,
    dest: str,
    outbound_date: str,
    adults: int = 1,
) -> str:
    """Google Flights 单程深链（日历按天补洞用）。"""
    from app.services.flight_search import resolve_google_airport

    o = resolve_google_airport(origin)
    d = resolve_google_airport(dest)
    q = f"Flights to {d} from {o} on {outbound_date} one way"
    if adults > 1:
        q += f" for {adults} adults"
    return f"https://www.google.com/travel/flights?hl=zh-CN&curr=CNY&q={quote(q)}"


def _looks_like_ctrip_api(url: str) -> bool:
    u = url.lower()
    if "ctrip.com" not in u and "ctrip.cn" not in u:
        return False
    keys = (
        "itinerary",
        "products",
        "flightlist",
        "search",
        "batchsearch",
        "getflight",
        "international",
        "aggregate",
        "list",
    )
    return any(k in u for k in keys)


def _looks_like_qunar_api(url: str) -> bool:
    u = url.lower()
    if "qunar.com" not in u:
        return False
    keys = ("flight", "search", "price", "list", "oneway", "round", "touch", "api")
    return any(k in u for k in keys)


def _looks_like_fliggy_api(url: str) -> bool:
    u = url.lower()
    if "fliggy.com" not in u and "taobao.com" not in u and "alibaba.com" not in u:
        return False
    keys = ("flight", "search", "price", "item", "cheapest", "owb", "trip")
    return any(k in u for k in keys)


def _is_blocked_page(text: str) -> bool:
    t = (text or "").lower()
    needles = (
        "whaleguard",
        "验证码",
        "安全验证",
        "访问验证",
        "滑块",
        "人机验证",
        "unusual traffic",
        "please verify",
        "访问受限",
        "系统检测到",
        "抱歉，你的访问",
    )
    return any(n in t for n in needles) or "whaleguard block" in t


def _is_hard_block_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        k in msg
        for k in (
            "whaleguard",
            "反爬",
            "验证码",
            "安全验证",
            "人机验证",
            "unusual traffic",
            "访问受限",
        )
    )


def _headless() -> bool:
    return bool(getattr(settings, "ota_verify_headless", True))


def _verify_providers() -> list[str]:
    raw = (getattr(settings, "ota_verify_providers", None) or "google").strip()
    allowed = {"google", "qunar", "fliggy", "ctrip", "ctrip_h5"}
    out: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if name in allowed and name not in out:
            out.append(name)
    return out or ["google"]


def _ctrip_cticket() -> str:
    import os

    return (
        (getattr(settings, "ota_ctrip_cticket", None) or "").strip()
        or os.environ.get("OTA_CTRIP_CTICKET", "").strip()
        or os.environ.get("CTRIP_CTICKET", "").strip()
    )


def _ota_proxy() -> str | None:
    import os

    proxy = (getattr(settings, "ota_proxy", None) or "").strip()
    if not proxy:
        proxy = (
            os.environ.get("OTA_PROXY")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("all_proxy")
            or ""
        ).strip()
    return proxy or None


def _launch_browser(sync_playwright_fn: Any) -> Any:
    """优先系统 Chrome；可选 OTA_PROXY 走代理（国内机房访问 Google 必需）。"""
    return _launch_browser_with_proxy(sync_playwright_fn, proxy=_ota_proxy())


def _launch_browser_direct(sync_playwright_fn: Any) -> Any:
    """国内 OTA（ctrip_h5）专用：强制直连，避免 WARP 干扰。"""
    return _launch_browser_with_proxy(sync_playwright_fn, proxy=None)


def _display_ready() -> bool:
    """检查 DISPLAY 对应的 X11 socket 是否可用（避免 headed 启动却无 Xvfb）。"""
    import os
    import socket

    display = (os.environ.get("DISPLAY") or "").strip()
    if not display:
        return False
    # ":99" / "localhost:99.0" → 取 display number
    num = display.rsplit(":", 1)[-1].split(".", 1)[0]
    if not num.isdigit():
        return False
    path = f"/tmp/.X11-unix/X{num}"
    if not os.path.exists(path):
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(path)
        sock.close()
        return True
    except OSError:
        return False


def _is_missing_xserver_error(exc: BaseException) -> bool:
    text = str(exc)
    return (
        "XServer" in text
        or "xvfb-run" in text
        or "Missing X server" in text
        or "no display" in text.lower()
    )


def _launch_browser_with_proxy(sync_playwright_fn: Any, proxy: str | None) -> Any:
    import os

    # Playwright 1.49+ 默认 headless_shell，指纹更差；强制用完整 Chromium
    os.environ.setdefault("PLAYWRIGHT_CHROMIUM_USE_HEADLESS_SHELL", "0")

    headless = _headless()
    # 有头模式但无可用 Xvfb：直接降级 headless，避免整次扫描失败
    if not headless and not _display_ready():
        _log.warning(
            "OTA_VERIFY_HEADLESS=false 但 DISPLAY=%r 不可用，降级 headless=True",
            os.environ.get("DISPLAY"),
        )
        headless = True
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-gpu",
        "--window-size=1366,864",
    ]
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
    }
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
        _log.info("OTA 浏览器代理: %s", proxy)

    def _try_launch(kwargs: dict[str, Any]) -> Any:
        try:
            browser = sync_playwright_fn.chromium.launch(channel="chrome", **kwargs)
            _log.info(
                "OTA 浏览器: channel=chrome headless=%s proxy=%s providers=%s",
                kwargs.get("headless"),
                proxy or "direct",
                ",".join(_verify_providers()),
            )
            return browser
        except Exception as e:
            _log.warning("channel=chrome 不可用，回落完整 Chromium: %s", e)
            if _is_missing_xserver_error(e) and not kwargs.get("headless"):
                raise
            browser = sync_playwright_fn.chromium.launch(**kwargs)
            _log.info(
                "OTA 浏览器: bundled Chromium headless=%s proxy=%s",
                kwargs.get("headless"),
                proxy or "direct",
            )
            return browser

    try:
        return _try_launch(launch_kwargs)
    except Exception as e:
        if headless or not _is_missing_xserver_error(e):
            raise
        _log.warning("有头启动失败（无 XServer），重试 headless: %s", e)
        retry = dict(launch_kwargs)
        retry["headless"] = True
        return _try_launch(retry)


def _place_match_tokens(place: str) -> set[str]:
    """核验页城市匹配：兼容机场码/都市圈别名（如 OSA/KIX/大阪/关西）。"""
    raw = (place or "").strip()
    code = resolve_airport(raw)
    cn = city_display_name(raw)
    tokens = {raw, code, cn, code.upper(), cn.upper() if cn.isascii() else cn}
    metro = {
        "OSA": {"OSA", "KIX", "ITM", "大阪", "关西", "OSAKA"},
        "KIX": {"OSA", "KIX", "ITM", "大阪", "关西", "OSAKA"},
        "ITM": {"OSA", "KIX", "ITM", "大阪", "关西", "OSAKA"},
        "BJS": {"BJS", "PEK", "PKX", "北京"},
        "PEK": {"BJS", "PEK", "PKX", "北京"},
        "PKX": {"BJS", "PEK", "PKX", "北京"},
        "SHA": {"SHA", "PVG", "上海"},
        "PVG": {"SHA", "PVG", "上海"},
    }
    tokens.update(metro.get(code.upper(), set()))
    return {t for t in tokens if t}


def _page_matches_place(page_blob: str, url: str, place: str) -> bool:
    blob = f"{url}\n{page_blob}"
    blob_upper = blob.upper()
    for token in _place_match_tokens(place):
        if token.isascii():
            if token.upper() in blob_upper:
                return True
        elif token in blob:
            return True
    return False


def _new_page(browser: Any) -> Any:
    context = browser.new_context(
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 864},
        screen={"width": 1920, "height": 1080},
        color_scheme="light",
        has_touch=False,
        java_script_enabled=True,
        extra_http_headers={
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Upgrade-Insecure-Requests": "1",
        },
    )
    page = context.new_page()
    page.add_init_script(_STEALTH_JS)
    return page


def _new_h5_page(browser: Any, cticket: str) -> Any:
    """携程 H5 手机态 + 注入 cticket。"""
    context = browser.new_context(
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=_IPHONE_UA,
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
        java_script_enabled=True,
        extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    expires = time.time() + 86400 * 7
    cookies = []
    for domain in (".ctrip.com", "m.ctrip.com"):
        cookies.append(
            {
                "name": "cticket",
                "value": cticket,
                "domain": domain,
                "path": "/",
                "expires": expires,
            }
        )
    context.add_cookies(cookies)
    page = context.new_page()
    page.add_init_script(_STEALTH_JS)
    return page


def _parse_h5_flights(body: str) -> list[dict[str, Any]]:
    flights: list[dict[str, Any]] = []
    for m in _H5_FLIGHT_RE.finditer(body or ""):
        try:
            price = float(m.group(3))
        except Exception:
            continue
        if price < _MIN_H5_LEG_PRICE or price > _MAX_H5_LEG_PRICE:
            continue
        tail = m.group(4) or ""
        fn = ""
        fn_m = _H5_FLIGHT_NO_RE.search(tail)
        if fn_m:
            fn = fn_m.group(1)
        flights.append(
            {
                "dep": m.group(1),
                "arr": m.group(2),
                "price": price,
                "flight_no": fn,
                "raw": re.sub(r"\s+", " ", (m.group(0) or ""))[:120],
            }
        )
    # 去重：同 dep/arr/price 只留一条
    uniq: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for f in flights:
        key = (f["dep"], f["arr"], f["price"], f["flight_no"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    uniq.sort(key=lambda x: x["price"])
    return uniq


def _h5_soft_click(page: Any, selectors: list[str], timeout: int = 2500) -> str | None:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=timeout)
                page.wait_for_timeout(random.randint(900, 1600))
                return sel
        except Exception:
            continue
    return None


def _h5_click_flight(page: Any, flight: dict[str, Any]) -> str | None:
    """优先点最低价航班的时刻/价格文本。"""
    cands = []
    if flight.get("dep"):
        cands.append(f"text={flight['dep']}")
    if flight.get("price"):
        cands.append(f"text=¥{int(flight['price'])}")
        cands.append(f"text=￥{int(flight['price'])}")
    if flight.get("flight_no"):
        cands.append(f"text={flight['flight_no']}")
    cands.extend(["text=特惠航班", "text=订", "text=选择"])
    return _h5_soft_click(page, cands)


def _humanize(page: Any) -> None:
    """轻量拟人：短停 + 滚动，避免直达深链后零交互。"""
    try:
        page.wait_for_timeout(random.randint(400, 1100))
        page.mouse.move(random.randint(120, 700), random.randint(140, 480))
        page.mouse.wheel(0, random.randint(200, 700))
        page.wait_for_timeout(random.randint(300, 800))
    except Exception:
        pass


def _attach_json_price_listener(page: Any, matcher, bucket: list[float]) -> None:
    def on_response(response: Any) -> None:
        try:
            if response.status != 200:
                return
            if not matcher(response.url):
                return
            ctype = (response.headers.get("content-type") or "").lower()
            if "json" not in ctype and "javascript" not in ctype and "text" not in ctype:
                return
            body = response.text()
            if not body or len(body) > 8_000_000:
                return
            text = body.strip()
            if text.startswith("{") or text.startswith("["):
                bucket.extend(extract_prices_from_json(json.loads(text)))
            else:
                # JSONP / 前缀噪声
                l, r = text.find("{"), text.rfind("}")
                if l >= 0 and r > l:
                    bucket.extend(extract_prices_from_json(json.loads(text[l : r + 1])))
        except Exception:
            return

    page.on("response", on_response)


def _warmup(page: Any, home: str, timeout_ms: int, state: dict[str, Any] | None, flag: str) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    if state is not None and state.get(flag):
        return
    try:
        page.goto(home, wait_until="domcontentloaded", timeout=min(timeout_ms, 25000))
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeout:
            pass
        _humanize(page)
        if state is not None:
            state[flag] = True
    except Exception as e:
        _log.debug("暖场 %s 失败（可忽略）: %s", home, e)


def _verify_ctrip(
    page: Any,
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int,
    timeout_ms: int,
    page_url: str | None,
    state: dict[str, Any] | None = None,
) -> OtaVerifyResult:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    o = resolve_airport(origin)
    d = resolve_airport(dest)
    url = page_url or ctrip_round_trip_url(o, d, outbound_date, return_date, adults=adults)
    api_prices: list[float] = []
    _attach_json_price_listener(page, _looks_like_ctrip_api, api_prices)

    _warmup(page, "https://flights.ctrip.com/online/channel/domestic", timeout_ms, state, "warm_ctrip")
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 25000))
    except PlaywrightTimeout:
        pass
    _humanize(page)
    page.wait_for_timeout(random.randint(1800, 3200))

    content = page.content()
    body_text = page.inner_text("body") if page.locator("body").count() else ""
    if _is_blocked_page(body_text) or "whaleguard" in content.lower():
        raise CtripVerifyError("携程 WhaleGuard/反爬拦截")

    dom_prices = extract_prices_from_text(body_text) + extract_prices_from_text(content)
    lowest = pick_ota_price(api_prices, dom_prices)
    if lowest is None:
        raise CtripVerifyError(f"未能从携程页面解析到价格（{o}->{d} {outbound_date}/{return_date}）")
    return OtaVerifyResult(
        price=lowest,
        currency="CNY",
        summary=f"携程核验 {outbound_date}→{return_date}",
        source="Ctrip",
    )


def _ctrip_h5_first_url(origin: str, dest: str, outbound_date: str, return_date: str) -> str:
    o = resolve_airport(origin)
    d = resolve_airport(dest)
    return (
        "https://m.ctrip.com/html5/flight/pages/first"
        f"?dcity={o}&acity={d}&ddate={outbound_date}&adate={return_date}&flighttype=S"
    )


def _verify_ctrip_h5(
    page: Any,
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int,
    timeout_ms: int,
    state: dict[str, Any] | None = None,
) -> OtaVerifyResult:
    """
    携程 H5 + cticket 稳态核验：
    pages/first 去程列表 → 点最低价航班 → 回程列表 → 去+回最低价相加。
    """
    del adults  # H5 深链暂固定 1 成人
    o = resolve_airport(origin)
    d = resolve_airport(dest)
    o_cn, d_cn = city_display_name(o), city_display_name(d)
    url = _ctrip_h5_first_url(o, d, outbound_date, return_date)

    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(random.randint(2800, 4200))
    _h5_soft_click(page, ["text=知道了", "text=同意", "text=我知道了", "text=关闭"])
    _humanize(page)

    body = page.inner_text("body") if page.locator("body").count() else ""
    if _is_blocked_page(body) or "whaleguard" in (page.content() or "").lower():
        raise CtripVerifyError("携程H5 WhaleGuard/反爬拦截")
    blob = f"{page.url}\n{body}"
    if not _page_matches_place(blob, page.url, origin):
        raise CtripVerifyError(f"携程H5 出发地不匹配（期望 {o_cn}/{o}）")
    if not _page_matches_place(blob, page.url, dest):
        raise CtripVerifyError(f"携程H5 目的地不匹配（期望 {d_cn}/{d}）")
    if "北京" in body and "上海" in body and o_cn not in body and "珠海" not in body:
        # 常见误落首页：北京→上海默认态
        if o not in ("BJS", "PEK", "PKX", "SHA", "PVG") and d not in ("BJS", "PEK", "PKX", "SHA", "PVG"):
            raise CtripVerifyError("携程H5 落回默认首页（北京→上海），未进目标航线")

    out_flights = _parse_h5_flights(body)
    if not out_flights:
        raise CtripVerifyError(f"携程H5 未解析到去程航班（{o}->{d} {outbound_date}）")
    out_best = out_flights[0]
    clicked = _h5_click_flight(page, out_best)
    _log.info("ctrip_h5 去程最低 ¥%s %s-%s click=%s", out_best["price"], out_best["dep"], out_best["arr"], clicked)
    page.wait_for_timeout(random.randint(3200, 4800))

    body2 = page.inner_text("body") if page.locator("body").count() else ""
    if not any(k in body2 for k in ("返", "回程", "返程", "已选")):
        # 再点一次价格区域
        _h5_click_flight(page, out_best)
        page.wait_for_timeout(3000)
        body2 = page.inner_text("body") if page.locator("body").count() else ""
    if not any(k in body2 for k in ("返", "回程", "返程", "已选")):
        raise CtripVerifyError("携程H5 未进入回程选择页")

    # 若日历可见，点回程日期 MM-DD
    ret_md = return_date[5:] if len(return_date) >= 10 else return_date
    try:
        locs = page.locator(f"text=/{re.escape(ret_md)}/")
        n = min(locs.count(), 8)
        for i in range(n):
            try:
                locs.nth(i).click(timeout=1500)
                page.wait_for_timeout(2200)
                break
            except Exception:
                continue
    except Exception:
        pass

    body3 = page.inner_text("body") if page.locator("body").count() else ""
    if _is_blocked_page(body3):
        raise CtripVerifyError("携程H5 回程页触发安全验证")
    # 回程页常残留「去程已选」摘要；只解析「返」之后的航班块
    ret_body = body3
    for marker in ("返:", "返：", "返程", "回程", "返 "):
        idx = body3.find(marker)
        if idx >= 0:
            ret_body = body3[idx:]
            break
    ret_flights = _parse_h5_flights(ret_body)
    # 再滤掉与去程完全同时刻同价的残留
    ret_flights = [
        f
        for f in ret_flights
        if not (
            f["dep"] == out_best["dep"]
            and f["arr"] == out_best["arr"]
            and abs(f["price"] - out_best["price"]) < 0.5
        )
    ]
    # 滤掉把「去程到达时刻」误当成回程起飞的串线（如 23:45-08:05）
    ret_flights = [f for f in ret_flights if f["dep"] != out_best["arr"]] or ret_flights
    if not ret_flights:
        raise CtripVerifyError(f"携程H5 未解析到回程航班（{d}->{o} {return_date}）")
    ret_best = ret_flights[0]
    total = float(out_best["price"]) + float(ret_best["price"])
    if total < _MIN_OTA_PRICE or total > _MAX_OTA_PRICE:
        raise CtripVerifyError(f"携程H5 往返合计异常: {total}")

    out_sum = _format_leg_summary(
        {
            "airline": _airline_cn_from_flight_no(out_best.get("flight_no") or ""),
            "flight_no": out_best.get("flight_no") or "",
            "dep_time": out_best["dep"],
            "arr_time": out_best["arr"],
        }
    )
    ret_sum = _format_leg_summary(
        {
            "airline": _airline_cn_from_flight_no(ret_best.get("flight_no") or ""),
            "flight_no": ret_best.get("flight_no") or "",
            "dep_time": ret_best["dep"],
            "arr_time": ret_best["arr"],
        }
    )
    _log.info("ctrip_h5 核验成功 total=¥%s out=%s ret=%s", total, out_sum, ret_sum)
    if state is not None:
        state["warm_ctrip_h5"] = True
    return OtaVerifyResult(
        price=total,
        currency="CNY",
        summary=out_sum,
        summary_return=ret_sum,
        source="CtripH5",
        dep_time=out_best["dep"],
        arr_time=out_best["arr"],
        return_dep_time=ret_best["dep"],
        return_arr_time=ret_best["arr"],
        airline=out_best.get("flight_no") or "",
        return_airline=ret_best.get("flight_no") or "",
    )


def _verify_qunar(
    page: Any,
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int,
    timeout_ms: int,
    page_url: str | None = None,
    state: dict[str, Any] | None = None,
) -> OtaVerifyResult:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    o = resolve_airport(origin)
    d = resolve_airport(dest)
    url = page_url or qunar_round_trip_url(o, d, outbound_date, return_date)
    api_prices: list[float] = []
    _attach_json_price_listener(page, _looks_like_qunar_api, api_prices)

    _warmup(page, "https://flight.qunar.com/", timeout_ms, state, "warm_qunar")
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 25000))
    except PlaywrightTimeout:
        pass
    _humanize(page)
    # SPA：多等一会并尝试等到价格节点
    for _ in range(4):
        page.wait_for_timeout(1500)
        try:
            if page.locator("text=¥").count() >= 3 or page.locator("text=￥").count() >= 3:
                break
        except Exception:
            pass
    _humanize(page)

    content = page.content()
    body_text = page.inner_text("body") if page.locator("body").count() else ""
    if _is_blocked_page(body_text):
        raise CtripVerifyError("去哪儿触发安全验证/反爬")
    o_cn, d_cn = city_display_name(o), city_display_name(d)
    page_blob = f"{page.url}\n{body_text}"
    if not _page_matches_place(page_blob, page.url, origin):
        raise CtripVerifyError(f"去哪儿结果页出发地不匹配（期望 {o_cn}/{o}）")
    if not _page_matches_place(page_blob, page.url, dest):
        raise CtripVerifyError(f"去哪儿结果页目的地不匹配（期望 {d_cn}/{d}）")
    if not any(k in body_text for k in ("机票", "航班", "起", "往返", "直飞", "经济舱", "含税价")):
        raise CtripVerifyError("去哪儿未进入机票结果页")

    dom_prices = extract_prices_from_text(body_text) + extract_prices_from_text(content)
    lowest = pick_ota_price(api_prices, dom_prices)
    if lowest is None:
        raise CtripVerifyError(f"未能从去哪儿页面解析到价格（{o}->{d} {outbound_date}/{return_date}）")
    return OtaVerifyResult(
        price=lowest,
        currency="CNY",
        summary=f"去哪儿核验 {outbound_date}→{return_date}",
        source="Qunar",
    )


def _verify_fliggy(
    page: Any,
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int,
    timeout_ms: int,
    page_url: str | None = None,
    state: dict[str, Any] | None = None,
) -> OtaVerifyResult:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    o = resolve_airport(origin)
    d = resolve_airport(dest)
    url = page_url or fliggy_round_trip_url(o, d, outbound_date, return_date, adults=adults)
    api_prices: list[float] = []
    _attach_json_price_listener(page, _looks_like_fliggy_api, api_prices)

    _warmup(page, "https://www.fliggy.com/", timeout_ms, state, "warm_fliggy")
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 25000))
    except PlaywrightTimeout:
        pass
    _humanize(page)
    for _ in range(4):
        page.wait_for_timeout(1500)
        try:
            if page.locator("text=¥").count() >= 3 or page.locator("text=￥").count() >= 3:
                break
        except Exception:
            pass
    _humanize(page)

    content = page.content()
    body_text = page.inner_text("body") if page.locator("body").count() else ""
    if _is_blocked_page(body_text):
        raise CtripVerifyError("飞猪触发安全验证/反爬")

    dom_prices = extract_prices_from_text(body_text) + extract_prices_from_text(content)
    lowest = pick_ota_price(api_prices, dom_prices)
    if lowest is None:
        raise CtripVerifyError(f"未能从飞猪页面解析到价格（{o}->{d} {outbound_date}/{return_date}）")
    return OtaVerifyResult(
        price=lowest,
        currency="CNY",
        summary=f"飞猪核验 {outbound_date}→{return_date}",
        source="Fliggy",
    )


def _wait_google_price_ready(page: Any, rounds: int = 12, interval_ms: int = 400) -> None:
    """轮询等价格出现（短间隔，尽早跳出；无价时尽快放弃空等）。"""
    empty_hints = ("找不到符合条件", "未找到航班", "No flights", "no results", "尝试更改日期")
    for i in range(rounds):
        try:
            if page.locator("text=¥").count() >= 1 or page.locator("text=￥").count() >= 1:
                return
            if page.locator("text=$").count() >= 3:
                return
            # 2 轮后若页面已渲染且明确无结果，提前结束
            if i >= 2:
                body = (_google_body_text(page) or "")[:4000]
                if any(h.lower() in body.lower() for h in empty_hints):
                    return
        except Exception:
            pass
        page.wait_for_timeout(interval_ms)


def _google_body_text(page: Any) -> str:
    try:
        if page.locator("body").count():
            return page.inner_text("body") or ""
    except Exception:
        pass
    return ""


def _google_verify_mode() -> str:
    mode = str(getattr(settings, "google_verify_mode", "fast") or "fast").strip().lower()
    return mode if mode in {"fast", "full"} else "fast"


def _click_google_outbound_card(page: Any, card: dict[str, Any]) -> bool:
    """点选去程卡片，进入回程列表。"""
    dep = str(card.get("dep_time") or "")
    arr = str(card.get("arr_time") or "")
    airline = str(card.get("airline") or "")
    price = card.get("price")
    price_txt = ""
    if isinstance(price, (int, float)):
        price_txt = f"{int(price):,}"

    selectors: list[str] = []
    if dep and arr:
        selectors.extend(
            [
                f'a[aria-label*="{dep}"][aria-label*="{arr}"]',
                f'[role="link"][aria-label*="{dep}"][aria-label*="{arr}"]',
                f'[role="button"][aria-label*="{dep}"][aria-label*="{arr}"]',
                f'li:has-text("{dep}"):has-text("{arr}")',
                f'div[role="listitem"]:has-text("{dep}"):has-text("{arr}")',
            ]
        )
    if airline and dep:
        selectors.append(f'li:has-text("{airline}"):has-text("{dep}")')
    if price_txt and dep:
        selectors.append(f'li:has-text("{dep}"):has-text("¥{price_txt}")')
        selectors.append(f'li:has-text("{dep}"):has-text("￥{price_txt}")')
    selectors.extend(
        [
            'a[aria-label*="选择航班"]',
            'a[aria-label*="Select flight"]',
            'button[aria-label*="选择航班"]',
            'button[aria-label*="Select flight"]',
        ]
    )

    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.scroll_into_view_if_needed(timeout=2000)
            loc.click(timeout=4000)
            page.wait_for_timeout(400)
            return True
        except Exception:
            continue

    # 退化：点包含去程时刻的可点击节点
    if dep:
        try:
            loc = page.get_by_text(dep, exact=True).first
            if loc.count():
                loc.click(timeout=4000)
                page.wait_for_timeout(400)
                return True
        except Exception:
            pass
    return False


def _parse_return_after_outbound_click(
    page: Any,
    outbound: dict[str, Any],
) -> dict[str, Any] | None:
    """去程点选后轮询解析回程卡片（短轮询，失败由上层决定是否降级）。"""
    for i in range(8):
        page.wait_for_timeout(700 if i else 500)
        body = _google_body_text(page)
        if "unusual traffic" in body.lower() or _is_blocked_page(body):
            raise CtripVerifyError("Google Flights 触发反爬")
        cards = parse_google_return_cards(body)
        if not cards:
            all_cards = parse_google_flight_cards(body) + parse_google_cards_from_aria(page)
            cards = [
                c
                for c in all_cards
                if not (
                    c["dep_time"] == outbound["dep_time"]
                    and c["arr_time"] == outbound["arr_time"]
                    and abs(float(c["price"]) - float(outbound["price"])) < 0.5
                )
            ]
        else:
            # 用 aria 补航班号
            aria_cards = parse_google_cards_from_aria(page)
            by_key = {
                (c["dep_time"], c["arr_time"], float(c["price"])): c for c in aria_cards
            }
            for c in cards:
                ak = (c["dep_time"], c["arr_time"], float(c["price"]))
                if not c.get("flight_no") and ak in by_key:
                    c["flight_no"] = by_key[ak].get("flight_no") or ""
        picked = _pick_return_card(cards, outbound)
        if picked:
            return picked
        low = body.lower()
        if any(k in body for k in ("选择回程", "选择返程", "回程航班", "返程航班")) or "returning" in low:
            continue
    return None


def _verify_google(
    page: Any,
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int,
    timeout_ms: int,
    page_url: str | None = None,
    mode: str | None = None,
) -> OtaVerifyResult:
    from app.services.flight_search import resolve_google_airport

    o = resolve_google_airport(origin)
    d = resolve_google_airport(dest)
    # 始终按主机场重建 URL，避免任务里残留城市码（如 OSA）导致 Google 无结果
    url = google_flights_url(o, d, outbound_date, return_date, adults=adults)
    _ = page_url  # 保留参数兼容旧调用
    use_mode = (mode or _google_verify_mode()).strip().lower()
    if use_mode not in {"fast", "full"}:
        use_mode = "fast"
    mode = use_mode
    t0 = time.time()

    _log.info("Google Flights 打开: %s mode=%s", url, mode)
    t = time.time()
    # fast 用 commit 抢速度；full 用 domcontentloaded，给班次卡更多渲染时间
    if mode == "fast":
        try:
            page.goto(url, wait_until="commit", timeout=max(timeout_ms, 60000))
        except Exception:
            page.goto(url, wait_until="domcontentloaded", timeout=max(timeout_ms, 90000))
    else:
        page.goto(url, wait_until="domcontentloaded", timeout=max(timeout_ms, 90000))
    goto_ms = int((time.time() - t) * 1000)
    # 跳过 networkidle：Google/WARP 下常空等 10～45s，实测等 ¥ 出现更稳且更快
    t = time.time()
    _wait_google_price_ready(page, rounds=12 if mode == "fast" else 20)
    if mode == "full":
        # 价格先出来后班次卡可能再晚 1～2s
        page.wait_for_timeout(1500)
    wait_ms = int((time.time() - t) * 1000)

    content = page.content()
    body_text = _google_body_text(page)
    if "unusual traffic" in body_text.lower() or _is_blocked_page(body_text):
        raise CtripVerifyError("Google Flights 触发反爬")

    cards = parse_google_outbound_cards(body_text)
    aria_cards = parse_google_cards_from_aria(page)
    if aria_cards:
        # 合并：同价同时刻优先保留带航班号的
        merged: dict[tuple[str, str, float], dict[str, Any]] = {}
        for c in cards + aria_cards:
            key = (c["dep_time"], c["arr_time"], float(c["price"]))
            prev = merged.get(key)
            if prev is None or (c.get("flight_no") and not prev.get("flight_no")):
                merged[key] = c
        cards = sorted(merged.values(), key=lambda c: c["price"])
    prices = extract_prices_from_text(body_text) + extract_prices_from_text(content)
    if not cards and mode == "full":
        page.wait_for_timeout(2000)
        body_text = _google_body_text(page)
        content = page.content()
        cards = parse_google_outbound_cards(body_text)
        aria_cards = parse_google_cards_from_aria(page)
        if aria_cards:
            cards = sorted(
                { (c["dep_time"], c["arr_time"], float(c["price"])): c for c in cards + aria_cards }.values(),
                key=lambda c: c["price"],
            )
        prices = extract_prices_from_text(body_text) + extract_prices_from_text(content)
    if cards:
        best = cards[0]
        lowest = float(best["price"])
        out_label = _format_leg_summary(best)
        ret_label = ""
        ret_card: dict[str, Any] | None = None
        click_ms = 0
        ret_ms = 0

        # fast：往返搜索页去程卡片价即为往返总价，跳过点选回程（省 ~9s/组）
        if mode == "full":
            t = time.time()
            clicked = _click_google_outbound_card(page, best)
            click_ms = int((time.time() - t) * 1000)
            if clicked:
                t = time.time()
                try:
                    ret_card = _parse_return_after_outbound_click(page, best)
                except CtripVerifyError:
                    raise
                except Exception as e:
                    _log.warning("Google 回程解析异常: %s", e)
                    ret_card = None
                ret_ms = int((time.time() - t) * 1000)
            else:
                _log.warning(
                    "Google 未能点选去程卡片 %s %s→%s，降级为去程价",
                    best.get("airline"),
                    best.get("dep_time"),
                    best.get("arr_time"),
                )
            if ret_card:
                ret_label = _format_leg_summary(ret_card)
                try:
                    ret_price = float(ret_card["price"])
                    if _MIN_OTA_PRICE <= ret_price <= _MAX_OTA_PRICE:
                        lowest = ret_price
                except (TypeError, ValueError, KeyError):
                    pass
            else:
                # full 模式拿不到回程班次时仍接受去程卡片价，避免整组失败重试拖慢
                _log.info(
                    "Google 回程未解析，接受去程卡片价 ¥%s（%s→%s %s/%s）",
                    int(lowest),
                    o,
                    d,
                    outbound_date,
                    return_date,
                )

        total_ms = int((time.time() - t0) * 1000)
        _log.info(
            "Google核验耗时 mode=%s goto=%sms wait=%sms click=%sms ret=%sms total=%sms ok=¥%s",
            mode,
            goto_ms,
            wait_ms,
            click_ms,
            ret_ms,
            total_ms,
            int(lowest),
        )
        return OtaVerifyResult(
            price=lowest,
            currency="CNY",
            summary=out_label,
            summary_return=ret_label,
            source="GoogleFlights",
            airline=str(best["airline"]),
            dep_time=str(best["dep_time"]),
            arr_time=str(best["arr_time"]),
            return_airline=str((ret_card or {}).get("airline") or ""),
            return_dep_time=str((ret_card or {}).get("dep_time") or ""),
            return_arr_time=str((ret_card or {}).get("arr_time") or ""),
        )

    lowest = pick_lowest(prices)
    if lowest is None:
        raise CtripVerifyError(
            f"未能从 Google Flights 解析到价格（{o}->{d} {outbound_date}/{return_date}）"
        )
    # 有往返总价但班次卡解析失败：fast 必接受；full 降级保留价（班次留空，不冲掉密核结果）
    if _MIN_OTA_PRICE <= lowest <= _MAX_OTA_PRICE:
        total_ms = int((time.time() - t0) * 1000)
        _log.warning(
            "Google %s 仅有价无班次卡，接受往返价 ¥%s（%s→%s %s/%s）total=%sms",
            mode,
            int(lowest),
            o,
            d,
            outbound_date,
            return_date,
            total_ms,
        )
        return OtaVerifyResult(
            price=lowest,
            currency="CNY",
            # 不写假班次文案；前端在无班次时展示「见核对入口」
            summary="",
            summary_return="",
            source="GoogleFlights",
        )
    raise CtripVerifyError(
        f"Google Flights 仅解析到价格、无去/回程班次（{o}->{d} {outbound_date}/{return_date}）"
    )


def scrape_google_one_way_day_price(
    page: Any,
    origin: str,
    dest: str,
    outbound_date: str,
    adults: int = 1,
    timeout_ms: int = 45000,
) -> float:
    """Google 单程列表当天最低价（日历补洞）。"""
    from app.services.flight_search import resolve_google_airport

    o = resolve_google_airport(origin)
    d = resolve_google_airport(dest)
    url = google_flights_one_way_url(o, d, outbound_date, adults=adults)
    _log.info("Google 单程打开: %s", url)
    try:
        page.goto(url, wait_until="commit", timeout=max(timeout_ms, 60000))
    except Exception:
        page.goto(url, wait_until="domcontentloaded", timeout=max(timeout_ms, 90000))
    _wait_google_price_ready(page, rounds=12)

    content = page.content()
    body_text = _google_body_text(page)
    if "unusual traffic" in body_text.lower() or _is_blocked_page(body_text):
        raise CtripVerifyError("Google Flights 触发反爬")

    cards = parse_google_outbound_cards(body_text)
    aria_cards = parse_google_cards_from_aria(page)
    if aria_cards:
        merged: dict[tuple[str, str, float], dict[str, Any]] = {}
        for c in cards + aria_cards:
            key = (c["dep_time"], c["arr_time"], float(c["price"]))
            prev = merged.get(key)
            if prev is None or (c.get("flight_no") and not prev.get("flight_no")):
                merged[key] = c
        cards = sorted(merged.values(), key=lambda c: c["price"])
    if cards:
        lowest = float(cards[0]["price"])
        if _MIN_OTA_PRICE <= lowest <= _MAX_OTA_PRICE:
            return lowest
        # 单程价可能低于往返下限；放宽到 H5 单腿带
        if 100.0 <= lowest <= _MAX_OTA_PRICE:
            return lowest
        raise CtripVerifyError(f"Google 单程价异常: {lowest}")

    prices = extract_prices_from_text(body_text) + extract_prices_from_text(content)
    lowest = pick_lowest(prices)
    if lowest is None:
        raise CtripVerifyError(
            f"未能从 Google Flights 解析到单程价（{o}->{d} {outbound_date}）"
        )
    if 100.0 <= lowest <= _MAX_OTA_PRICE:
        return float(lowest)
    raise CtripVerifyError(f"Google 单程价异常: {lowest}")


def scrape_google_one_way_days(
    origin: str,
    dest: str,
    days: list[str],
    adults: int = 1,
    timeout_ms: int = 45000,
    delay_sec: float = 0.3,
    on_each: Optional[Any] = None,
    should_stop: Optional[Any] = None,
    deadline_ts: float | None = None,
) -> dict[str, float]:
    """
    批量 Google 单程 fast 扫日最低价（日历补洞）。
    复用同一浏览器；失败日跳过，不 fail-closed。
    """
    if not days:
        return {}
    if not playwright_available():
        raise CtripVerifyError(
            "未安装 Playwright。请执行: pip install playwright && playwright install chromium"
        )

    from playwright.sync_api import sync_playwright

    cal: dict[str, float] = {}
    total = len(days)
    with sync_playwright() as p:
        browser = _launch_browser(p)
        try:
            page = _new_page(browser)
            for i, day in enumerate(days, 1):
                if should_stop and should_stop():
                    break
                if deadline_ts is not None and time.time() >= deadline_ts:
                    break
                try:
                    price = scrape_google_one_way_day_price(
                        page,
                        origin,
                        dest,
                        day,
                        adults=adults,
                        timeout_ms=timeout_ms,
                    )
                    if price is not None and price > 0:
                        cal[day] = float(price)
                except Exception as e:
                    _log.warning("Google 单程日历日失败 %s→%s %s: %s", origin, dest, day, e)
                if on_each:
                    try:
                        on_each(i, total, day, cal.get(day))
                    except TypeError:
                        on_each(i, total)
                if delay_sec > 0 and i < total:
                    time.sleep(delay_sec)
        finally:
            try:
                browser.close()
            except Exception:
                pass
    return cal


def _apply_verify_result(opt: Any, result: OtaVerifyResult) -> None:
    opt.verified_price = result.price
    opt.total_price = result.price
    opt.verify_status = "ok"
    opt.source = f"{result.source}Verified"
    # Google 核验成功时以同一次点选得到的去/回程为准，避免与 TP 缓存班次混拼
    if result.summary and _looks_like_schedule_summary(result.summary):
        opt.summary_outbound = result.summary
    elif result.summary:
        existing = (opt.summary_outbound or "").strip()
        if not existing or existing in _PLACEHOLDER_SUMMARIES or "见核对" in existing:
            opt.summary_outbound = result.summary
        elif result.airline and result.airline not in existing:
            opt.summary_outbound = f"{existing} · {result.airline}"

    if result.summary_return and _looks_like_schedule_summary(result.summary_return):
        opt.summary_return = result.summary_return
    else:
        existing_ret = (opt.summary_return or "").strip()
        if existing_ret in _PLACEHOLDER_SUMMARIES or "见核对" in existing_ret:
            opt.summary_return = ""
        elif not _looks_like_schedule_summary(existing_ret):
            opt.summary_return = ""


def verify_round_trip(
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int = 1,
    timeout_ms: int = 45000,
    page_url: str | None = None,
    google_url: str | None = None,
    qunar_url: str | None = None,
    fliggy_url: str | None = None,
    skip_ctrip: bool = False,
    browser: Any | None = None,
    state: dict[str, Any] | None = None,
    providers: list[str] | None = None,
    google_mode: str | None = None,
) -> OtaVerifyResult:
    """
    真价核验：按 providers（或 OTA_VERIFY_PROVIDERS）顺序尝试。
    全部失败才抛错（fail-closed，不回落假数据）。
    """
    if not playwright_available():
        raise CtripVerifyError(
            "未安装 Playwright。请执行: pip install playwright && playwright install chromium"
        )

    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    owns_browser = browser is None
    st = state if state is not None else {}
    if providers:
        allowed = {"google", "qunar", "fliggy", "ctrip", "ctrip_h5"}
        use_providers = [p for p in providers if p in allowed]
        if not use_providers:
            use_providers = _verify_providers()
    else:
        use_providers = _verify_providers()
    providers = use_providers
    if skip_ctrip or "ctrip" not in providers:
        st["skip_ctrip"] = True
    if "ctrip_h5" not in providers:
        st["skip_ctrip_h5"] = True
    elif not _ctrip_cticket():
        st["skip_ctrip_h5"] = True
        errors.append("ctrip_h5: 未配置 OTA_CTRIP_CTICKET/CTRIP_CTICKET")
    if "qunar" not in providers:
        st["skip_qunar"] = True
    if "fliggy" not in providers:
        st["skip_fliggy"] = True
    if "google" not in providers:
        st["skip_google"] = True

    def _try_provider(name: str, skip_key: str, fn) -> OtaVerifyResult | None:
        if st.get(skip_key):
            return None
        # Google 密核阶段可复用同一 page，避免每组新建 context
        reuse = st.get("reuse_google_page") if name == "Google" else None
        owns_page = reuse is None
        page = reuse if reuse is not None else _new_page(browser_ref)
        try:
            return fn(page)
        except Exception as e:
            # 导航/反爬/解析失败都继续下一源，避免单源异常打断整条回落链
            errors.append(f"{name}: {e}")
            _log.warning("%s核验失败，尝试下一源: %s", name, e)
            if _is_hard_block_error(e) or "ERR_HTTP" in str(e) or "RESPONSE_CODE_FAILURE" in str(e):
                st[skip_key] = True
            return None
        finally:
            if owns_page:
                try:
                    page.context.close()
                except Exception:
                    pass

    def _try_ctrip_h5() -> OtaVerifyResult | None:
        if st.get("skip_ctrip_h5"):
            return None
        ticket = _ctrip_cticket()
        if not ticket:
            errors.append("ctrip_h5: 未配置 Cookie")
            st["skip_ctrip_h5"] = True
            return None
        pw = st.get("playwright")
        if pw is None:
            errors.append("ctrip_h5: 缺少 playwright 句柄")
            return None
        direct = None
        page = None
        try:
            direct = _launch_browser_direct(pw)
            page = _new_h5_page(direct, ticket)
            return _verify_ctrip_h5(
                page,
                origin,
                dest,
                outbound_date,
                return_date,
                adults,
                timeout_ms,
                state=st,
            )
        except Exception as e:
            errors.append(f"ctrip_h5: {e}")
            _log.warning("携程H5核验失败，尝试下一源: %s", e)
            if _is_hard_block_error(e):
                st["skip_ctrip_h5"] = True
            return None
        finally:
            try:
                if page is not None:
                    page.context.close()
            except Exception:
                pass
            try:
                if direct is not None:
                    direct.close()
            except Exception:
                pass

    browser_ref: Any = browser

    def _run_one(name: str) -> OtaVerifyResult | None:
        if name == "ctrip_h5":
            return _try_ctrip_h5()
        if name == "google":
            g_mode = google_mode or (st.get("google_mode") if st else None)
            return _try_provider(
                "Google",
                "skip_google",
                lambda page, _m=g_mode: _verify_google(
                    page,
                    origin,
                    dest,
                    outbound_date,
                    return_date,
                    adults,
                    timeout_ms,
                    google_url,
                    mode=_m,
                ),
            )
        if name == "qunar":
            return _try_provider(
                "去哪儿",
                "skip_qunar",
                lambda page: _verify_qunar(
                    page,
                    origin,
                    dest,
                    outbound_date,
                    return_date,
                    adults,
                    timeout_ms,
                    qunar_url,
                    state=st,
                ),
            )
        if name == "fliggy":
            return _try_provider(
                "飞猪",
                "skip_fliggy",
                lambda page: _verify_fliggy(
                    page,
                    origin,
                    dest,
                    outbound_date,
                    return_date,
                    adults,
                    timeout_ms,
                    fliggy_url,
                    state=st,
                ),
            )
        if name == "ctrip":
            return _try_provider(
                "携程",
                "skip_ctrip",
                lambda page: _verify_ctrip(
                    page,
                    origin,
                    dest,
                    outbound_date,
                    return_date,
                    adults,
                    timeout_ms,
                    page_url,
                    state=st,
                ),
            )
        return None

    def _run(b: Any) -> OtaVerifyResult:
        nonlocal browser_ref
        browser_ref = b
        for name in providers:
            got = _run_one(name)
            if got:
                return got
        raise CtripVerifyError(
            "OTA 核验失败（已尝试: "
            + ",".join(providers)
            + "）。"
            + " | ".join(errors)
        )

    try:
        if owns_browser:
            with sync_playwright() as p:
                st["playwright"] = p
                b = _launch_browser(p)
                try:
                    return _run(b)
                finally:
                    b.close()
        else:
            return _run(browser)
    except CtripVerifyError:
        raise
    except PlaywrightTimeout as e:
        raise CtripVerifyError(f"OTA 页面超时: {e}") from e
    except Exception as e:
        raise CtripVerifyError(f"OTA 核验异常: {e}") from e


def _google_verify_workers() -> int:
    """Google 单源并行路数，钳制在 1–3（小机器避免 OOM）。"""
    raw = int(getattr(settings, "google_verify_workers", 1) or 1)
    return max(1, min(3, raw))


def _mark_remaining_skipped(candidates: list[Any], start: int = 0) -> None:
    for rest in candidates[start:]:
        st = getattr(rest, "verify_status", None) or ""
        # 勿覆盖已失败/已成功；仅标记未尝试项
        if st not in ("ok", "failed"):
            rest.verify_status = "skipped"


def _emit_on_each(on_each: Optional[Any], done: int, total: int, opt: Any = None) -> None:
    if not on_each:
        return
    try:
        on_each(done, total, opt)
    except TypeError:
        on_each(done, total)


def verify_candidates(
    candidates: list[Any],
    origin: str,
    dest: str,
    adults: int = 1,
    timeout_ms: int = 45000,
    delay_sec: float = 1.0,
    on_each: Optional[Any] = None,
    target_ok: int | None = None,
    should_stop: Optional[Any] = None,
    providers: list[str] | None = None,
    deadline_ts: float | None = None,
    google_mode: str | None = None,
) -> tuple[list[Any], list[str]]:
    """
    对候选 FlightOption 核验。
    - Google 单源且 GOOGLE_VERIFY_WORKERS>1：多浏览器并行（每线程独立 Playwright）
    - 其它情况：单浏览器串行（可复用 page）
    """
    if not playwright_available():
        raise CtripVerifyError(
            "未安装 Playwright。请执行: pip install playwright && playwright install chromium"
        )

    allowed = {"google", "qunar", "fliggy", "ctrip", "ctrip_h5"}
    if providers:
        use_providers = [p for p in providers if p in allowed]
    else:
        use_providers = _verify_providers()
    if not use_providers:
        use_providers = ["google"]

    base_state: dict[str, Any] = {
        "skip_ctrip": "ctrip" not in use_providers,
        "skip_ctrip_h5": "ctrip_h5" not in use_providers or not _ctrip_cticket(),
        "skip_qunar": "qunar" not in use_providers,
        "skip_fliggy": "fliggy" not in use_providers,
        "skip_google": "google" not in use_providers,
    }
    if google_mode:
        base_state["google_mode"] = str(google_mode).strip().lower()

    workers = _google_verify_workers()
    parallel = use_providers == ["google"] and workers > 1 and len(candidates) > 1
    _log.info(
        "OTA 核验源: %s google_mode=%s workers=%s parallel=%s",
        ",".join(use_providers),
        base_state.get("google_mode") or _google_verify_mode(),
        workers if parallel else 1,
        parallel,
    )

    if parallel:
        return _verify_candidates_parallel_google(
            candidates=candidates,
            origin=origin,
            dest=dest,
            adults=adults,
            timeout_ms=timeout_ms,
            delay_sec=delay_sec,
            on_each=on_each,
            target_ok=target_ok,
            should_stop=should_stop,
            deadline_ts=deadline_ts,
            base_state=base_state,
            workers=workers,
        )
    return _verify_candidates_serial(
        candidates=candidates,
        origin=origin,
        dest=dest,
        adults=adults,
        timeout_ms=timeout_ms,
        delay_sec=delay_sec,
        on_each=on_each,
        target_ok=target_ok,
        should_stop=should_stop,
        use_providers=use_providers,
        deadline_ts=deadline_ts,
        base_state=base_state,
    )


def _verify_candidates_serial(
    candidates: list[Any],
    origin: str,
    dest: str,
    adults: int,
    timeout_ms: int,
    delay_sec: float,
    on_each: Optional[Any],
    target_ok: int | None,
    should_stop: Optional[Any],
    use_providers: list[str],
    deadline_ts: float | None,
    base_state: dict[str, Any],
) -> tuple[list[Any], list[str]]:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    state = dict(base_state)
    goal = target_ok if target_ok is not None else len(candidates)
    ok_count = 0

    with sync_playwright() as p:
        state["playwright"] = p
        if use_providers == ["ctrip_h5"]:
            browser = _launch_browser_direct(p)
        else:
            browser = _launch_browser(p)
        reuse_page = None
        try:
            if use_providers == ["google"]:
                reuse_page = _new_page(browser)
                state["reuse_google_page"] = reuse_page
            for i, opt in enumerate(candidates):
                if should_stop and should_stop():
                    _mark_remaining_skipped(candidates, i)
                    errors.append("用户取消")
                    break
                if deadline_ts is not None and time.time() >= deadline_ts:
                    _mark_remaining_skipped(candidates, i)
                    errors.append("核验预算耗尽")
                    _log.info("核验达到 deadline，跳过剩余 %s 组", len(candidates) - i)
                    break
                if ok_count >= goal:
                    _mark_remaining_skipped(candidates, i)
                    _emit_on_each(on_each, len(candidates), len(candidates), None)
                    break
                try:
                    o_code = getattr(opt, "origin_code", None) or origin
                    d_code = getattr(opt, "dest_code", None) or dest
                    result = verify_round_trip(
                        o_code,
                        d_code,
                        opt.outbound_date,
                        opt.return_date,
                        adults=adults,
                        timeout_ms=timeout_ms,
                        page_url=opt.verify_url_ctrip or None,
                        google_url=opt.verify_url or None,
                        qunar_url=getattr(opt, "verify_url_qunar", None) or None,
                        fliggy_url=None,
                        skip_ctrip=bool(state.get("skip_ctrip")),
                        browser=browser,
                        state=state,
                        providers=use_providers,
                        google_mode=state.get("google_mode"),
                    )
                    _apply_verify_result(opt, result)
                    ok_count += 1
                except CtripVerifyError as e:
                    opt.verify_status = "failed"
                    opt.verified_price = None
                    msg = f"{opt.outbound_date}/{opt.return_date}: {e}"
                    errors.append(msg)
                    _log.warning("OTA 核验失败 %s", msg)
                _emit_on_each(on_each, i + 1, len(candidates), opt)
                if i < len(candidates) - 1 and delay_sec > 0 and ok_count < goal:
                    if deadline_ts is not None and time.time() + delay_sec >= deadline_ts:
                        continue
                    time.sleep(delay_sec)
        finally:
            if reuse_page is not None:
                try:
                    reuse_page.context.close()
                except Exception:
                    pass
            browser.close()

    return candidates, errors


def _verify_candidates_parallel_google(
    candidates: list[Any],
    origin: str,
    dest: str,
    adults: int,
    timeout_ms: int,
    delay_sec: float,
    on_each: Optional[Any],
    target_ok: int | None,
    should_stop: Optional[Any],
    deadline_ts: float | None,
    base_state: dict[str, Any],
    workers: int,
) -> tuple[list[Any], list[str]]:
    """Google 单源：N 路独立 Chrome 并行拉活（Playwright sync 非线程安全，禁共用 browser）。"""
    from playwright.sync_api import sync_playwright

    goal = target_ok if target_ok is not None else len(candidates)
    total = len(candidates)
    lock = threading.Lock()
    stop = threading.Event()
    next_i = 0
    ok_count = 0
    done_count = 0
    errors: list[str] = []

    def claim() -> tuple[int, Any] | None:
        nonlocal next_i, ok_count
        with lock:
            if stop.is_set():
                return None
            if should_stop and should_stop():
                stop.set()
                errors.append("用户取消")
                return None
            if deadline_ts is not None and time.time() >= deadline_ts:
                stop.set()
                if "核验预算耗尽" not in errors:
                    errors.append("核验预算耗尽")
                return None
            if ok_count >= goal:
                stop.set()
                return None
            while next_i < total:
                i = next_i
                next_i += 1
                opt = candidates[i]
                if getattr(opt, "verify_status", None) == "ok":
                    continue
                return i, opt
            stop.set()
            return None

    def worker(wid: int) -> None:
        nonlocal ok_count, done_count
        state = dict(base_state)
        with sync_playwright() as p:
            state["playwright"] = p
            browser = _launch_browser(p)
            reuse_page = _new_page(browser)
            state["reuse_google_page"] = reuse_page
            try:
                _log.info("Google 并行 worker-%s 已启动", wid)
                while not stop.is_set():
                    claimed = claim()
                    if claimed is None:
                        break
                    _i, opt = claimed
                    try:
                        o_code = getattr(opt, "origin_code", None) or origin
                        d_code = getattr(opt, "dest_code", None) or dest
                        result = verify_round_trip(
                            o_code,
                            d_code,
                            opt.outbound_date,
                            opt.return_date,
                            adults=adults,
                            timeout_ms=timeout_ms,
                            page_url=opt.verify_url_ctrip or None,
                            google_url=opt.verify_url or None,
                            qunar_url=getattr(opt, "verify_url_qunar", None) or None,
                            fliggy_url=None,
                            skip_ctrip=True,
                            browser=browser,
                            state=state,
                            providers=["google"],
                            google_mode=state.get("google_mode"),
                        )
                        _apply_verify_result(opt, result)
                        with lock:
                            ok_count += 1
                            if ok_count >= goal:
                                stop.set()
                    except CtripVerifyError as e:
                        opt.verify_status = "failed"
                        opt.verified_price = None
                        msg = f"{opt.outbound_date}/{opt.return_date}: {e}"
                        with lock:
                            errors.append(msg)
                        _log.warning("OTA 核验失败 %s", msg)
                    with lock:
                        done_count += 1
                        cur_done = done_count
                    _emit_on_each(on_each, cur_done, total, opt)
                    if delay_sec > 0 and not stop.is_set():
                        if deadline_ts is not None and time.time() + delay_sec >= deadline_ts:
                            continue
                        time.sleep(delay_sec)
            finally:
                try:
                    reuse_page.context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass
                _log.info("Google 并行 worker-%s 已退出", wid)

    n = min(workers, total)
    _log.info("Google 并行核验启动 workers=%s candidates=%s goal=%s", n, total, goal)
    with ThreadPoolExecutor(max_workers=n, thread_name_prefix="gf-google") as pool:
        futs = [pool.submit(worker, i + 1) for i in range(n)]
        for fut in as_completed(futs):
            exc = fut.exception()
            if exc is not None:
                _log.exception("Google 并行 worker 异常: %s", exc)
                with lock:
                    errors.append(f"并行 worker 异常: {exc}")

    _mark_remaining_skipped(candidates, 0)
    if ok_count >= goal:
        _emit_on_each(on_each, total, total, None)
    _log.info(
        "Google 并行核验结束 ok=%s/%s done=%s errors=%s",
        ok_count,
        goal,
        done_count,
        len(errors),
    )
    return candidates, errors
