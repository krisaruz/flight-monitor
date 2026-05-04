"""
机票价格监控工具 - Flight Price Monitor
========================================
监控指定航线在日期范围内的往返机票价格，按价格从低到高排序。

使用 Amadeus Flight Offers Search API（免费注册）。
注册地址: https://developers.amadeus.com/register

使用方式:
  1. 设置环境变量:
     set AMADEUS_CLIENT_ID=你的client_id
     set AMADEUS_CLIENT_SECRET=你的client_secret

  2. 运行:
     python flight_monitor.py
     python flight_monitor.py --origin HKG --dest KIX --start 2026-04-20 --end 2026-06-30 --days 5
     python flight_monitor.py --demo  (演示模式，无需API密钥)
"""

import argparse
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


def load_env() -> None:
    """从项目目录及当前工作目录加载 `.env`（需安装 python-dotenv）。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    load_dotenv()

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.system("")  # enable ANSI escape on Windows


# ─── 数据模型 ────────────────────────────────────────────────────────────────

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
    outbound_segments: list = field(default_factory=list)
    return_segments: list = field(default_factory=list)
    booking_class: str = ""
    source: str = ""

    @property
    def trip_days(self) -> int:
        d1 = datetime.strptime(self.outbound_date, "%Y-%m-%d")
        d2 = datetime.strptime(self.return_date, "%Y-%m-%d")
        return (d2 - d1).days

    @property
    def outbound_summary(self) -> str:
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
        if not self.return_segments:
            return "N/A"
        segs = self.return_segments
        stops = sum(s.stops for s in segs) + len(segs) - 1
        airlines = "/".join(dict.fromkeys(s.airline for s in segs))
        dep = segs[0].departure_time[11:16] if len(segs[0].departure_time) > 11 else segs[0].departure_time
        arr = segs[-1].arrival_time[11:16] if len(segs[-1].arrival_time) > 11 else segs[-1].arrival_time
        stop_text = "直飞" if stops == 0 else f"转{stops}次"
        return f"{airlines} {dep}-{arr} ({stop_text})"


# ─── Amadeus API 搜索 ───────────────────────────────────────────────────────

class AmadeusFlightSearch:
    """使用 Amadeus Self-Service API 搜索航班"""

    BASE_URL = "https://test.api.amadeus.com"  # test 环境, 换 production 用 api.amadeus.com

    def __init__(self, client_id: str, client_secret: str, production: bool = False):
        self.client_id = client_id
        self.client_secret = client_secret
        if production:
            self.BASE_URL = "https://api.amadeus.com"
        self.token = None
        self.token_expires = 0

    def _get_token(self):
        import urllib.request
        import urllib.parse

        if self.token and time.time() < self.token_expires - 60:
            return self.token

        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()

        req = urllib.request.Request(
            f"{self.BASE_URL}/v1/security/oauth2/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())

        self.token = result["access_token"]
        self.token_expires = time.time() + result.get("expires_in", 1799)
        return self.token

    def _api_get(self, path: str, params: dict) -> dict:
        import urllib.request
        import urllib.parse

        token = self._get_token()
        query = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}{path}?{query}"

        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

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
            params["travelClass"] = cabin  # ECONOMY, PREMIUM_ECONOMY, BUSINESS, FIRST

        try:
            data = self._api_get("/v2/shopping/flight-offers", params)
        except Exception as e:
            print(f"  [!] API 请求失败 ({depart_date} -> {return_date}): {e}")
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


# ─── 演示数据 ────────────────────────────────────────────────────────────────

def generate_demo_data(origin, dest, start_date, end_date, trip_days) -> list[FlightOption]:
    """生成演示数据，用于展示程序功能"""
    import random
    random.seed(42)

    airlines_outbound = [
        ("CX", "国泰航空"), ("HX", "香港航空"), ("UO", "香港快运"),
        ("NH", "全日空"), ("JL", "日本航空"), ("MM", "乐桃航空"),
    ]
    airlines_return = airlines_outbound.copy()

    results = []
    current = start_date
    while current + timedelta(days=trip_days) <= end_date:
        ret = current + timedelta(days=trip_days)
        n_options = random.randint(2, 5)

        for _ in range(n_options):
            al_out = random.choice(airlines_outbound)
            al_ret = random.choice(airlines_return)

            is_direct_out = random.random() < 0.4
            is_direct_ret = random.random() < 0.4

            base_price = random.uniform(1200, 4500)
            weekday = current.weekday()
            if weekday >= 4:
                base_price *= random.uniform(1.1, 1.4)
            month_factor = {4: 0.9, 5: 1.0, 6: 1.15}.get(current.month, 1.0)
            base_price *= month_factor
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
            out_arr_dt = current + timedelta(hours=flight_hours)
            out_arr = f"{current.strftime('%Y-%m-%d')}T{arr_time_out.hour:02d}:{arr_time_out.minute:02d}:00"

            ret_dep = f"{ret.strftime('%Y-%m-%d')}T{dep_hour_ret:02d}:{dep_min_ret:02d}:00"
            ret_arr = f"{ret.strftime('%Y-%m-%d')}T{arr_time_ret.hour:02d}:{arr_time_ret.minute:02d}:00"

            stops_out = 0 if is_direct_out else random.randint(1, 2)
            stops_ret = 0 if is_direct_ret else random.randint(1, 2)

            fn_out = f"{al_out[0]}{random.randint(100, 999)}"
            fn_ret = f"{al_ret[0]}{random.randint(100, 999)}"

            results.append(FlightOption(
                outbound_date=current.strftime("%Y-%m-%d"),
                return_date=ret.strftime("%Y-%m-%d"),
                total_price=round(base_price, 0),
                currency="CNY",
                outbound_segments=[FlightSegment(
                    airline=al_out[0],
                    flight_no=fn_out,
                    departure_airport=origin,
                    arrival_airport=dest,
                    departure_time=out_dep,
                    arrival_time=out_arr,
                    duration=f"PT{int(flight_hours)}H{int((flight_hours % 1) * 60)}M",
                    stops=stops_out,
                )],
                return_segments=[FlightSegment(
                    airline=al_ret[0],
                    flight_no=fn_ret,
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

        current += timedelta(days=1)

    return results


# ─── 输出格式化 ───────────────────────────────────────────────────────────────

def format_duration(iso_duration: str) -> str:
    """PT4H30M -> 4h30m"""
    d = iso_duration.replace("PT", "").lower()
    return d if d else "N/A"


def print_results_table(results: list[FlightOption], top_n: int = 30):
    """使用 rich 打印结果表格"""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        _use_rich = True
    except ImportError:
        _use_rich = False

    sorted_results = sorted(results, key=lambda x: x.total_price)
    display = sorted_results[:top_n]

    if _use_rich:
        _print_rich_table(display, len(sorted_results))
    else:
        _print_plain_table(display, len(sorted_results))


def _print_rich_table(display: list[FlightOption], total_count: int):
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel

    console = Console(width=max(120, os.get_terminal_size().columns) if sys.stdout.isatty() else 130)

    console.print()
    console.print(Panel(
        f"[bold green]共找到 {total_count} 个航班组合，显示最便宜的 {len(display)} 个[/]",
        title="[bold]机票价格监控结果[/]",
        border_style="blue",
    ))

    table = Table(
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
        title_style="bold",
        expand=False,
        padding=(0, 1),
    )

    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("出发日期", min_width=10, no_wrap=True)
    table.add_column("返回日期", min_width=10, no_wrap=True)
    table.add_column("天", width=3, justify="center")
    table.add_column("去程航班", min_width=26, no_wrap=True)
    table.add_column("回程航班", min_width=26, no_wrap=True)
    table.add_column("总价(往返)", min_width=10, justify="right", style="bold", no_wrap=True)

    for i, opt in enumerate(display, 1):
        price_style = "bold green" if i <= 3 else ("yellow" if i <= 10 else "white")
        price_text = f"{opt.currency} {opt.total_price:,.0f}"

        table.add_row(
            str(i),
            opt.outbound_date,
            opt.return_date,
            str(opt.trip_days),
            opt.outbound_summary,
            opt.return_summary,
            f"[{price_style}]{price_text}[/]",
        )

    console.print(table)

    if display:
        cheapest = display[0]
        console.print()
        console.print(
            f"  [bold green]>>> 最低价: {cheapest.currency} {cheapest.total_price:,.0f}[/]"
            f"  |  出发: {cheapest.outbound_date}  返回: {cheapest.return_date}"
            f"  |  {cheapest.outbound_summary}"
        )
        console.print()


def _print_plain_table(display: list[FlightOption], total_count: int):
    print(f"\n{'='*100}")
    print(f"  共找到 {total_count} 个航班组合，显示最便宜的 {len(display)} 个")
    print(f"{'='*100}")
    print(f"{'#':>4}  {'出发日期':^12}  {'返回日期':^12}  {'天数':^4}  {'去程':^30}  {'回程':^30}  {'总价':>12}")
    print(f"{'-'*4}  {'-'*12}  {'-'*12}  {'-'*4}  {'-'*30}  {'-'*30}  {'-'*12}")

    for i, opt in enumerate(display, 1):
        marker = " ***" if i <= 3 else ""
        print(
            f"{i:>4}  {opt.outbound_date:^12}  {opt.return_date:^12}  "
            f"{opt.trip_days:^4}  {opt.outbound_summary:<30}  "
            f"{opt.return_summary:<30}  {opt.currency} {opt.total_price:>8,.0f}{marker}"
        )

    if display:
        cheapest = display[0]
        print(f"\n  >>> 最低价: {cheapest.currency} {cheapest.total_price:,.0f}"
              f"  |  出发: {cheapest.outbound_date}  返回: {cheapest.return_date}")
    print()


def save_results(results: list[FlightOption], filepath: str):
    """保存结果到 JSON 文件"""
    sorted_results = sorted(results, key=lambda x: x.total_price)
    data = []
    for opt in sorted_results:
        d = {
            "outbound_date": opt.outbound_date,
            "return_date": opt.return_date,
            "trip_days": opt.trip_days,
            "total_price": opt.total_price,
            "currency": opt.currency,
            "outbound": opt.outbound_summary,
            "return": opt.return_summary,
            "booking_class": opt.booking_class,
        }
        data.append(d)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存到: {filepath}")


# ─── 主搜索逻辑 ──────────────────────────────────────────────────────────────

def count_departure_dates(
    start_date: datetime,
    end_date: datetime,
    trip_days: int,
) -> int:
    """可扫描的出发日数量（出发日 + trip_days 需在 end_date 之前或当天）。"""
    n = 0
    current = start_date
    while current + timedelta(days=trip_days) <= end_date:
        n += 1
        current += timedelta(days=1)
    return n


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
    request_delay_sec: float = 0.15,
) -> list[FlightOption]:
    """扫描日期范围内所有可能的出发日期。

    verbose=False 时不写 stdout（供飞书后台线程调用）；进度写入 logging。
    """
    all_results: list[FlightOption] = []
    current = start_date
    dates_to_scan: list[datetime] = []

    while current + timedelta(days=trip_days) <= end_date:
        dates_to_scan.append(current)
        current += timedelta(days=1)

    total = len(dates_to_scan)
    if verbose:
        print(f"\n  扫描范围: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
        print(f"  往返天数: {trip_days} 天")
        print(f"  待扫描出发日期: {total} 个\n")
    else:
        _log.info(
            "扫描 %s→%s，%s ~ %s，%s 天往返，共 %s 个出发日",
            origin,
            destination,
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
            trip_days,
            total,
        )

    if total == 0:
        return []

    def _one_day(dt: datetime) -> list[FlightOption]:
        depart = dt.strftime("%Y-%m-%d")
        ret = (dt + timedelta(days=trip_days)).strftime("%Y-%m-%d")
        return searcher.search_round_trip(
            origin,
            destination,
            depart,
            ret,
            adults=adults,
            currency=currency,
            max_results=results_per_date,
            cabin=cabin,
        )

    if verbose:
        try:
            from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn

            with Progress(
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task("搜索航班...", total=total)
                for dt in dates_to_scan:
                    all_results.extend(_one_day(dt))
                    progress.update(task, advance=1)
                    time.sleep(request_delay_sec)
        except ImportError:
            for i, dt in enumerate(dates_to_scan, 1):
                pct = i / total * 100
                depart = dt.strftime("%Y-%m-%d")
                ret = (dt + timedelta(days=trip_days)).strftime("%Y-%m-%d")
                print(f"\r  [{pct:5.1f}%] 搜索 {depart} -> {ret} ...", end="", flush=True)
                all_results.extend(_one_day(dt))
                time.sleep(request_delay_sec)
            print()
    else:
        log_every = max(1, total // 10)
        for i, dt in enumerate(dates_to_scan, 1):
            all_results.extend(_one_day(dt))
            if i == 1 or i % log_every == 0 or i == total:
                _log.debug("航班扫描进度 %s/%s (%s)", i, total, dt.strftime("%Y-%m-%d"))
            time.sleep(request_delay_sec)

    return all_results


# ─── CLI 入口 ────────────────────────────────────────────────────────────────

AIRPORT_ALIASES = {
    "香港": "HKG", "hongkong": "HKG", "hkg": "HKG",
    "大阪": "KIX", "osaka": "KIX", "kix": "KIX",
    "东京": "NRT", "tokyo": "NRT", "nrt": "NRT", "hnd": "HND",
    "首尔": "ICN", "seoul": "ICN", "icn": "ICN",
    "曼谷": "BKK", "bangkok": "BKK", "bkk": "BKK",
    "新加坡": "SIN", "singapore": "SIN", "sin": "SIN",
    "台北": "TPE", "taipei": "TPE", "tpe": "TPE",
    "上海": "PVG", "shanghai": "PVG", "pvg": "PVG", "sha": "SHA",
    "北京": "PEK", "beijing": "PEK", "pek": "PEK",
    "广州": "CAN", "guangzhou": "CAN", "can": "CAN",
    "深圳": "SZX", "shenzhen": "SZX", "szx": "SZX",
    "名古屋": "NGO", "nagoya": "NGO",
    "福冈": "FUK", "fukuoka": "FUK",
    "札幌": "CTS", "sapporo": "CTS",
    "冲绳": "OKA", "okinawa": "OKA", "那霸": "OKA",
    "武汉": "WUH", "wuhan": "WUH", "wuh": "WUH",
    "成都": "CTU", "chengdu": "CTU", "ctu": "CTU",
    "重庆": "CKG", "chongqing": "CKG", "ckg": "CKG",
    "杭州": "HGH", "hangzhou": "HGH", "hgh": "HGH",
    "南京": "NKG", "nanjing": "NKG", "nkg": "NKG",
    "西安": "XIY", "xian": "XIY", "xiy": "XIY",
    "昆明": "KMG", "kunming": "KMG", "kmg": "KMG",
    "厦门": "XMN", "xiamen": "XMN", "xmn": "XMN",
    "长沙": "CSX", "changsha": "CSX", "csx": "CSX",
    "青岛": "TAO", "qingdao": "TAO", "tao": "TAO",
    "大连": "DLC", "dalian": "DLC", "dlc": "DLC",
    "天津": "TSN", "tianjin": "TSN", "tsn": "TSN",
    "郑州": "CGO", "zhengzhou": "CGO", "cgo": "CGO",
}


def resolve_airport(name: str) -> str:
    """将城市名/别名解析为 IATA 代码"""
    key = name.strip().lower()
    if key in AIRPORT_ALIASES:
        return AIRPORT_ALIASES[key]
    if len(name) == 3 and name.isalpha():
        return name.upper()
    return name.upper()


def main():
    load_env()

    parser = argparse.ArgumentParser(
        description="机票价格监控工具 - 搜索最便宜的往返机票",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python flight_monitor.py --demo
  python flight_monitor.py --origin 香港 --dest 大阪 --start 2026-04-20 --end 2026-06-30 --days 5
  python flight_monitor.py --origin HKG --dest KIX --days 7 --cabin ECONOMY --top 50
  python flight_monitor.py --origin 香港 --dest 东京 --days 4 --save results.json

常用城市代码:
  香港=HKG  大阪=KIX  东京=NRT/HND  首尔=ICN  曼谷=BKK
  新加坡=SIN  台北=TPE  上海=PVG  北京=PEK  广州=CAN
        """,
    )

    parser.add_argument("--origin", "-o", default="HKG", help="出发城市/机场代码 (默认: HKG 香港)")
    parser.add_argument("--dest", "-d", default="KIX", help="目的城市/机场代码 (默认: KIX 大阪)")
    parser.add_argument("--start", "-s", default=None, help="搜索起始日期 YYYY-MM-DD (默认: 明天)")
    parser.add_argument("--end", "-e", default=None, help="搜索结束日期 YYYY-MM-DD (默认: 起始+60天)")
    parser.add_argument("--days", "-n", type=int, default=5, help="往返天数 (默认: 5)")
    parser.add_argument("--adults", type=int, default=1, help="成人数量 (默认: 1)")
    parser.add_argument("--currency", default="CNY", help="货币代码 (默认: CNY)")
    parser.add_argument("--cabin", default="", choices=["", "ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"],
                        help="舱位等级 (默认: 全部)")
    parser.add_argument("--top", type=int, default=30, help="显示前N个最便宜结果 (默认: 30)")
    parser.add_argument("--save", default="", help="保存结果到JSON文件")
    parser.add_argument("--production", action="store_true", help="使用 Amadeus 生产环境 (默认用测试环境)")
    parser.add_argument("--demo", action="store_true", help="演示模式，使用模拟数据，无需API密钥")

    args = parser.parse_args()

    origin = resolve_airport(args.origin)
    dest = resolve_airport(args.dest)

    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
    else:
        start_date = datetime.now() + timedelta(days=1)

    if args.end:
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
    else:
        end_date = start_date + timedelta(days=60)

    print(f"""
========================================
      Flight Price Monitor
      机票价格监控工具
========================================

  航线: {origin} → {dest} (往返)
  日期: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}
  行程: {args.days} 天
  人数: {args.adults} 成人
  货币: {args.currency}
  模式: {'演示模式' if args.demo else '生产环境' if args.production else '测试环境'}
""")

    if args.demo:
        print("  [演示模式] 使用模拟数据展示功能...\n")
        all_results = generate_demo_data(origin, dest, start_date, end_date, args.days)
    else:
        client_id = os.environ.get("AMADEUS_CLIENT_ID", "")
        client_secret = os.environ.get("AMADEUS_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            print("  [错误] 未设置 Amadeus API 密钥！")
            print()
            print("  请先注册免费账号: https://developers.amadeus.com/register")
            print("  然后设置环境变量:")
            print("    set AMADEUS_CLIENT_ID=你的client_id")
            print("    set AMADEUS_CLIENT_SECRET=你的client_secret")
            print()
            print("  或使用 --demo 参数查看演示效果:")
            print("    python flight_monitor.py --demo")
            sys.exit(1)

        searcher = AmadeusFlightSearch(client_id, client_secret, production=args.production)
        all_results = scan_dates(
            searcher, origin, dest,
            start_date, end_date, args.days,
            adults=args.adults, currency=args.currency,
            cabin=args.cabin,
        )

    if not all_results:
        print("  未找到任何航班，请尝试调整搜索条件。")
        sys.exit(0)

    print_results_table(all_results, top_n=args.top)

    if args.save:
        save_results(all_results, args.save)


if __name__ == "__main__":
    main()
