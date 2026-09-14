# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 全来源全业务类型采集（纯全量版）
- 6 来源 × 11 业务类型 × N 天，全部组合都跑
- 央企走 BID_PLATFORM，其他走 DEAL_PROVINCE
- 并发 2，延迟 0.8/0.4，风控熔断
"""

import os
import re
import csv
import json
import time
import random
import logging
import warnings
import threading
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

warnings.filterwarnings("ignore")

# ========================= 配置 =========================
BASE = "https://www.ggzy.gov.cn"
LIST_API = BASE + "/information/pubTradingInfo/getTradList"

DATE_START = date(2026, 9, 8)
DATE_END   = date(2026, 9, 10)

LIMIT_THRESHOLD = 1000   #单次查询上限
PAGE_SLEEP  = 0.8      #翻页延迟
SLICE_SLEEP = 0.4       #分片延迟
MAX_RETRIES = 3         #最大重试次数
TIMEOUT     = 20        #请求超时
MAX_WORKERS = 1        #并发数
SAVE_EVERY_NEW = 300     #落盘频率

# ==== 数据来源 ====
SOURCE_TYPES = {
    "1": "省平台",
    "2": "央企招投标",
    "3": "财政部",
    "4": "自然资源部",
    "5": "国资委",
    "6": "商务部",
}

# ==== 央企的 BID_PLATFORM ====
BID_PLATFORMS = [
    "C1100000259", "C1100000096", "D1100000008", "D1100000098",
    "B1100000014", "E1100000179", "G1100000193", "D1100000001",
    "D1100000002", "C1101080263", "D1100000011", "B1100000013",
    "L1100000165", "I1100000181", "C1101000272", "I1100000073",
    "D1100000088", "B1100000010", "E1100000089", "C1100000005",
    "B1100000009", "D1100000269", "I1100000196", "F1101080264",
    "C1100000189", "G1100000175", "D1100000152", "C1100000187",
    "D1100000028", "D1305000277", "D1501000258", "C2103000033",
    "C2201000275", "C1101080262", "C3100000019", "E3309000192",
    "I3701000256", "C4201000018", "C4201000016", "D4401000176",
    "D4403000371", "I1100000755", "E5101000890", "D1101080855",
    "D4403030148", "D1100000133", "K4403050754", "D1101080711",
    "E1100000900", "E1100000879", "I1100000727", "M1100000751",
    "A2301010892", "E4103010399", "I1101080883", "G1100000081",
    "G1101020874", "B1100000286", "L1100000076", "G1100000997",
    "K4403050904", "C1100000952", "E3100000977", "D3100000943",
    "G3100000948", "G1100000926", "B1100000958", "D1100000279",
    "G1100005013", "E1100000932",
]

CLASSIFY_MAP = {
    "01": "工程建设", "02": "政府采购", "03": "土地使用权", "04": "矿业权",
    "05": "国有产权", "21": "碳排放权", "22": "排污权", "23": "药品采购权",
    "24": "二类疫苗", "25": "林权", "90": "其他",
}

STAGE_MAP = {
    "01": ["0101", "0102", "0104", "0105"],
    "02": ["0201", "0202", "0203", "0204"],
    "03": ["0301", "0302"],
    "04": ["0401", "0402", "0403", "0404"],
    "05": ["0501", "0502"],
    "21": ["2101", "2102"],
    "22": ["2201", "2202"],
    "23": ["2302", "2303", "2304"],
    "24": ["2402", "2403", "2404"],
    "25": ["2501", "2502"],
    "90": ["9001", "9002"],
}

STAGE_NAME = {
    "0101": "招标/资审公告", "0102": "开标记录",
    "0104": "交易结果公示", "0105": "招标/资审文件澄清",
    "0201": "采购/资审公告", "0202": "中标公告",
    "0203": "采购合同", "0204": "更正事项",
    "0301": "出让公示", "0302": "成交宗地",
    "0401": "出让公告", "0402": "出让结果",
    "0403": "公开信息", "0404": "登记公告信息",
    "0501": "挂牌披露", "0502": "交易结果",
    "2101": "出售公告", "2102": "结果公示",
    "2201": "交易公告", "2202": "结果公示",
    "2302": "交易公告", "2303": "交易目录", "2304": "变更公告",
    "2402": "交易公告", "2403": "交易目录", "2404": "变更公告",
    "2501": "信息披露", "2502": "成交公告",
    "9001": "交易公告", "9002": "成交公示",
}

PROVINCE_CODES = [
    ("110000", "北京"), ("120000", "天津"), ("130000", "河北"), ("140000", "山西"),
    ("150000", "内蒙古"), ("210000", "辽宁"), ("220000", "吉林"), ("230000", "黑龙江"),
    ("310000", "上海"), ("320000", "江苏"), ("330000", "浙江"), ("340000", "安徽"),
    ("350000", "福建"), ("360000", "江西"), ("370000", "山东"), ("410000", "河南"),
    ("420000", "湖北"), ("430000", "湖南"), ("440000", "广东"), ("450000", "广西"),
    ("460000", "海南"), ("500000", "重庆"), ("510000", "四川"), ("520000", "贵州"),
    ("530000", "云南"), ("540000", "西藏"), ("610000", "陕西"), ("620000", "甘肃"),
    ("630000", "青海"), ("640000", "宁夏"), ("650000", "新疆"), ("660000", "兵团"),
]

CITY_MAP = {
    "510000": [
        ("510100", "成都"), ("510300", "自贡"), ("510400", "攀枝花"), ("510500", "泸州"),
        ("510600", "德阳"), ("510700", "绵阳"), ("510800", "广元"), ("510900", "遂宁"),
        ("511000", "内江"), ("511100", "乐山"), ("511300", "南充"), ("511400", "眉山"),
        ("511500", "宜宾"), ("511600", "广安"), ("511700", "达州"), ("511800", "雅安"),
        ("511900", "巴中"), ("512000", "资阳"),
        ("513200", "阿坝"), ("513300", "甘孜"), ("513400", "凉山"),
    ],
}

OUT_CSV  = "ggzy_listql.csv"
OUT_JSON = "ggzy_listql.json"
OUT_PROGRESS = "progress.json"
OUT_INCOMPLETE = "incomplete.json"

FIELDS = [
    "id", "title", "publishTime",
    "informationType", "informationTypeText",
    "businessType", "businessTypeText",
    "province", "provinceText", "city", "cityText",
    "transactionSourcesPlatform", "transactionSourcesPlatformText",
    "industryType", "industryTypeText", "tenderProjectCode",
    "url", "abs_url", "crawl_time",
    "slice_date", "slice_source", "slice_classify",
    "slice_province", "slice_stage", "slice_city", "slice_bid_platform",
]

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": BASE,
    "Pragma": "no-cache",
    "Referer": BASE + "/deal/dealList.html?HEADER_DEAL_TYPE=02",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
    "X-Pass-Token": "",
    "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ggzy")


# ========================= Session =========================
_thread_local = threading.local()


def get_session():
    if not hasattr(_thread_local, "s"):
        s = requests.Session()
        s.headers.update(HEADERS)
        _thread_local.s = s
    return _thread_local.s


# ========================= 熔断 =========================
class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._pause_until = 0.0

    def pause(self, seconds=60):
        with self._lock:
            self._pause_until = max(self._pause_until, time.time() + seconds)
            log.warning("⛔ 触发风控，全局暂停 %s 秒", seconds)

    def wait(self):
        while True:
            with self._lock:
                now = time.time()
                if now >= self._pause_until:
                    return
                wait_s = self._pause_until - now
            time.sleep(min(wait_s, 3))


_limiter = RateLimiter()


# ========================= 状态 =========================
STATE = {
    "start_time": None,
    "total_records": 0,
    "per_source": {},
    "per_classify": {},
    "per_day": {},
    "current": {},
    "incomplete": [],
    "updated_at": None,
}
_state_lock = threading.Lock()
_save_lock = threading.Lock()


# ========================= 工具 =========================
def one_line(s):
    if s is None:
        return None
    return re.sub(r"\s+", " ", str(s).replace("\xa0", " ")).strip()


def abs_url(rel):
    if not rel:
        return None
    if rel.startswith("http"):
        return rel
    if not rel.startswith("/"):
        rel = "/" + rel
    return BASE + rel


def date_iter(a, b):
    cur = a
    while cur <= b:
        yield cur
        cur += timedelta(days=1)


def build_query(day_str, source_type, classify, extra=None, page=1):
    q = {
        "SOURCE_TYPE": source_type,
        "DEAL_CLASSIFY": classify,
        "DEAL_TIME": "02",
        "TIMEBEGIN": day_str,
        "TIMEEND": day_str,
        "PAGENUMBER": str(page),
    }
    if extra:
        q.update(extra)
    return q


def write_progress():
    with _state_lock:
        STATE["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(OUT_PROGRESS, "w", encoding="utf-8") as f:
                json.dump(STATE, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# ========================= 请求 =========================
def post_list(query):
    _limiter.wait()
    s = get_session()
    for i in range(MAX_RETRIES):
        try:
            r = s.post(LIST_API, data=query, timeout=TIMEOUT)
            if r.status_code in (403, 429):
                _limiter.pause(120)
                return None
            if r.status_code >= 500:
                _limiter.pause(30)
                return None
            r.raise_for_status()
            j = r.json()
            code = j.get("code")
            if code == 200:
                return j.get("data") or {}
            if code == 804:
                return {"__need_deep__": True, "total": 1000}
            if code in (403, 429, 999):
                _limiter.pause(120)
                return None
            log.warning("  接口 code=%s msg=%s", code, j.get("message"))
            return None
        except Exception as e:
            log.warning("  请求失败 (%s/%s): %s", i + 1, MAX_RETRIES, e)
            time.sleep(1.5 * (i + 1) + random.random())
    return None


def peek(day_str, source, classify, extra=None):
    d = post_list(build_query(day_str, source, classify, extra, 1))
    if d is None:
        return "ERR", 0, 0
    if d.get("__need_deep__"):
        return "DEEP", d.get("total", 1000), 0
    return "OK", d.get("total", 0), d.get("pages", 0)


def fetch_all_pages(day_str, source, classify, extra, pages, cap=60):
    records = []
    for p in range(1, min(pages, cap) + 1):
        if p > 1:
            time.sleep(PAGE_SLEEP + random.random() * 0.3)
        d = post_list(build_query(day_str, source, classify, extra, p))
        if d is None or d.get("__need_deep__"):
            break
        recs = d.get("records") or []
        if not recs:
            break
        records.extend(recs)
    return records


def tag(rec, day_str, source, classify,
        prov=None, stage=None, city=None, bid_platform=None):
    rec["_slice_date"] = day_str
    rec["_slice_source"] = source
    rec["_slice_classify"] = classify
    rec["_slice_province"] = prov
    rec["_slice_stage"] = stage
    rec["_slice_city"] = city
    rec["_slice_bid_platform"] = bid_platform


def add_incomplete(day_str, source, classify, reason, **kw):
    with _state_lock:
        item = {"date": day_str, "source": source,
                "classify": classify, "reason": reason}
        item.update(kw)
        STATE["incomplete"].append(item)


# ========================= 省市分支（非央企）=========================
def crawl_city(day_str, source, classify, prov_code, prov_name, stage, base_total):
    cities = CITY_MAP.get(prov_code)
    if not cities:
        log.warning("      无市字典 %s stage=%s → incomplete", prov_name, stage)
        add_incomplete(day_str, source, classify, "no_city_map",
                       province=prov_name, stage=stage, total=base_total)
        return []
    records = []
    for ccode, cname in cities:
        extra = {"DEAL_PROVINCE": prov_code,
                 "DEAL_STAGE": stage,
                 "DEAL_CITY": ccode}
        st, t, p = peek(day_str, source, classify, extra)
        if st == "ERR" or t == 0:
            continue
        if t >= LIMIT_THRESHOLD:
            log.error("      %s %s stage=%s 仍超 1000 → incomplete",
                      prov_name, cname, stage)
            add_incomplete(day_str, source, classify, "city_over",
                           province=prov_name, city=cname,
                           stage=stage, total=t)
            continue
        recs = fetch_all_pages(day_str, source, classify, extra, p)
        for r in recs:
            tag(r, day_str, source, classify, prov_name, stage, cname)
        records.extend(recs)
        time.sleep(SLICE_SLEEP + random.random() * 0.3)
    return records


def crawl_stage(day_str, source, classify, prov_code, prov_name):
    # 探测该省该业务类型是否有 stage 细分
    probe_any = False
    for stage in STAGE_MAP.get(classify, []):
        st, t, _ = peek(day_str, source, classify,
                        {"DEAL_PROVINCE": prov_code, "DEAL_STAGE": stage})
        if st == "OK" and t > 0:
            probe_any = True
            break

    if not probe_any:
        log.warning("    %s 所有 stage 都是 0 → 退回按省整体翻页", prov_name)
        extra = {"DEAL_PROVINCE": prov_code}
        st, t, p = peek(day_str, source, classify, extra)
        if st == "OK" and t > 0:
            recs = fetch_all_pages(day_str, source, classify, extra, p)
            for r in recs:
                tag(r, day_str, source, classify, prov_name)
            return recs
        return []

    records = []
    for stage in STAGE_MAP.get(classify, []):
        extra = {"DEAL_PROVINCE": prov_code, "DEAL_STAGE": stage}
        st, t, p = peek(day_str, source, classify, extra)
        if st == "ERR" or t == 0:
            continue
        if t < LIMIT_THRESHOLD:
            recs = fetch_all_pages(day_str, source, classify, extra, p)
            for r in recs:
                tag(r, day_str, source, classify, prov_name, stage)
            records.extend(recs)
            log.info("      %s %s [%s] total=%s → %s 条",
                     prov_name, stage, STAGE_NAME.get(stage, ""), t, len(recs))
        else:
            log.warning("      %s %s [%s] total=%s ≥ 1000 → 按市下钻",
                        prov_name, stage, STAGE_NAME.get(stage, ""), t)
            records.extend(crawl_city(day_str, source, classify,
                                       prov_code, prov_name, stage, t))
        time.sleep(SLICE_SLEEP + random.random() * 0.3)
    return records


def crawl_province(day_str, source, classify, prov_code, prov_name):
    extra = {"DEAL_PROVINCE": prov_code}
    st, t, p = peek(day_str, source, classify, extra)
    if st == "ERR" or t == 0:
        return []
    if t < LIMIT_THRESHOLD:
        recs = fetch_all_pages(day_str, source, classify, extra, p)
        for r in recs:
            tag(r, day_str, source, classify, prov_name)
        log.info("    %s total=%s → %s 条", prov_name, t, len(recs))
        return recs
    log.warning("    %s total=%s ≥ 1000 → 按 stage 下钻", prov_name, t)
    return crawl_stage(day_str, source, classify, prov_code, prov_name)


def crawl_day_province(day_str, source, classify):
    st, t, p = peek(day_str, source, classify)
    if st == "ERR" or t == 0:
        return []
    if st == "OK" and t < LIMIT_THRESHOLD:
        recs = fetch_all_pages(day_str, source, classify, None, p)
        for r in recs:
            tag(r, day_str, source, classify)
        log.info("  [%s] total=%s → %s 条",
                 SOURCE_TYPES.get(source, source), t, len(recs))
        return recs
    log.warning("  [%s] total=%s ≥ 1000 → 按省下钻",
                SOURCE_TYPES.get(source, source), t)
    records = []
    for code, name in PROVINCE_CODES:
        records.extend(crawl_province(day_str, source, classify, code, name))
        time.sleep(SLICE_SLEEP + random.random() * 0.3)
    return records


# ========================= 央企分支 =========================
def crawl_bid_platform(day_str, source, classify, platform, base_total):
    extra = {"BID_PLATFORM": platform}
    st, t, p = peek(day_str, source, classify, extra)
    if st == "ERR" or t == 0:
        return []
    if t >= LIMIT_THRESHOLD:
        log.error("    央企平台 %s 仍超 1000 → incomplete", platform)
        add_incomplete(day_str, source, classify, "bid_over",
                       bid_platform=platform, total=t)
        return []
    recs = fetch_all_pages(day_str, source, classify, extra, p)
    for r in recs:
        tag(r, day_str, source, classify, bid_platform=platform)
    return recs


def crawl_bid_stage(day_str, source, classify):
    records = []
    for stage in STAGE_MAP.get(classify, []):
        extra = {"DEAL_STAGE": stage}
        st, t, p = peek(day_str, source, classify, extra)
        if st == "ERR" or t == 0:
            continue
        if t < LIMIT_THRESHOLD:
            recs = fetch_all_pages(day_str, source, classify, extra, p)
            for r in recs:
                tag(r, day_str, source, classify, stage=stage)
            records.extend(recs)
            log.info("      央企 %s [%s] total=%s → %s 条",
                     stage, STAGE_NAME.get(stage, ""), t, len(recs))
        else:
            log.warning("      央企 %s [%s] total=%s ≥ 1000 → 按 BID_PLATFORM 下钻",
                        stage, STAGE_NAME.get(stage, ""), t)
            for bp in BID_PLATFORMS:
                records.extend(crawl_bid_platform(day_str, source, classify, bp, t))
                time.sleep(SLICE_SLEEP + random.random() * 0.3)
        time.sleep(SLICE_SLEEP + random.random() * 0.3)
    return records


def crawl_day_bid(day_str, source, classify):
    st, t, p = peek(day_str, source, classify)
    if st == "ERR" or t == 0:
        return []
    if st == "OK" and t < LIMIT_THRESHOLD:
        recs = fetch_all_pages(day_str, source, classify, None, p)
        for r in recs:
            tag(r, day_str, source, classify)
        log.info("  [央企] total=%s → %s 条", t, len(recs))
        return recs
    log.warning("  [央企] total=%s ≥ 1000 → 按 stage 下钻", t)
    return crawl_bid_stage(day_str, source, classify)


def crawl_day(day_str, source, classify):
    if source == "2":     # 央企走 BID_PLATFORM
        return crawl_day_bid(day_str, source, classify)
    return crawl_day_province(day_str, source, classify)


# ========================= 输出 =========================
def normalize(rec):
    rel = rec.get("url") or ""
    return {
        "id": rec.get("id"),
        "title": one_line(rec.get("title")),
        "publishTime": rec.get("publishTime"),
        "informationType": rec.get("informationType"),
        "informationTypeText": rec.get("informationTypeText"),
        "businessType": rec.get("businessType"),
        "businessTypeText": rec.get("businessTypeText"),
        "province": rec.get("province"),
        "provinceText": rec.get("provinceText"),
        "city": rec.get("city"),
        "cityText": rec.get("cityText"),
        "transactionSourcesPlatform": rec.get("transactionSourcesPlatform"),
        "transactionSourcesPlatformText": rec.get("transactionSourcesPlatformText"),
        "industryType": rec.get("industryType"),
        "industryTypeText": rec.get("industryTypeText"),
        "tenderProjectCode": rec.get("tenderProjectCode"),
        "url": rel,
        "abs_url": abs_url(rel),
        "crawl_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "slice_date": rec.get("_slice_date"),
        "slice_source": rec.get("_slice_source"),
        "slice_classify": rec.get("_slice_classify"),
        "slice_province": rec.get("_slice_province"),
        "slice_stage": rec.get("_slice_stage"),
        "slice_city": rec.get("_slice_city"),
        "slice_bid_platform": rec.get("_slice_bid_platform"),
    }


def save_all(all_records, force=False):
    with _save_lock:
        try:
            rows = [normalize(r) for r in all_records]
            with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
                w.writeheader()
                for r in rows:
                    w.writerow(r)
            with open(OUT_JSON, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            with open(OUT_INCOMPLETE, "w", encoding="utf-8") as f:
                json.dump(STATE["incomplete"], f, ensure_ascii=False, indent=2)
            write_progress()
        except Exception as e:
            log.warning("落盘失败：%s", e)


# ========================= 主流程 =========================
def main():
    STATE["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_progress()

    all_records = []
    seen = set()
    days = list(date_iter(DATE_START, DATE_END))

    # 全量组合：6 源 × 11 类 × N 天
    tasks = [
        (src, sname, cc, cname, d)
        for src, sname in SOURCE_TYPES.items()
        for cc, cname in CLASSIFY_MAP.items()
        for d in days
    ]

    log.info("=" * 60)
    log.info("数据来源：%s 种 %s",
             len(SOURCE_TYPES), list(SOURCE_TYPES.values()))
    log.info("业务类型：%s 种", len(CLASSIFY_MAP))
    log.info("日期范围：%s ~ %s（%s 天）", DATE_START, DATE_END, len(days))
    log.info("总任务数：%s", len(tasks))
    log.info("并发数：%s PAGE_SLEEP=%s SLICE_SLEEP=%s",
             MAX_WORKERS, PAGE_SLEEP, SLICE_SLEEP)
    log.info("=" * 60)

    def run_task(task):
        src, sname, classify, cname, day = task
        day_str = day.strftime("%Y-%m-%d")
        with _state_lock:
            STATE["current"] = {
                "source": src, "source_name": sname,
                "classify": classify, "classify_name": cname,
                "day": day_str,
            }
        log.info("── [%s|%s] %s ──", sname, cname, day_str)
        try:
            return src, sname, classify, cname, day_str, \
                   crawl_day(day_str, src, classify)
        except Exception as e:
            log.exception("任务失败 [%s|%s] %s：%s", sname, cname, day_str, e)
            return src, sname, classify, cname, day_str, []

    completed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(run_task, t): t for t in tasks}
        for fut in as_completed(futures):
            src, sname, classify, cname, day_str, recs = fut.result()
            completed += 1

            with _state_lock:
                added = 0
                for rec in recs:
                    rid = rec.get("id")
                    if rid and rid in seen:
                        continue
                    if rid:
                        seen.add(rid)
                    all_records.append(rec)
                    added += 1
                total = len(all_records)
                STATE["total_records"] = total
                STATE["per_source"][src] = STATE["per_source"].get(src, 0) + added
                STATE["per_classify"][classify] = \
                    STATE["per_classify"].get(classify, 0) + added
                STATE["per_day"][day_str] = \
                    STATE["per_day"].get(day_str, 0) + added

            log.info("[%s/%s] [%s|%s] %s 新增 %s（累计 %s）",
                     completed, len(tasks), sname, cname, day_str, added, total)

            if total // SAVE_EVERY_NEW != (total - added) // SAVE_EVERY_NEW:
                save_all(list(all_records), force=True)
                log.info("  💾 已落盘：%s 条", total)

            write_progress()

    save_all(list(all_records), force=True)

    log.info("=" * 60)
    log.info("全部完成，共 %s 条", len(all_records))
    log.info("按来源：")
    for c, n in sorted(STATE["per_source"].items(), key=lambda x: -x[1]):
        log.info("  %s %s: %s", c, SOURCE_TYPES.get(c, ""), n)
    log.info("按业务类型：")
    for c, n in sorted(STATE["per_classify"].items(), key=lambda x: -x[1]):
        log.info("  %s %s: %s", c, CLASSIFY_MAP.get(c, ""), n)
    log.info("按天：")
    for d, n in sorted(STATE["per_day"].items()):
        log.info("  %s: %s", d, n)
    if STATE["incomplete"]:
        log.warning("有 %s 个分片未抓全，见 %s",
                    len(STATE["incomplete"]), OUT_INCOMPLETE)
    log.info("=" * 60)


if __name__ == "__main__":
    main()