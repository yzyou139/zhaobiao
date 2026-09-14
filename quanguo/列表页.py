# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 政府采购列表页采集（只抓列表 API，不进详情页）
接口：POST https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList
"""

import re
import csv
import json
import time
import random
import logging

import requests

# ========================= 配置 =========================
BASE = "https://www.ggzy.gov.cn"
LIST_API = BASE + "/information/pubTradingInfo/getTradList"

# 查询参数（按需改这里就行）
LIST_QUERY = {
    "DEAL_CLASSIFY": "",       # 02 政府采购；01 工程建设；03 土地使用权 ...
    "SOURCE_TYPE": "1",
    "DEAL_TIME": "03",           # 时间维度：01 今日 / 02 近XX天自定义 / 03 近3天 ...
    "PAGENUMBER": "1",           # 代码循环覆盖
}

# 抓多少页（None = 按接口返回的总页数全抓）
MAX_PAGES = 60
# 每页间隔（秒）
PAGE_SLEEP = 1.0
# 每页条数（接口默认 20，若接口支持 size 可改）
PAGE_SIZE = 20

OUT_CSV = "ggzy_list.csv"
OUT_JSON = "ggzy_list.json"

# 导出字段（顺序就是 CSV 列顺序）
FIELDS = [
    "id",
    "title",
    "publishTime",
    "informationType",
    "informationTypeText",
    "businessType",
    "businessTypeText",
    "province",
    "provinceText",
    "city",
    "cityText",
    "transactionSourcesPlatform",
    "transactionSourcesPlatformText",
    "industryType",
    "industryTypeText",
    "tenderProjectCode",
    "url",          # 原始相对路径
    "abs_url",      # 拼上域名的完整地址
    "crawl_time",
]

# 请求头（照搬你 列表页.py 里的）
HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
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


# ========================= 工具函数 =========================
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


def dedup_key(rec):
    """按 id 去重（没有 id 则退回 title+publishTime+url）"""
    if rec.get("id"):
        return rec["id"]
    return "|".join([
        str(rec.get("title") or ""),
        str(rec.get("publishTime") or ""),
        str(rec.get("url") or ""),
    ])


# ========================= 列表页抓取 =========================
def fetch_list_page(page_num=1, timeout=20, retries=3):
    data = dict(LIST_QUERY)
    data["PAGENUMBER"] = str(page_num)
    data.setdefault("PAGESIZE", str(PAGE_SIZE))

    for i in range(retries):
        try:
            r = requests.post(LIST_API, headers=HEADERS, data=data, timeout=timeout)
            r.raise_for_status()
            j = r.json()
            if j.get("code") != 200:
                log.warning("接口返回非 200：%s", j.get("message"))
                return None
            return j.get("data") or {}
        except Exception as e:
            log.warning("列表请求失败 (%s/%s) page=%s : %s", i + 1, retries, page_num, e)
            time.sleep(1.5 * (i + 1) + random.random())
    return None


def crawl_list(max_pages=MAX_PAGES):
    all_records = []
    seen_keys = set()

    page = 1
    total_pages = 1
    total_count = 0

    while True:
        if max_pages is not None and page > max_pages:
            log.info("达到 MAX_PAGES=%s，停止翻页", max_pages)
            break

        log.info("抓取列表页 %s / %s", page, total_pages if total_pages else "?")
        d = fetch_list_page(page)
        if not d:
            log.warning("第 %s 页无数据，停止", page)
            break

        recs = d.get("records") or []
        total_pages = d.get("pages") or total_pages
        total_count = d.get("total") or total_count

        if not recs:
            log.info("第 %s 页返回空，停止", page)
            break

        added = 0
        for rec in recs:
            k = dedup_key(rec)
            if k in seen_keys:
                continue
            seen_keys.add(k)
            all_records.append(rec)
            added += 1

        log.info("第 %s 页：接口 %s 条，去重后新增 %s 条，累计 %s 条",
                 page, len(recs), added, len(all_records))

        if page >= total_pages:
            break

        page += 1
        time.sleep(PAGE_SLEEP + random.random() * 0.4)

    log.info("列表抓取完成：接口 total=%s，pages=%s，实际拿到 %s 条",
             total_count, total_pages, len(all_records))
    return all_records


# ========================= 字段整理 =========================
def normalize(rec):
    rel = rec.get("url") or ""
    row = {
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
    }
    return row


# ========================= 输出 =========================
def save_csv(rows, path):
    if not rows:
        log.warning("无数据，跳过 CSV")
        return
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    log.info("已写入 CSV：%s（%s 行）", path, len(rows))


def save_json(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    log.info("已写入 JSON：%s", path)


# ========================= 主流程 =========================
def main():
    records = crawl_list(max_pages=MAX_PAGES)
    rows = [normalize(r) for r in records]

    # 控制台先看几条
    for r in rows[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=2))

    save_csv(rows, OUT_CSV)
    save_json(rows, OUT_JSON)
    log.info("完成：%s 条", len(rows))


if __name__ == "__main__":
    main()