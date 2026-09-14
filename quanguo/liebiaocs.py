# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 列表采集
- 按天查 → total ≥ 1000 → 按省份下钻（DEAL_PROVINCE，已验证有效）
- 大省仍超 1000 → 按 DEAL_STAGE 尝试下钻
- 全程 id 去重，分片信息落盘
"""

import re
import csv
import json
import time
import random
import logging
from datetime import date, timedelta

import requests

BASE = "https://www.ggzy.gov.cn"
LIST_API = BASE + "/information/pubTradingInfo/getTradList"

DATE_START = date(2026, 9, 8)
DATE_END   = date(2026, 9, 10)

# 已验证可用的基础参数
BASE_PARAMS = {
    "DEAL_CLASSIFY": "02",
    "SOURCE_TYPE":   "1",
    "DEAL_TIME":     "02",
}

LIMIT_THRESHOLD = 1000
PAGE_SLEEP = 0.8
SLICE_SLEEP = 0.4
MAX_RETRIES = 3
TIMEOUT = 20

# 政府采购信息类型（DEAL_STAGE 备用下钻）
GOV_STAGES = ["0201", "0202", "0203", "0204"]

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

OUT_CSV  = "ggzy_list3.csv"
OUT_JSON = "ggzy_list3.json"

FIELDS = [
    "id", "title", "publishTime",
    "informationType", "informationTypeText",
    "businessType", "businessTypeText",
    "province", "provinceText", "city", "cityText",
    "transactionSourcesPlatform", "transactionSourcesPlatformText",
    "industryType", "industryTypeText", "tenderProjectCode",
    "url", "abs_url", "crawl_time",
    "slice_date", "slice_province", "slice_stage",
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


def build_query(day_str, extra=None, page=1):
    q = dict(BASE_PARAMS)
    q["TIMEBEGIN"] = day_str
    q["TIMEEND"]   = day_str
    q["PAGENUMBER"] = str(page)
    if extra:
        q.update(extra)
    return q


def post_list(query):
    for i in range(MAX_RETRIES):
        try:
            r = requests.post(LIST_API, headers=HEADERS, data=query, timeout=TIMEOUT)
            r.raise_for_status()
            j = r.json()
            code = j.get("code")
            if code == 200:
                return j.get("data") or {}
            if code == 804:
                return {"__need_deep__": True, "total": 1000}
            log.warning("  接口 code=%s msg=%s", code, j.get("message"))
            return None
        except Exception as e:
            log.warning("  请求失败 (%s/%s): %s", i + 1, MAX_RETRIES, e)
            time.sleep(1.5 * (i + 1) + random.random())
    return None


def peek(day_str, extra=None):
    """只取第 1 页，看 total/pages"""
    d = post_list(build_query(day_str, extra, 1))
    if d is None:
        return "ERR", 0, 0
    if d.get("__need_deep__"):
        return "DEEP", d.get("total", 1000), 0
    return "OK", d.get("total", 0), d.get("pages", 0)


def fetch_all_pages(day_str, extra, pages, cap=60):
    records = []
    for p in range(1, min(pages, cap) + 1):
        if p > 1:
            time.sleep(PAGE_SLEEP + random.random() * 0.3)
        d = post_list(build_query(day_str, extra, p))
        if d is None or d.get("__need_deep__"):
            break
        recs = d.get("records") or []
        if not recs:
            break
        records.extend(recs)
    return records


# ========================= 下钻 =========================
def crawl_day(day_str):
    """抓一天，需要时按省份下钻"""
    status, total, pages = peek(day_str)

    if status == "ERR":
        log.warning("  %s 第 1 页请求失败", day_str)
        return []
    if total == 0:
        log.info("  %s total=0", day_str)
        return []

    if status == "OK" and total < LIMIT_THRESHOLD:
        log.info("  %s total=%s pages=%s → 直接翻页", day_str, total, pages)
        return fetch_all_pages(day_str, None, pages)

    # total ≥ 1000 → 按省份下钻
    log.warning("  %s total=%s ≥ 1000 → 按省份下钻", day_str, total)
    records = []
    for code, name in PROVINCE_CODES:
        extra = {"DEAL_PROVINCE": code}
        st, t, p = peek(day_str, extra)
        if st == "ERR" or t == 0:
            continue
        if t >= LIMIT_THRESHOLD:
            log.warning("    %s %s total=%s 仍 ≥ 1000 → 按 STAGE 下钻", day_str, name, t)
            for stage in GOV_STAGES:
                st2, t2, p2 = peek(day_str, {**extra, "DEAL_STAGE": stage})
                if st2 == "ERR" or t2 == 0:
                    continue
                if t2 >= LIMIT_THRESHOLD:
                    log.error("      %s %s stage=%s 仍 ≥ 1000，无法继续下钻", day_str, name, stage)
                    continue
                recs = fetch_all_pages(day_str, {**extra, "DEAL_STAGE": stage}, p2)
                for r in recs:
                    r["_slice_date"] = day_str
                    r["_slice_province"] = name
                    r["_slice_stage"] = stage
                records.extend(recs)
                log.info("      %s %s stage=%s total=%s → 抓 %s 条",
                         day_str, name, stage, t2, len(recs))
                time.sleep(SLICE_SLEEP + random.random() * 0.3)
        else:
            recs = fetch_all_pages(day_str, extra, p)
            for r in recs:
                r["_slice_date"] = day_str
                r["_slice_province"] = name
                r["_slice_stage"] = None
            records.extend(recs)
            log.info("    %s %s total=%s → 抓 %s 条", day_str, name, t, len(recs))
        time.sleep(SLICE_SLEEP + random.random() * 0.3)
    return records


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
        "slice_province": rec.get("_slice_province"),
        "slice_stage": rec.get("_slice_stage"),
    }


def save_csv(rows, path):
    if not rows:
        return
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def save_json(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


# ========================= 主流程 =========================
def main():
    all_records = []
    seen = set()
    total_days = (DATE_END - DATE_START).days + 1
    log.info("时间范围：%s ~ %s（%s 天）", DATE_START, DATE_END, total_days)

    for idx, day in enumerate(date_iter(DATE_START, DATE_END), 1):
        day_str = day.strftime("%Y-%m-%d")
        log.info("[%s/%s] %s", idx, total_days, day_str)

        try:
            recs = crawl_day(day_str)
        except Exception as e:
            log.exception("日期 %s 失败：%s", day_str, e)
            recs = []

        added = 0
        for rec in recs:
            rid = rec.get("id")
            if rid and rid in seen:
                continue
            if rid:
                seen.add(rid)
            all_records.append(rec)
            added += 1
        log.info("  → %s 新增 %s 条（累计 %s）", day_str, added, len(all_records))

        if idx % 5 == 0 or idx == total_days:
            rows = [normalize(r) for r in all_records]
            save_csv(rows, OUT_CSV)
            save_json(rows, OUT_JSON)

        time.sleep(SLICE_SLEEP + random.random() * 0.3)


    log.info("完成：%s 条", len(all_records))


if __name__ == "__main__":
    main()