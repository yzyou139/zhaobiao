# -*- coding: utf-8 -*-
"""
重庆公共资源交易网 列表页采集
- 支持全部业务类型、发布时间、行政区域筛选
- 纯 requests + lxml，无需登录
"""

import re
import csv
import time
import random
import logging
from urllib.parse import urljoin

import requests
from lxml import etree

# ========================= 配置 =========================
BASE = "https://www.cqggzy.com"
LIST_URL = BASE + "/trade/014"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": LIST_URL,
}

# 筛选参数
DATE = "3d"              # all / today / 3d / 7d / 10d / 3m / range
CATEGORY = "all"          # all / 014001 / 014005 / 014002 / 014006 / 014011 / 014004 / 014014 / 014010 / 014012 / 014009 / 014013
INFOC = "all"             # all / 市级 / 区县名（URL编码）
KEYWORD = ""              # 搜索关键字
MAX_PAGES = None          # None=全部页，或指定页数

OUT_CSV = "cqggzy_list.csv"

FIELDS = [
    "notice_title", "notice_url", "info_region", "notice_type",
    "publish_time", "source", "crawl_time",
]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cqggzy")


# ========================= 列表页解析 =========================
def parse_list_page(page_num: int) -> list:
    """抓取一页列表，返回公告列表"""
    params = {
        "date": DATE,
        "pageNum": str(page_num),
        "categoryNum": CATEGORY,
        "infoc": INFOC,
    }
    if KEYWORD:
        params["keyword"] = KEYWORD

    try:
        resp = requests.get(LIST_URL, headers=HEADERS, params=params, timeout=15)
        resp.encoding = "utf-8"
        html = etree.HTML(resp.text)
    except Exception as e:
        log.warning("第 %s 页请求失败：%s", page_num, e)
        return []

    items = []
    # 每条公告的 li 节点
    li_list = html.xpath('//ul[contains(@class,"min-h-80")]/li')

    for li in li_list:
        # 标题和链接
        a_nodes = li.xpath('.//a[contains(@class,"block text-lg")]')
        if not a_nodes:
            continue
        a = a_nodes[0]
        title = a.xpath("string(.)").strip()
        href = a.xpath("@href")[0].strip()
        detail_url = urljoin(BASE, href)

        # 行政区域
        region_nodes = li.xpath('.//div[contains(@class,"list-item-infoc")]/text()')
        region = region_nodes[0].strip() if region_nodes else ""

        # 公告类型
        type_nodes = li.xpath('.//div[contains(@class,"list-item-type")]/text()')
        notice_type = type_nodes[0].strip() if type_nodes else ""

        # 发布时间：li 末尾的日期文本
        li_text = li.xpath("string(.)").strip()
        m = re.search(r"(\d{4}-\d{2}-\d{2})\s*$", li_text)
        publish_time = m.group(1) if m else ""

        items.append({
            "notice_title": title,
            "notice_url": detail_url,
            "info_region": region,
            "notice_type": notice_type,
            "publish_time": publish_time,
            "source": "重庆市公共资源交易网",
            "crawl_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    return items


# ========================= 获取总页数 =========================
def get_total_pages() -> int:
    """从列表页获取总页数"""
    params = {
        "date": DATE,
        "pageNum": "1",
        "categoryNum": CATEGORY,
        "infoc": INFOC,
    }
    try:
        resp = requests.get(LIST_URL, headers=HEADERS, params=params, timeout=15)
        resp.encoding = "utf-8"
        html = etree.HTML(resp.text)
        # 方法1：从分页链接中取最大值
        page_links = html.xpath('//nav[@aria-label="分页"]//a[contains(@href,"pageNum")]/@href')
        max_page = 1
        for link in page_links:
            m = re.search(r"pageNum=(\d+)", link)
            if m:
                max_page = max(max_page, int(m.group(1)))
        return max_page
    except Exception as e:
        log.warning("获取总页数失败：%s", e)
        return 1


# ========================= 主流程 =========================
def main():
    total_pages = get_total_pages()
    if MAX_PAGES:
        total_pages = min(total_pages, MAX_PAGES)

    log.info("业务类型=%s 时间=%s 区域=%s 共 %s 页",
             CATEGORY, DATE, INFOC, total_pages)

    all_items = []
    seen_urls = set()

    for p in range(1, total_pages + 1):
        log.info("抓取第 %s/%s 页", p, total_pages)
        items = parse_list_page(p)

        added = 0
        for item in items:
            url = item["notice_url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            all_items.append(item)
            added += 1

        log.info("  本页 %s 条，新增 %s 条，累计 %s 条",
                 len(items), added, len(all_items))

        # 实时落盘
        with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            for it in all_items:
                w.writerow(it)

        time.sleep(random.uniform(0.5, 1.5))

    log.info("完成，共 %s 条，已写入 %s", len(all_items), OUT_CSV)


if __name__ == "__main__":
    main()