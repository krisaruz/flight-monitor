from __future__ import annotations

"""OTA 真价核验：优先携程，若 WhaleGuard/解析失败则改核验 Google Flights。"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

from app.services.deeplinks import ctrip_round_trip_url
from app.services.flight_search import resolve_airport

_log = logging.getLogger(__name__)

_PRICE_RE = re.compile(
    r"(?:¥|￥|CNY\s*)\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,6})(?:\.[0-9]+)?",
    re.IGNORECASE,
)

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
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
        if 50 <= v <= 200_000:
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
                    if 50 <= n <= 200_000:
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
        if not (50 <= price <= 200_000):
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
    )
    return any(n in t for n in needles) or "whaleguard block" in t


def _launch_browser(sync_playwright_fn: Any) -> Any:
    try:
        return sync_playwright_fn.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
    except Exception:
        return sync_playwright_fn.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )


def _new_page(browser: Any) -> Any:
    context = browser.new_context(
        locale="zh-CN",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1365, "height": 900},
        extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    page = context.new_page()
    page.add_init_script(_STEALTH_JS)
    return page


def _verify_ctrip(
    page: Any,
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int,
    timeout_ms: int,
    page_url: str | None,
) -> OtaVerifyResult:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    o = resolve_airport(origin)
    d = resolve_airport(dest)
    url = page_url or ctrip_round_trip_url(o, d, outbound_date, return_date, adults=adults)
    api_prices: list[float] = []

    def on_response(response: Any) -> None:
        try:
            if response.status != 200:
                return
            if not _looks_like_ctrip_api(response.url):
                return
            ctype = (response.headers.get("content-type") or "").lower()
            if "json" not in ctype and "javascript" not in ctype:
                return
            body = response.text()
            if not body or len(body) > 8_000_000:
                return
            api_prices.extend(extract_prices_from_json(json.loads(body)))
        except Exception:
            return

    page.on("response", on_response)
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 25000))
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(2500)

    content = page.content()
    body_text = page.inner_text("body") if page.locator("body").count() else ""
    if _is_blocked_page(body_text) or "whaleguard" in content.lower():
        raise CtripVerifyError("携程 WhaleGuard/反爬拦截")

    dom_prices = extract_prices_from_text(body_text) + extract_prices_from_text(content)
    lowest = pick_lowest(api_prices) or pick_lowest(dom_prices)
    if lowest is None:
        raise CtripVerifyError(f"未能从携程页面解析到价格（{o}->{d} {outbound_date}/{return_date}）")
    return OtaVerifyResult(
        price=lowest,
        currency="CNY",
        summary=f"携程核验 {outbound_date}→{return_date}",
        source="Ctrip",
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
    if "unusual traffic" in body_text.lower():
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
    skip_ctrip: bool = False,
    browser: Any | None = None,
    state: dict[str, Any] | None = None,
) -> OtaVerifyResult:
    """
    真价核验：先携程，失败则 Google Flights。
    两者都失败才抛错（fail-closed，不回落假数据）。
    """
    if not playwright_available():
        raise CtripVerifyError(
            "未安装 Playwright。请执行: pip install playwright && playwright install chromium"
        )

    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    owns_browser = browser is None

    def _run(b: Any) -> OtaVerifyResult:
        nonlocal errors
        do_ctrip = not skip_ctrip and not (state or {}).get("skip_ctrip")
        if do_ctrip:
            page = _new_page(b)
            try:
                return _verify_ctrip(
                    page,
                    origin,
                    dest,
                    outbound_date,
                    return_date,
                    adults,
                    timeout_ms,
                    page_url,
                )
            except CtripVerifyError as e:
                errors.append(f"Ctrip: {e}")
                _log.warning("携程核验失败，尝试 Google Flights: %s", e)
                if state is not None and ("WhaleGuard" in str(e) or "whaleguard" in str(e).lower()):
                    state["skip_ctrip"] = True
            finally:
                page.context.close()

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
            "OTA 核验失败（携程+Google 均未拿到真价）。" + " | ".join(errors)
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
    同一次跑里若携程 WhaleGuard，后续自动跳过携程直连 Google。
    """
    if not playwright_available():
        raise CtripVerifyError(
            "未安装 Playwright。请执行: pip install playwright && playwright install chromium"
        )

    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    state: dict[str, Any] = {"skip_ctrip": False}
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
