from __future__ import annotations

"""OTA 真价核验：携程（隐匿暖场）→ 去哪儿 → 飞猪 → Google Flights。"""

import json
import logging
import random
import re
import time
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
    source: str = "Ctrip"
    airline: str = ""
    dep_time: str = ""
    arr_time: str = ""


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


def parse_google_outbound_cards(text: str) -> list[dict[str, Any]]:
    """
    从 Google Flights 正文解析去程卡片：
    14:45 – 19:20 / 香港快运航空 / … / ¥1,547
    """
    if not text:
        return []
    norm = (
        text.replace("\xa0", " ")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )
    pattern = re.compile(
        r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*\n\s*([^\n]{2,40}?)\s*\n"
        r".{0,120}?(?:¥|￥)\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,6})",
        re.S,
    )
    cards: list[dict[str, Any]] = []
    for m in pattern.finditer(norm):
        try:
            price = float(m.group(4).replace(",", ""))
        except ValueError:
            continue
        if not (_MIN_OTA_PRICE <= price <= _MAX_OTA_PRICE):
            continue
        airline = m.group(3).strip()
        if airline in {"最佳", "价格最低", "热门去程航班", "按热门航班排序"}:
            continue
        cards.append(
            {
                "dep_time": m.group(1),
                "arr_time": m.group(2),
                "airline": airline,
                "price": price,
            }
        )
    cards.sort(key=lambda c: c["price"])
    return cards


def google_flights_url(
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int = 1,
) -> str:
    o = resolve_airport(origin)
    d = resolve_airport(dest)
    q = f"Flights to {d} from {o} on {outbound_date} through {return_date}"
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


def _launch_browser(sync_playwright_fn: Any) -> Any:
    headless = _headless()
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
    ]
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
    }
    try:
        return sync_playwright_fn.chromium.launch(channel="chrome", **launch_kwargs)
    except Exception as e:
        _log.info("channel=chrome 不可用，回落 Chromium: %s", e)
        return sync_playwright_fn.chromium.launch(**launch_kwargs)


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
    if o_cn not in page_blob and o not in page.url.upper():
        raise CtripVerifyError(f"去哪儿结果页出发地不匹配（期望 {o_cn}/{o}）")
    if d_cn not in page_blob and d not in page.url.upper():
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


def _verify_google(
    page: Any,
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int,
    timeout_ms: int,
    page_url: str | None = None,
) -> OtaVerifyResult:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    o = resolve_airport(origin)
    d = resolve_airport(dest)
    url = page_url or google_flights_url(o, d, outbound_date, return_date, adults=adults)

    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 25000))
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(4000)

    content = page.content()
    body_text = page.inner_text("body") if page.locator("body").count() else ""
    if "unusual traffic" in body_text.lower() or _is_blocked_page(body_text):
        raise CtripVerifyError("Google Flights 触发反爬")

    cards = parse_google_outbound_cards(body_text)
    prices = extract_prices_from_text(body_text) + extract_prices_from_text(content)
    if cards:
        best = cards[0]
        lowest = float(best["price"])
        label = f"{best['airline']} {best['dep_time']}→{best['arr_time']}"
        return OtaVerifyResult(
            price=lowest,
            currency="CNY",
            summary=label,
            source="GoogleFlights",
            airline=str(best["airline"]),
            dep_time=str(best["dep_time"]),
            arr_time=str(best["arr_time"]),
        )

    lowest = pick_lowest(prices)
    if lowest is None:
        raise CtripVerifyError(
            f"未能从 Google Flights 解析到价格（{o}->{d} {outbound_date}/{return_date}）"
        )
    return OtaVerifyResult(
        price=lowest,
        currency="CNY",
        summary="",
        source="GoogleFlights",
    )


