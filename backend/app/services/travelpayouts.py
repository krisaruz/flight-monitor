from __future__ import annotations

"""Travelpayouts / Aviasales Data API — 免费缓存市场价格（发现阶段）。"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from urllib.parse import quote

from app.services.flight_search import FlightOption, FlightSegment, resolve_airport

_log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]

BASE = "https://api.travelpayouts.com"


def _http_opener() -> urllib.request.OpenerDirector:
    """默认直连，避免 Windows 残留代理（如 127.0.0.1:7897）未启动时 WinError 10061。

    需要代理时显式设置环境变量 HTTPS_PROXY / HTTP_PROXY，或 TRAVELPAYOUTS_PROXY。
    """
    proxy = (
        os.environ.get("TRAVELPAYOUTS_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or ""
    ).strip()
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    # ProxyHandler({}) = 强制不走系统代理
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _network_error_hint(err: BaseException) -> str:
    text = str(err)
    if "10061" in text or "actively refused" in text.lower() or "积极拒绝" in text:
        return (
            f"{err}。"
            "常见原因：系统/环境配置了本地代理（如 127.0.0.1:7890/7897）但代理软件未开启。"
            "请启动 Clash/V2Ray 等，或关闭系统代理后重试；"
            "若必须走代理，请设置 HTTPS_PROXY=http://127.0.0.1:端口 并重启服务。"
        )
    return text

# 机场三字码 → Travelpayouts 常用城市码
AIRPORT_TO_CITY = {
    "KIX": "OSA",
    "ITM": "OSA",
    "NRT": "TYO",
    "HND": "TYO",
    "PVG": "SHA",
    "SHA": "SHA",
    "PEK": "BJS",
    "PKX": "BJS",
    "ICN": "SEL",
    "GMP": "SEL",
    "XIY": "SIA",  # 西安：机场码 → Travelpayouts 城市码
    "TFU": "CTU",
}


def to_city_code(code: str) -> str:
    c = resolve_airport(code)
    return AIRPORT_TO_CITY.get(c, c)


def months_covering(start: datetime, end: datetime) -> list[str]:
    if end < start:
        return []
    months: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return months


def iter_date_combos(
    start_date: datetime,
    end_date: datetime,
    stay_min: int,
    stay_max: int,
) -> list[tuple[str, str]]:
    """出发窗 × 停留天数 → (outbound, return) 日期对。"""
    if end_date < start_date or stay_max < stay_min:
        return []
    combos: list[tuple[str, str]] = []
    day = start_date
    while day <= end_date:
        for stay in range(stay_min, stay_max + 1):
            ret = day + timedelta(days=stay)
            combos.append((day.strftime("%Y-%m-%d"), ret.strftime("%Y-%m-%d")))
        day += timedelta(days=1)
    return combos


def count_api_batches(
    start: datetime,
    end: datetime,
    stay_min: int = 1,
    stay_max: int = 1,
) -> int:
    """发现阶段请求批次数 = 日期组合数。"""
    return max(1, len(iter_date_combos(start, end, stay_min, stay_max)))


def make_skeleton_option(
    origin: str,
    dest: str,
    outbound_date: str,
    return_date: str,
    adults: int = 1,
    cache_price: float = 0.0,
    currency: str = "CNY",
) -> FlightOption:
    """无 TP 缓存时的日期组合占位，供后续 OTA 核验补齐真价与航班详情。"""
    from app.services.deeplinks import build_verify_links

    o = resolve_airport(origin)
    d = resolve_airport(dest)
    links = build_verify_links(o, d, outbound_date, return_date, adults=adults)
    return FlightOption(
        outbound_date=outbound_date,
        return_date=return_date,
        total_price=float(cache_price or 0),
        currency=currency.upper(),
        source="DateCombo",
        verify_url=links["google"],
        verify_url_ctrip=links["ctrip"],
        verify_url_qunar=links["qunar"],
        summary_outbound="待核验",
        summary_return="待核验",
        cache_price=float(cache_price or 0),
        verified_price=None,
        verify_status="pending",
        origin_code=o,
        dest_code=d,
    )


def select_verify_pool(
    priced: list[FlightOption],
    all_combos: list[tuple[str, str]],
    origin: str,
    dest: str,
    adults: int,
    target: int,
    currency: str = "CNY",
    max_attempts: int | None = None,
) -> list[FlightOption]:
    """
    组装核验池：优先 TP 有价组合，不足则按日期均匀补齐，直到凑满 max_attempts。
    目标是让后续核验有机会产出 target 条成功结果（默认 Top-N）。
    """
    target = max(1, int(target))
    attempts = max_attempts if max_attempts is not None else min(len(all_combos), max(target * 2, target + 5))
    attempts = max(target, min(len(all_combos), attempts))

    priced_sorted = sorted(
        [o for o in priced if (o.cache_price or 0) > 0],
        key=lambda o: o.cache_price,
    )
    # 多目的地时按日期+目的地去重；补齐骨架时轮询 dest 列表
    dests = [d.strip().upper() for d in str(dest).split(",") if d.strip()] or [resolve_airport(dest)]
    origins = [o.strip().upper() for o in str(origin).split(",") if o.strip()] or [resolve_airport(origin)]
    primary_o = origins[0]

    have = {
        (o.outbound_date, o.return_date, (o.dest_code or "").upper())
        for o in priced_sorted
    }
    missing: list[tuple[str, str, str]] = []
    for d0, r0 in all_combos:
        for d_code in dests:
            if (d0, r0, d_code) not in have:
                missing.append((d0, r0, d_code))

    pool: list[FlightOption] = list(priced_sorted[:attempts])
    need = attempts - len(pool)
    if need > 0 and missing:
        if need >= len(missing):
            picks = missing
        else:
            step = len(missing) / need
            picks = [missing[min(len(missing) - 1, int(i * step))] for i in range(need)]
            seen: set[tuple[str, str, str]] = set()
            uniq: list[tuple[str, str, str]] = []
            for p in picks:
                if p not in seen:
                    seen.add(p)
                    uniq.append(p)
            picks = uniq
        for dep, ret, d_code in picks:
            pool.append(
                make_skeleton_option(
                    primary_o,
                    d_code,
                    dep,
                    ret,
                    adults=adults,
                    cache_price=0,
                    currency=currency,
                )
            )
    return pool[:attempts]


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


def ensure_verify_url(opt: FlightOption, origin: str, dest: str, adults: int = 1) -> str:
    if opt.verify_url:
        return opt.verify_url
    return google_flights_url(origin, dest, opt.outbound_date, opt.return_date, adults=adults)


def _transfer_text(n: int) -> str:
    if n <= 0:
        return "直飞"
    return f"转{n}次"


def _parse_dt(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    day = value[:10]
    hm = value[11:16] if "T" in value and len(value) >= 16 else ""
    return day, hm


def _hm_plus_minutes(day: str, hm: str, minutes: int) -> str:
    if not day or not hm or minutes <= 0:
        return ""
    try:
        base = datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M")
        return (base + timedelta(minutes=int(minutes))).strftime("%H:%M")
    except ValueError:
        return ""


def _flight_code(airline: str, flight_no: str) -> str:
    a = (airline or "").upper().strip()
    n = str(flight_no or "").strip()
    if a and n:
        if n.upper().startswith(a):
            return n.upper().replace(" ", "")
        return f"{a}{n}".replace(" ", "")
    return a or n or ""


class TravelpayoutsClient:
    def __init__(self, token: str, request_delay_sec: float = 0.2, market: str = "cn"):
        self.token = token.strip()
        self.request_delay_sec = max(0.05, float(request_delay_sec))
        self.market = (market or "cn").strip().lower() or "cn"

    def _get(self, path: str, params: dict[str, Any], retries: int = 3) -> dict[str, Any]:
        q = dict(params)
        q["token"] = self.token
        url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Gatefare/1.0"})
        opener = _http_opener()
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                with opener.open(req, timeout=45) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"Travelpayouts HTTP {e.code}: {body}") from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                _log.warning("Travelpayouts 网络错误 attempt=%s/%s: %s", attempt, retries, e)
                if attempt < retries:
                    time.sleep(0.4 * attempt)
        raise RuntimeError(f"Travelpayouts 网络错误: {_network_error_hint(last_err)}") from last_err

    def fetch_prices_for_dates(
        self,
        origin: str,
        destination: str,
        departure_at: str,
        return_at: str,
        currency: str = "CNY",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """按具体往返日期查缓存价（比 grouped_prices 更密）。"""
        params: dict[str, Any] = {
            "origin": to_city_code(origin),
            "destination": to_city_code(destination),
            "departure_at": departure_at,
            "return_at": return_at,
            "unique": "false",
            "sorting": "price",
            "direct": "false",
            "one_way": "false",
            "currency": currency.lower(),
            "limit": max(1, min(30, int(limit))),
            "page": 1,
        }
        data = self._get("/aviasales/v3/prices_for_dates", params)
        if data.get("success") is False:
            raise RuntimeError(f"Travelpayouts 返回失败: {data.get('error') or data}")
        raw = data.get("data") or []
        return raw if isinstance(raw, list) else []

    def fetch_grouped_month(
        self,
        origin: str,
        destination: str,
        month: str,
        stay_min: int,
        stay_max: int,
        currency: str = "CNY",
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "origin": to_city_code(origin),
            "destination": to_city_code(destination),
            "departure_at": month,
            "group_by": "departure_at",
            "currency": currency.lower(),
            "direct": "false",
            "min_trip_duration": stay_min,
            "max_trip_duration": stay_max,
            "market": market or self.market,
        }
        data = self._get("/aviasales/v3/grouped_prices", params)
        if data.get("success") is False:
            raise RuntimeError(f"Travelpayouts 返回失败: {data.get('error') or data}")
        raw = data.get("data") or {}
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            return list(raw.values())
        return []

    def scan_window(
        self,
        origin: str,
        destination: str,
        start_date: datetime,
        end_date: datetime,
        stay_min: int,
        stay_max: int,
        currency: str = "CNY",
        adults: int = 1,
        on_progress: Optional[ProgressCallback] = None,
        market: str | None = None,  # noqa: ARG002 — 保留签名兼容
    ) -> list[FlightOption]:
        """
        按「出发日 × 停留」逐组调用 prices_for_dates。
        空结果可跳过；网络/HTTP 失败重试后仍失败则整窗失败（fail-closed）。
        """
        combos = iter_date_combos(start_date, end_date, stay_min, stay_max)
        if not combos:
            raise RuntimeError("出发日期窗无效，无法调用 Travelpayouts")
        total = len(combos)
        if on_progress:
            on_progress(0, total)

        results: list[FlightOption] = []
        for i, (dep, ret) in enumerate(combos, 1):
            rows = self.fetch_prices_for_dates(
                origin,
                destination,
                dep,
                ret,
                currency=currency,
                limit=5,
            )
            # 同一日期组合取最便宜一条
            best: FlightOption | None = None
            for row in rows:
                opt = self._row_to_option(row, origin, destination, currency, adults)
                if not opt:
                    continue
                if opt.outbound_date != dep or opt.return_date != ret:
                    continue
                if best is None or opt.cache_price < best.cache_price:
                    best = opt
            if best:
                results.append(best)

            if on_progress:
                on_progress(i, total)
            if i < total:
                time.sleep(self.request_delay_sec)

        return results

    def _row_to_option(
        self,
        row: dict[str, Any],
        origin: str,
        destination: str,
        currency: str,
        adults: int,
    ) -> FlightOption | None:
        out_day, out_hm = _parse_dt(str(row.get("departure_at") or ""))
        ret_day, ret_hm = _parse_dt(str(row.get("return_at") or ""))
        if not out_day or not ret_day:
            return None
        try:
            price = float(row.get("price") or 0)
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None

        airline = str(row.get("airline") or "").upper()
        flight_no = str(row.get("flight_number") or "")
        code = _flight_code(airline, flight_no)
        transfers = int(row.get("transfers") or 0)
        ret_transfers = int(row.get("return_transfers") or 0)
        o_air = str(row.get("origin_airport") or resolve_airport(origin)).upper()
        d_air = str(row.get("destination_airport") or resolve_airport(destination)).upper()

        try:
            dur_to = int(row.get("duration_to") or 0)
        except (TypeError, ValueError):
            dur_to = 0
        try:
            dur_back = int(row.get("duration_back") or 0)
        except (TypeError, ValueError):
            dur_back = 0

        out_arr = _hm_plus_minutes(out_day, out_hm, dur_to) if out_hm else ""
        ret_arr = _hm_plus_minutes(ret_day, ret_hm, dur_back) if ret_hm else ""

        if code and out_hm and out_arr:
            out_summary = f"{code} {out_hm}→{out_arr} ({_transfer_text(transfers)})"
        elif code and out_hm:
            out_summary = f"{code} {out_hm} ({_transfer_text(transfers)})"
        else:
            out_summary = f"{airline or '航司'} {out_hm or '见核对'} ({_transfer_text(transfers)})"

        if ret_hm and ret_arr:
            ret_summary = f"回程 {ret_hm}→{ret_arr} ({_transfer_text(ret_transfers)})"
        elif ret_hm:
            ret_summary = f"回程 {ret_hm} ({_transfer_text(ret_transfers)})"
        else:
            ret_summary = f"回程 {_transfer_text(ret_transfers)}"

        from app.services.deeplinks import build_verify_links

        links = build_verify_links(o_air, d_air, out_day, ret_day, adults=adults)

        out_seg = FlightSegment(
            airline=airline or "??",
            flight_no=code or "N/A",
            departure_airport=o_air,
            arrival_airport=d_air,
            departure_time=f"{out_day}T{out_hm or '00:00'}:00",
            arrival_time=f"{out_day}T{out_arr or out_hm or '00:00'}:00",
            duration=str(dur_to or ""),
            stops=transfers,
        )
        ret_seg = FlightSegment(
            airline=airline or "??",
            flight_no="N/A",
            departure_airport=d_air,
            arrival_airport=o_air,
            departure_time=f"{ret_day}T{ret_hm or '00:00'}:00",
            arrival_time=f"{ret_day}T{ret_arr or ret_hm or '00:00'}:00",
            duration=str(dur_back or ""),
            stops=ret_transfers,
        )

        return FlightOption(
            outbound_date=out_day,
            return_date=ret_day,
            total_price=price,
            currency=(str(row.get("currency") or currency)).upper(),
            outbound_segments=[out_seg],
            return_segments=[ret_seg],
            booking_class="ECONOMY",
            source="Travelpayouts",
            verify_url=links["google"],
            verify_url_ctrip=links["ctrip"],
            verify_url_qunar=links["qunar"],
            summary_outbound=out_summary,
            summary_return=ret_summary,
            cache_price=price,
            verified_price=None,
            verify_status="pending",
            origin_code=o_air,
            dest_code=d_air,
        )
