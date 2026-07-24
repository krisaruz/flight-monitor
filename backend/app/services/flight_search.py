from __future__ import annotations

"""Amadeus 往返扫价核心：出发日期窗 + 停留 N–M 天。"""

import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

_log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]


def load_env() -> None:
    """从项目根目录及当前工作目录加载 `.env`。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # backend/app/services/… → 项目根（再上一级出 backend/）
    root = Path(__file__).resolve().parents[3]
    load_dotenv(root / ".env")
    load_dotenv()


def get_request_delay_sec() -> float:
    try:
        return max(0.05, float(os.environ.get("AMADEUS_REQUEST_DELAY", "0.15")))
    except ValueError:
        return 0.15


@dataclass
class FlightSegment:
    airline: str
    flight_no: str
    departure_airport: str
    arrival_airport: str
    departure_time: str
    arrival_time: str
    duration: str
    stops: int = 0


@dataclass
class FlightOption:
    outbound_date: str
    return_date: str
    total_price: float
    currency: str
    outbound_segments: list[FlightSegment] = field(default_factory=list)
    return_segments: list[FlightSegment] = field(default_factory=list)
    booking_class: str = ""
    source: str = ""
    verify_url: str = ""
    verify_url_ctrip: str = ""
    verify_url_qunar: str = ""
    summary_outbound: str = ""
    summary_return: str = ""
    cache_price: float = 0.0
    verified_price: float | None = None
    verify_status: str = ""
    origin_code: str = ""
    dest_code: str = ""

    @property
    def trip_days(self) -> int:
        d1 = datetime.strptime(self.outbound_date, "%Y-%m-%d")
        d2 = datetime.strptime(self.return_date, "%Y-%m-%d")
        return (d2 - d1).days

    @property
    def outbound_summary(self) -> str:
        if self.summary_outbound:
            return self.summary_outbound
        if not self.outbound_segments:
            return "N/A"
        segs = self.outbound_segments
        stops = sum(s.stops for s in segs) + len(segs) - 1
        airlines = "/".join(dict.fromkeys(s.airline for s in segs))
        dep = segs[0].departure_time[11:16] if len(segs[0].departure_time) > 11 else segs[0].departure_time
        arr = segs[-1].arrival_time[11:16] if len(segs[-1].arrival_time) > 11 else segs[-1].arrival_time
        stop_text = "直飞" if stops == 0 else f"转{stops}次"
        return f"{airlines} {dep}-{arr} ({stop_text})"

    @property
    def return_summary(self) -> str:
        if self.summary_return:
            return self.summary_return
        if not self.return_segments:
            return "N/A"
        segs = self.return_segments
        stops = sum(s.stops for s in segs) + len(segs) - 1
        airlines = "/".join(dict.fromkeys(s.airline for s in segs))
        dep = segs[0].departure_time[11:16] if len(segs[0].departure_time) > 11 else segs[0].departure_time
        arr = segs[-1].arrival_time[11:16] if len(segs[-1].arrival_time) > 11 else segs[-1].arrival_time
        stop_text = "直飞" if stops == 0 else f"转{stops}次"
        return f"{airlines} {dep}-{arr} ({stop_text})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "outbound_date": self.outbound_date,
            "return_date": self.return_date,
            "trip_days": self.trip_days,
            "total_price": self.total_price,
            "currency": self.currency,
            "outbound": self.outbound_summary,
            "return": self.return_summary,
            "booking_class": self.booking_class,
            "source": self.source,
            "verify_url": self.verify_url,
            "verify_url_ctrip": self.verify_url_ctrip,
            "verify_url_qunar": self.verify_url_qunar,
        }


class AmadeusFlightSearch:
    """使用 Amadeus Self-Service API；对 429 / 5xx / 网络错误做退避重试。"""

    def __init__(self, client_id: str, client_secret: str, production: bool = False):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = "https://api.amadeus.com" if production else "https://test.api.amadeus.com"
        self.token: Optional[str] = None
        self.token_expires = 0.0

    def _invalidate_token(self) -> None:
        self.token = None
        self.token_expires = 0.0

    @staticmethod
    def _parse_error_body(body: bytes) -> str:
        try:
            err = json.loads(body.decode("utf-8", errors="replace"))
            if isinstance(err, dict):
                errs = err.get("errors")
                if isinstance(errs, list) and errs:
                    first = errs[0]
                    detail = first.get("detail") or first.get("title") or str(first)
                    return str(detail)
                return str(
                    err.get("error_description")
                    or err.get("error")
                    or json.dumps(err, ensure_ascii=False)[:200],
                )
        except Exception:
            pass
        return body.decode("utf-8", errors="replace")[:300]

    def _sleep_backoff(self, attempt: int, retry_after: Optional[float]) -> None:
        if retry_after is not None and retry_after > 0:
            time.sleep(min(retry_after + random.uniform(0, 0.5), 120))
        else:
            time.sleep(min(2**attempt + random.uniform(0, 0.3), 60))

    def _fetch_oauth_token(self) -> str:
        if self.token and time.time() < self.token_expires - 60:
            return self.token

        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()
        url = f"{self.base_url}/v1/security/oauth2/token"
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    self.token = result["access_token"]
                    self.token_expires = time.time() + float(result.get("expires_in", 1799))
                    return self.token
            except urllib.error.HTTPError as e:
                body = e.read()
                msg = AmadeusFlightSearch._parse_error_body(body)
                ra_raw = e.headers.get("Retry-After")
                ra: Optional[float] = None
                if ra_raw:
                    try:
                        ra = float(ra_raw)
                    except ValueError:
                        pass
                if e.code in (429, 503, 502, 504) or e.code >= 500:
                    _log.warning("Amadeus OAuth HTTP %s (attempt %s): %s", e.code, attempt + 1, msg)
                    if attempt < 4:
                        self._sleep_backoff(attempt, ra)
                        continue
                _log.error("Amadeus OAuth 失败 HTTP %s: %s", e.code, msg)
                raise RuntimeError(f"Amadeus OAuth ({e.code}): {msg}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                _log.warning("Amadeus OAuth 网络错误 (attempt %s): %s", attempt + 1, e)
                if attempt < 4:
                    self._sleep_backoff(attempt, None)
                    continue
                raise RuntimeError(f"Amadeus OAuth 网络失败: {e}") from e
        raise RuntimeError("Amadeus OAuth 重试耗尽")

    def _api_get(self, path: str, params: dict[str, Any]) -> dict:
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}?{query}"

        for auth_round in range(3):
            token = self._fetch_oauth_token()
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
            for attempt in range(6):
                try:
                    with urllib.request.urlopen(req, timeout=45) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except urllib.error.HTTPError as e:
                    body = e.read()
                    msg = self._parse_error_body(body)
                    ra_raw = e.headers.get("Retry-After")
                    ra: Optional[float] = None
                    if ra_raw:
                        try:
                            ra = float(ra_raw)
                        except ValueError:
                            pass
                    if e.code == 401:
                        _log.warning("Amadeus token 失效，刷新后重试")
                        self._invalidate_token()
                        break
                    if e.code == 429 or e.code >= 500:
                        _log.warning(
                            "Amadeus GET %s HTTP %s (attempt %s): %s",
                            path,
                            e.code,
                            attempt + 1,
                            msg,
                        )
                        if attempt < 5:
                            self._sleep_backoff(attempt, ra)
                            continue
                        raise RuntimeError(f"{e.code}: {msg}") from None
                    _log.warning("Amadeus GET %s HTTP %s: %s", path, e.code, msg)
                    raise RuntimeError(f"{e.code}: {msg}") from None
                except (urllib.error.URLError, TimeoutError, OSError) as e:
                    _log.warning("Amadeus GET 网络错误 (attempt %s): %s", attempt + 1, e)
                    if attempt < 5:
                        self._sleep_backoff(attempt, None)
                        continue
                    raise RuntimeError(str(e)) from e
        raise RuntimeError("Amadeus GET 认证重试耗尽")

    def search_round_trip(
        self,
        origin: str,
        destination: str,
        depart_date: str,
        return_date: str,
        adults: int = 1,
        currency: str = "CNY",
        max_results: int = 5,
        cabin: str = "",
    ) -> list[FlightOption]:
        params = {
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": depart_date,
            "returnDate": return_date,
            "adults": adults,
            "currencyCode": currency,
            "max": max_results,
            "nonStop": "false",
        }
        if cabin:
            params["travelClass"] = cabin

        try:
            data = self._api_get("/v2/shopping/flight-offers", params)
        except Exception as e:
            _log.warning("航班报价请求失败 %s → %s: %s", depart_date, return_date, e)
            return []

        results = []
        for offer in data.get("data", []):
            price_info = offer.get("price", {})
            total = float(price_info.get("grandTotal", price_info.get("total", 0)))
            cur = price_info.get("currency", currency)

            outbound_segs = []
            return_segs = []

            for i, itin in enumerate(offer.get("itineraries", [])):
                segments = []
                for seg in itin.get("segments", []):
                    segments.append(FlightSegment(
                        airline=seg.get("carrierCode", ""),
                        flight_no=f"{seg.get('carrierCode', '')}{seg.get('number', '')}",
                        departure_airport=seg.get("departure", {}).get("iataCode", ""),
                        arrival_airport=seg.get("arrival", {}).get("iataCode", ""),
                        departure_time=seg.get("departure", {}).get("at", ""),
                        arrival_time=seg.get("arrival", {}).get("at", ""),
                        duration=seg.get("duration", ""),
                        stops=seg.get("numberOfStops", 0),
                    ))
                if i == 0:
                    outbound_segs = segments
                else:
                    return_segs = segments

            results.append(FlightOption(
                outbound_date=depart_date,
                return_date=return_date,
                total_price=total,
                currency=cur,
                outbound_segments=outbound_segs,
                return_segments=return_segs,
                booking_class=offer.get("travelerPricings", [{}])[0]
                    .get("fareDetailsBySegment", [{}])[0]
                    .get("cabin", ""),
                source="Amadeus",
            ))

        return results


def list_departure_dates(start_date: datetime, end_date: datetime) -> list[datetime]:
    """出发日 ∈ [start, end]（含首尾），不约束回程是否落在窗内。"""
    if end_date < start_date:
        return []
    dates: list[datetime] = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def count_scan_combinations(
    start_date: datetime,
    end_date: datetime,
    stay_min: int,
    stay_max: int,
) -> int:
    """预估 API 请求组合数：出发日数 × 停留档位数。"""
    if stay_min < 1 or stay_max < stay_min:
        return 0
    n_depart = len(list_departure_dates(start_date, end_date))
    return n_depart * (stay_max - stay_min + 1)


def count_departure_dates(
    start_date: datetime,
    end_date: datetime,
    trip_days: int | None = None,
    *,
    stay_min: int | None = None,
    stay_max: int | None = None,
) -> int:
    """兼容旧签名：仅出发日数量；若给 stay 区间则返回组合数。"""
    if stay_min is not None and stay_max is not None:
        return count_scan_combinations(start_date, end_date, stay_min, stay_max)
    # 新语义：只数出发日
    return len(list_departure_dates(start_date, end_date))


def dedupe_flight_options(results: list[FlightOption]) -> list[FlightOption]:
    seen: set[tuple[Any, ...]] = set()
    out: list[FlightOption] = []
    for opt in sorted(results, key=lambda x: (x.total_price, x.outbound_date)):
        key = (
            opt.outbound_date,
            opt.return_date,
            round(opt.total_price, 2),
            opt.currency,
            opt.outbound_summary,
            opt.return_summary,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(opt)
    return out


def generate_demo_data(
    origin: str,
    dest: str,
    start_date: datetime,
    end_date: datetime,
    trip_days: int | None = None,
    *,
    stay_min: int | None = None,
    stay_max: int | None = None,
) -> list[FlightOption]:
    """生成演示数据。支持固定天数或停留区间。"""
    random.seed(42)
    if stay_min is None or stay_max is None:
        if trip_days is None:
            trip_days = 5
        stay_min = trip_days
        stay_max = trip_days

    airlines_outbound = [
        ("CX", "国泰航空"), ("HX", "香港航空"), ("UO", "香港快运"),
        ("NH", "全日空"), ("JL", "日本航空"), ("MM", "乐桃航空"),
    ]
    airlines_return = airlines_outbound.copy()

    results: list[FlightOption] = []
    for current in list_departure_dates(start_date, end_date):
        for stay in range(stay_min, stay_max + 1):
            ret = current + timedelta(days=stay)
            n_options = random.randint(2, 4)

            for _ in range(n_options):
                al_out = random.choice(airlines_outbound)
                al_ret = random.choice(airlines_return)

                is_direct_out = random.random() < 0.4
                is_direct_ret = random.random() < 0.4

                base_price = random.uniform(1200, 4500)
                weekday = current.weekday()
                if weekday >= 4:
                    base_price *= random.uniform(1.1, 1.4)
                month_factor = {4: 0.9, 5: 1.0, 6: 1.15, 11: 1.05, 12: 1.2}.get(current.month, 1.0)
                base_price *= month_factor
                base_price *= 1.0 + (stay - stay_min) * 0.02
                if is_direct_out and is_direct_ret:
                    base_price *= random.uniform(1.05, 1.3)

                dep_hour_out = random.randint(6, 22)
                dep_min_out = random.choice([0, 15, 30, 45])
                flight_hours = random.uniform(3.5, 4.5) if is_direct_out else random.uniform(6, 12)
                arr_time_out = datetime(2026, 1, 1, dep_hour_out, dep_min_out) + timedelta(hours=flight_hours)

                dep_hour_ret = random.randint(8, 21)
                dep_min_ret = random.choice([0, 15, 30, 45])
                flight_hours_ret = random.uniform(4, 5) if is_direct_ret else random.uniform(7, 13)
                arr_time_ret = datetime(2026, 1, 1, dep_hour_ret, dep_min_ret) + timedelta(hours=flight_hours_ret)

                out_dep = f"{current.strftime('%Y-%m-%d')}T{dep_hour_out:02d}:{dep_min_out:02d}:00"
                out_arr = f"{current.strftime('%Y-%m-%d')}T{arr_time_out.hour:02d}:{arr_time_out.minute:02d}:00"
                ret_dep = f"{ret.strftime('%Y-%m-%d')}T{dep_hour_ret:02d}:{dep_min_ret:02d}:00"
                ret_arr = f"{ret.strftime('%Y-%m-%d')}T{arr_time_ret.hour:02d}:{arr_time_ret.minute:02d}:00"

                stops_out = 0 if is_direct_out else random.randint(1, 2)
                stops_ret = 0 if is_direct_ret else random.randint(1, 2)

                results.append(FlightOption(
                    outbound_date=current.strftime("%Y-%m-%d"),
                    return_date=ret.strftime("%Y-%m-%d"),
                    total_price=round(base_price, 0),
                    currency="CNY",
                    outbound_segments=[FlightSegment(
                        airline=al_out[0],
                        flight_no=f"{al_out[0]}{random.randint(100, 999)}",
                        departure_airport=origin,
                        arrival_airport=dest,
                        departure_time=out_dep,
                        arrival_time=out_arr,
                        duration=f"PT{int(flight_hours)}H{int((flight_hours % 1) * 60)}M",
                        stops=stops_out,
                    )],
                    return_segments=[FlightSegment(
                        airline=al_ret[0],
                        flight_no=f"{al_ret[0]}{random.randint(100, 999)}",
                        departure_airport=dest,
                        arrival_airport=origin,
                        departure_time=ret_dep,
                        arrival_time=ret_arr,
                        duration=f"PT{int(flight_hours_ret)}H{int((flight_hours_ret % 1) * 60)}M",
                        stops=stops_ret,
                    )],
                    booking_class="ECONOMY",
                    source="Demo",
                ))

    return results


def scan_date_window(
    searcher: AmadeusFlightSearch | None,
    origin: str,
    destination: str,
    start_date: datetime,
    end_date: datetime,
    stay_min: int,
    stay_max: int,
    adults: int = 1,
    currency: str = "CNY",
    cabin: str = "",
    results_per_date: int = 5,
    request_delay_sec: Optional[float] = None,
    on_progress: Optional[ProgressCallback] = None,
    use_demo: bool = False,
) -> list[FlightOption]:
    """扫描出发窗 × 停留区间的所有往返组合。

    出发日 ∈ [start, end]；回程 = 出发 + stay，可落在窗外。
    """
    if stay_min < 1 or stay_max < stay_min:
        raise ValueError("stay_min/stay_max 无效")

    if use_demo or searcher is None:
        results = generate_demo_data(
            origin, destination, start_date, end_date,
            stay_min=stay_min, stay_max=stay_max,
        )
        total = count_scan_combinations(start_date, end_date, stay_min, stay_max)
        if on_progress:
            on_progress(total, total)
        return results

    delay_sec = (
        max(0.05, float(request_delay_sec))
        if request_delay_sec is not None
        else get_request_delay_sec()
    )

    departures = list_departure_dates(start_date, end_date)
    combos: list[tuple[datetime, int]] = [
        (dt, stay) for dt in departures for stay in range(stay_min, stay_max + 1)
    ]
    total = len(combos)
    if on_progress:
        on_progress(0, total)
    if total == 0:
        return []

    all_results: list[FlightOption] = []
    for i, (dt, stay) in enumerate(combos, 1):
        depart = dt.strftime("%Y-%m-%d")
        ret = (dt + timedelta(days=stay)).strftime("%Y-%m-%d")
        all_results.extend(
            searcher.search_round_trip(
                origin,
                destination,
                depart,
                ret,
                adults=adults,
                currency=currency,
                max_results=results_per_date,
                cabin=cabin,
            )
        )
        if on_progress:
            on_progress(i, total)
        time.sleep(delay_sec)

    return all_results


# 兼容旧名
def scan_dates(
    searcher: AmadeusFlightSearch,
    origin: str,
    destination: str,
    start_date: datetime,
    end_date: datetime,
    trip_days: int,
    adults: int = 1,
    currency: str = "CNY",
    cabin: str = "",
    results_per_date: int = 5,
    verbose: bool = True,
    request_delay_sec: Optional[float] = None,
) -> list[FlightOption]:
    def _prog(done: int, total: int) -> None:
        if verbose and total:
            pct = done / total * 100
            print(f"\r  [{pct:5.1f}%] 扫描进度 {done}/{total}", end="", flush=True)
            if done >= total:
                print()

    if verbose:
        print(f"\n  扫描范围: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
        print(f"  停留: {trip_days} 天（出发窗内逐日，回程可出窗）")
        print(f"  组合数: {count_scan_combinations(start_date, end_date, trip_days, trip_days)}\n")

    return scan_date_window(
        searcher,
        origin,
        destination,
        start_date,
        end_date,
        stay_min=trip_days,
        stay_max=trip_days,
        adults=adults,
        currency=currency,
        cabin=cabin,
        results_per_date=results_per_date,
        request_delay_sec=request_delay_sec,
        on_progress=_prog if verbose else None,
    )


# 海外常用别名；中国大陆城市由 cn_cities 目录动态合并
AIRPORT_ALIASES = {
    "香港": "HKG", "hongkong": "HKG", "hkg": "HKG",
    "澳门": "MFM", "macau": "MFM", "macao": "MFM", "mfm": "MFM",
    "大阪": "KIX", "osaka": "KIX", "kix": "KIX",
    "东京": "NRT", "tokyo": "NRT", "nrt": "NRT", "hnd": "HND",
    "首尔": "ICN", "seoul": "ICN", "icn": "ICN",
    "曼谷": "BKK", "bangkok": "BKK", "bkk": "BKK",
    "新加坡": "SIN", "singapore": "SIN", "sin": "SIN",
    "台北": "TPE", "taipei": "TPE", "tpe": "TPE",
    "名古屋": "NGO", "nagoya": "NGO",
    "福冈": "FUK", "fukuoka": "FUK",
    "札幌": "CTS", "sapporo": "CTS",
    "冲绳": "OKA", "okinawa": "OKA", "那霸": "OKA",
}


def _merged_airport_aliases() -> dict[str, str]:
    from app.services.cn_cities import cn_city_alias_map

    merged = dict(AIRPORT_ALIASES)
    merged.update(cn_city_alias_map())
    return merged


def resolve_airport(name: str) -> str:
    key = name.strip().lower()
    aliases = _merged_airport_aliases()
    if key in aliases:
        return aliases[key]
    if len(name.strip()) == 3 and name.strip().isalpha():
        return name.strip().upper()
    return name.strip().upper()