def _apply_verify_result(opt: Any, result: OtaVerifyResult) -> None:
    opt.verified_price = result.price
    opt.total_price = result.price
    opt.verify_status = "ok"
    opt.source = f"{result.source}Verified"
    existing = (opt.summary_outbound or "").strip()
    if result.summary:
        if (
            not existing
            or existing in {"待核验", "见核对"}
            or existing.startswith("Google核验")
            or "见核对" in existing
            or existing == "待核验"
        ):
            opt.summary_outbound = result.summary
        elif result.airline and result.airline not in existing:
            opt.summary_outbound = f"{existing} · {result.airline}"
    if not (opt.summary_return or "").strip() or opt.summary_return == "待核验":
        opt.summary_return = "回程见核对链接"


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
) -> OtaVerifyResult:
    """
    真价核验：携程 → 去哪儿 → 飞猪 → Google Flights。
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
    if skip_ctrip:
        st["skip_ctrip"] = True

    def _try_provider(name: str, skip_key: str, fn) -> OtaVerifyResult | None:
        if st.get(skip_key):
            return None
        page = _new_page(browser_ref)
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
            try:
                page.context.close()
            except Exception:
                pass

    browser_ref: Any = browser

    def _run(b: Any) -> OtaVerifyResult:
        nonlocal browser_ref
        browser_ref = b

        do_ctrip = not skip_ctrip and not st.get("skip_ctrip")
        if do_ctrip:
            got = _try_provider(
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
            if got:
                return got

        got = _try_provider(
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
        if got:
            return got

        got = _try_provider(
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
        if got:
            return got

        page = _new_page(b)
        try:
            return _verify_google(
                page,
                origin,
                dest,
                outbound_date,
                return_date,
                adults,
                timeout_ms,
                google_url,
            )
        except CtripVerifyError as e:
            errors.append(f"Google: {e}")
            raise
        finally:
            page.context.close()

    try:
        if owns_browser:
            with sync_playwright() as p:
                b = _launch_browser(p)
                try:
                    return _run(b)
                finally:
                    b.close()
        else:
            return _run(browser)
    except CtripVerifyError:
        raise CtripVerifyError(
            "OTA 核验失败（携程/去哪儿/飞猪/Google 均未拿到真价）。" + " | ".join(errors)
        )
    except PlaywrightTimeout as e:
        raise CtripVerifyError(f"OTA 页面超时: {e}") from e
    except Exception as e:
        raise CtripVerifyError(f"OTA 核验异常: {e}") from e


def verify_candidates(
    candidates: list[Any],
    origin: str,
    dest: str,
    adults: int = 1,
    timeout_ms: int = 45000,
    delay_sec: float = 1.0,
    on_each: Optional[Any] = None,
    target_ok: int | None = None,
) -> tuple[list[Any], list[str]]:
    """
    对候选 FlightOption 逐个核验（复用同一浏览器）。
    target_ok: 凑满成功条数即可提前结束（用于 Top-N）。
    同一次跑里若某源硬拦截，后续自动跳过该源。
    """
    if not playwright_available():
        raise CtripVerifyError(
            "未安装 Playwright。请执行: pip install playwright && playwright install chromium"
        )

    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    state: dict[str, Any] = {
        "skip_ctrip": False,
        "skip_qunar": False,
        "skip_fliggy": False,
    }
    goal = target_ok if target_ok is not None else len(candidates)
    ok_count = 0

    with sync_playwright() as p:
        browser = _launch_browser(p)
        try:
            for i, opt in enumerate(candidates):
                if ok_count >= goal:
                    for rest in candidates[i:]:
                        if rest.verify_status != "ok":
                            rest.verify_status = "skipped"
                    if on_each:
                        try:
                            on_each(len(candidates), len(candidates), None)
                        except TypeError:
                            on_each(len(candidates), len(candidates))
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
                    )
                    _apply_verify_result(opt, result)
                    ok_count += 1
                except CtripVerifyError as e:
                    opt.verify_status = "failed"
                    opt.verified_price = None
                    msg = f"{opt.outbound_date}/{opt.return_date}: {e}"
                    errors.append(msg)
                    _log.warning("OTA 核验失败 %s", msg)
                if on_each:
                    try:
                        on_each(i + 1, len(candidates), opt)
                    except TypeError:
                        on_each(i + 1, len(candidates))
                if i < len(candidates) - 1 and delay_sec > 0 and ok_count < goal:
                    time.sleep(delay_sec)
        finally:
            browser.close()

    return candidates, errors
