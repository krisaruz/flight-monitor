from __future__ import annotations

"""Travelpayouts / Aviasales Data API — 免费缓存市场价格（发现阶段）。"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from urllib.parse import quote

from app.services.flight_search import FlightOption, FlightSegment, resolve_airport

_log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]
StatusCallback = Callable[[str], None]

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

# 机场三字码 → Travelpayouts 常用城市码（仅 TP API 边界使用）
AIRPORT_TO_CITY = {
    "KIX": "OSA",
    "ITM": "OSA",
    "NRT": "TYO",
    "HND": "TYO",
    "CTS": "SPK",
    "CGK": "JKT",
    "JFK": "NYC",
    "EWR": "NYC",
    "LGA": "NYC",
    "ORD": "CHI",
    "MDW": "CHI",
    "IAD": "WAS",
    "DCA": "WAS",
    "YYZ": "YTO",
    "YUL": "YMQ",
    "LHR": "LON",
    "LGW": "LON",
    "CDG": "PAR",
    "ORY": "PAR",
    "FCO": "ROM",
    "MXP": "MIL",
    "GRU": "SAO",
    "GIG": "RIO",
    "PVG": "SHA",
    "SHA": "SHA",
    "PEK": "BJS",
    "PKX": "BJS",
    "ICN": "SEL",
    "GMP": "SEL",
    "XIY": "SIA",
    "TFU": "CTU",
}


def to_city_code(code: str) -> str:
    from app.services.flight_search import canonical_airport_code

    c = canonical_airport_code(code)
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


def expand_neighbor_combos(
    seeds: list[tuple[str, str]],
    all_combos: list[tuple[str, str]],
    max_extra: int = 12,
) -> list[tuple[str, str]]:
    """
    对已核验低价日期做邻域加密：去程 ±2 天、停留 ±1 天（仍须落在 all_combos 内）。
    """
    if max_extra <= 0 or not seeds or not all_combos:
        return []
    combo_set = set(all_combos)
    seen = set(seeds)
    out: list[tuple[str, str]] = []
    for dep, ret in seeds:
        try:
            d0 = datetime.strptime(dep, "%Y-%m-%d")
            r0 = datetime.strptime(ret, "%Y-%m-%d")
        except ValueError:
            continue
        stay = max(1, (r0 - d0).days)
        for dd in (-2, -1, 1, 2):
            for ds in (-1, 0, 1):
                nd = d0 + timedelta(days=dd)
                nr = nd + timedelta(days=max(1, stay + ds))
                key = (nd.strftime("%Y-%m-%d"), nr.strftime("%Y-%m-%d"))
                if key in combo_set and key not in seen:
                    seen.add(key)
                    out.append(key)
                    if len(out) >= max_extra:
                        return out
    return out


def build_options_for_combos(
    combos: list[tuple[str, str]],
    origin: str,
    dest: str,
    adults: int,
    currency: str = "CNY",
) -> list[FlightOption]:
    """把 (out, ret) 列表转成可核验骨架选项（单 OD）。"""
    o = resolve_airport(origin)
    d = resolve_airport(dest)
    return [
        make_skeleton_option(o, d, dep, ret, adults=adults, cache_price=0, currency=currency)
        for dep, ret in combos
    ]


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
    from app.services.flight_search import resolve_google_airport

    o = resolve_google_airport(origin)
    d = resolve_google_airport(dest)
    q = f"Flights to {d} from {o} on {outbound_date} one way"
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
    def __init__(
        self,
        token: str,
        request_delay_sec: float = 0.2,
        market: str = "cn",
        concurrency: int = 6,
    ):
        self.token = token.strip()
        self.request_delay_sec = max(0.05, float(request_delay_sec))
        self.market = (market or "cn").strip().lower() or "cn"
        self.concurrency = max(1, min(16, int(concurrency)))
        self._throttle_lock = threading.Lock()
        self._next_slot = 0.0
        self._status_cb: Optional[StatusCallback] = None

    def _emit_status(self, message: str) -> None:
        cb = self._status_cb
        if cb:
            try:
                cb(message)
            except Exception:
                _log.debug("status callback failed", exc_info=True)

    def _acquire_request_slot(self) -> None:
        """全局限速：并行 worker 共享发请求槽位，避免把 delay 乘没。"""
        while True:
            with self._throttle_lock:
                now = time.monotonic()
                if now >= self._next_slot:
                    self._next_slot = now + self.request_delay_sec
                    return
                wait = self._next_slot - now
            time.sleep(min(wait, 1.0))

    def _note_rate_limit(self, seconds: float) -> None:
        pause = max(1.0, float(seconds))
        with self._throttle_lock:
            self._next_slot = max(self._next_slot, time.monotonic() + pause)
        msg = f"缓存接口限流，暂停约 {int(round(pause))} 秒后重试…"
        _log.warning("Travelpayouts rate limit: pause=%.1fs", pause)
        self._emit_status(msg)

    @staticmethod
    def _retry_after_seconds(err: urllib.error.HTTPError, attempt: int) -> float:
        raw = (err.headers.get("Retry-After") if err.headers else None) or ""
        try:
            if raw.strip().isdigit():
                return max(1.0, float(raw.strip()))
        except (TypeError, ValueError):
            pass
        # 指数退避，上限 30s
        return min(30.0, float(2 ** attempt))

    @staticmethod
    def _is_rate_limit_error(code: int, body: str) -> bool:
        if code == 429:
            return True
        if code in {403, 503}:
            low = (body or "").lower()
            return any(
                k in low
                for k in ("rate limit", "too many requests", "throttle", "限流", "频率")
            )
        return False

    def _get(self, path: str, params: dict[str, Any], retries: int = 4) -> dict[str, Any]:
        q = dict(params)
        q["token"] = self.token
        url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Gatefare/1.0"})
        opener = _http_opener()
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            self._acquire_request_slot()
            try:
                with opener.open(req, timeout=45) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")[:500]
                if self._is_rate_limit_error(e.code, body):
                    wait = self._retry_after_seconds(e, attempt)
                    self._note_rate_limit(wait)
                    if attempt < retries:
                        time.sleep(wait)
                        continue
                    raise RuntimeError(
                        "Travelpayouts 请求过于频繁（限流）。"
                        "请稍后再扫，或调低 TRAVELPAYOUTS_CONCURRENCY / 增大 TRAVELPAYOUTS_REQUEST_DELAY。"
                        f" HTTP {e.code}: {body}"
                    ) from e
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
        return_at: str = "",
        currency: str = "CNY",
        limit: int = 5,
        one_way: bool = False,
    ) -> list[dict[str, Any]]:
        """按具体日期查缓存价；one_way=True 时只查单程（日历主源）。"""
        params: dict[str, Any] = {
            "origin": to_city_code(origin),
            "destination": to_city_code(destination),
            "departure_at": departure_at,
            "unique": "false",
            "sorting": "price",
            "direct": "false",
            "one_way": "true" if one_way else "false",
            "currency": currency.lower(),
            "limit": max(1, min(30, int(limit))),
            "page": 1,
        }
        if not one_way:
            params["return_at"] = return_at
        data = self._get("/aviasales/v3/prices_for_dates", params)
        if data.get("success") is False:
            raise RuntimeError(f"Travelpayouts 返回失败: {data.get('error') or data}")
        raw = data.get("data") or []
        return raw if isinstance(raw, list) else []

    def fetch_one_way_min_price(
        self,
        origin: str,
        destination: str,
        departure_at: str,
        currency: str = "CNY",
    ) -> float | None:
        """单日单程最低缓存价；无价返回 None。"""
        rows = self.fetch_prices_for_dates(
            origin,
            destination,
            departure_at,
            return_at="",
            currency=currency,
            limit=5,
            one_way=True,
        )
        best: float | None = None
        for row in rows:
            try:
                price = float(row.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            # 若 API 仍带回程字段，仍可用其 price（单程请求下通常为单程价）
            dep = str(row.get("departure_at") or "")[:10]
            if dep and dep != departure_at:
                continue
            if best is None or price < best:
                best = price
        return best

    def scan_leg_calendar(
        self,
        origin: str,
        destination: str,
        start_date: datetime,
        end_date: datetime,
        currency: str = "CNY",
        on_progress: Optional[ProgressCallback] = None,
    ) -> dict[str, float]:
        """
        按日拉取单程最低价日历。
        空日不写入；网络错误 fail-closed。
        """
        from app.services.calendar_match import iter_day_strings

        days = iter_day_strings(start_date, end_date)
        total = max(1, len(days))
        if on_progress:
            on_progress(0, total)
        cal: dict[str, float] = {}
        for i, day in enumerate(days, 1):
            price = self.fetch_one_way_min_price(origin, destination, day, currency=currency)
            if price is not None and price > 0:
                cal[day] = float(price)
            if on_progress:
                on_progress(i, total)
        return cal

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

    def _best_option_for_combo(
        self,
        origin: str,
        destination: str,
        dep: str,
        ret: str,
        currency: str,
        adults: int,
    ) -> FlightOption | None:
        rows = self.fetch_prices_for_dates(
            origin,
            destination,
            dep,
            ret,
            currency=currency,
            limit=5,
        )
        best: FlightOption | None = None
        for row in rows:
            opt = self._row_to_option(row, origin, destination, currency, adults)
            if not opt:
                continue
            if opt.outbound_date != dep or opt.return_date != ret:
                continue
            if best is None or opt.cache_price < best.cache_price:
                best = opt
        return best

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
        on_status: Optional[StatusCallback] = None,
        market: str | None = None,  # noqa: ARG002 — 保留签名兼容
    ) -> list[FlightOption]:
        """
        按「出发日 × 停留」调用 prices_for_dates；默认有限并行 + 全局限速槽。
        空结果可跳过；网络/HTTP/限流耗尽失败则整窗失败（fail-closed）。
        """
        combos = iter_date_combos(start_date, end_date, stay_min, stay_max)
        if not combos:
            raise RuntimeError("出发日期窗无效，无法调用 Travelpayouts")
        total = len(combos)
        if on_progress:
            on_progress(0, total)

        prev_status = self._status_cb
        self._status_cb = on_status
        results: list[FlightOption] = []
        done_lock = threading.Lock()
        done_count = 0
        workers = min(self.concurrency, total)

        def _one(dep: str, ret: str) -> FlightOption | None:
            return self._best_option_for_combo(origin, destination, dep, ret, currency, adults)

        def _mark_done() -> None:
            nonlocal done_count
            with done_lock:
                done_count += 1
                cur = done_count
            if on_progress:
                on_progress(cur, total)

        try:
            if workers <= 1:
                for dep, ret in combos:
                    best = _one(dep, ret)
                    if best:
                        results.append(best)
                    _mark_done()
                return results

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_one, dep, ret): (dep, ret) for dep, ret in combos}
                try:
                    for fut in as_completed(futures):
                        try:
                            best = fut.result()
                        except Exception:
                            for pending in futures:
                                pending.cancel()
                            raise
                        if best:
                            results.append(best)
                        _mark_done()
                except Exception:
                    # 尽快打断仍在跑的请求槽等待
                    raise
        finally:
            self._status_cb = prev_status

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
