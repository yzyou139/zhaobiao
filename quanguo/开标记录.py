# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 开标记录（0102）解析器
主流模板：4 行 2 列 kv 表格（开标参与人/开标地点/开标时间/开标记录内容）
支持三种格式：
1. 标准多列表格（每行一个投标人）
2. 4 行 kv 表格（唱标记录藏在"开标记录内容"单元格里）
3. 全文文本
"""
import re
import csv
import json
import time
import random
import datetime
import logging
from typing import Optional, Dict, Any, List

from curl_cffi import requests
from lxml import etree

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ggzy")


FIELDS = [
    "notice_title", "notice_type", "project_code", "publish_time",
    "notice_url", "source", "crawl_time",
    "industry", "admin_region",
    "bid_open_time", "bid_open_place", "bid_open_record",
    "buyer_name", "agent_name",
    "bidder_count", "bidders_json",
    "content_text",
    "attach_file_names", "attach_json",
]


# ========================= 唱标字段映射 =========================
BIDDER_FIELD_MAP = {
    "投标人名称": "投标人",
    "投标人": "投标人",
    "投标单位": "投标人",
    "投标单位名称": "投标人",
    "投标企业": "投标人",

    "报价": "投标报价",
    "投标报价": "投标报价",
    "投标总价": "投标报价",
    "投标总价（元）": "投标报价",
    "投标报价（元）": "投标报价",
    "投标报价(元)": "投标报价",

    "工期": "工期",
    "投标工期": "工期",
    "工期（交货期）": "工期",
    "工期(交货期)": "工期",
    "工期（日历天）": "工期",

    "项目负责人": "项目负责人",
    "项目经理": "项目负责人",

    "质量标准": "质量要求",
    "质量要求": "质量要求",
    "质量目标": "质量要求",

    "投标保证金": "投标保证金",
    "保证金": "投标保证金",
    "保证金金额": "投标保证金",

    "投标文件递交时间": "投标文件递交时间",
    "安全文明施工措施费": "安全文明施工措施费",
    "密封情况": "密封情况",
    "签名": "签名",
    "备注": "备注",
}


# ========================= 英文月份映射 =========================
MONTH_MAP_EN = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    "January": 1, "February": 2, "March": 3, "April": 4, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10,
    "November": 11, "December": 12,
}


# ========================= 行业分类 =========================
INDUSTRY_RULES = [
    ("医疗卫生", ["医疗", "医院", "卫生", "药", "医学", "器械", "疾控", "保健"]),
    ("教育培训", ["学校", "大学", "学院", "教育", "培训", "教学", "体育"]),
    ("公共安全", ["消防", "公安", "警务", "安防", "应急", "救援", "司法"]),
    ("信息技术", ["软件", "信息化", "数字化", "智慧", "信息系统", "系统集成",
                 "计算机", "机房", "云计算", "大数据", "运维", "网络安全"]),
    ("工程建设", ["工程", "施工", "建筑", "装修", "改造", "修缮", "监理",
                 "市政", "道路", "桥梁", "管网", "高标准农田"]),
    ("市政交通", ["公路", "交通", "轨道", "公交", "停车", "路灯", "隧道"]),
    ("水利环保", ["水利", "水务", "供水", "排水", "污水", "环保", "生态", "垃圾"]),
    ("能源电力", ["电力", "电网", "光伏", "风电", "能源", "燃气", "配电"]),
    ("农林牧渔", ["农业", "农村", "林业", "畜牧", "渔业", "种植", "农田", "田园"]),
    ("文化旅游", ["文化", "旅游", "图书", "博物馆", "景区", "文物"]),
    ("服务外包", ["物业", "保洁", "保安", "绿化", "食堂", "餐饮", "劳务",
                 "外包", "维保", "租赁", "印刷", "审计", "咨询"]),
    ("科研检测", ["实验室", "科研", "检测", "试验", "认证", "计量"]),
    ("设备购置", ["设备", "仪器", "车辆", "空调", "电梯", "家具", "购置"]),
]


def classify_industry(title: str, content: str) -> str:
    for name, kws in INDUSTRY_RULES:
        if any(kw in (title or "") for kw in kws):
            return name
    sample = (content or "")[:600]
    for name, kws in INDUSTRY_RULES:
        if any(kw in sample for kw in kws):
            return name
    return "其他"


# ========================= 行政区域映射 =========================
# 按"长地名优先"排序，避免"成都"覆盖"成都高新区"之类
CITY_MAP = {
    # 四川
    "成都": ("四川省", "成都市"), "绵阳": ("四川省", "绵阳市"),
    "阿坝": ("四川省", "阿坝州"), "金川": ("四川省", "阿坝州"),
    "甘孜": ("四川省", "甘孜州"), "凉山": ("四川省", "凉山州"),
    # 贵州
    "安顺": ("贵州省", "安顺市"), "贵阳": ("贵州省", "贵阳市"),
    "遵义": ("贵州省", "遵义市"), "六盘水": ("贵州省", "六盘水市"),
    # 湖北
    "武汉": ("湖北省", "武汉市"), "宜昌": ("湖北省", "宜昌市"),
    "襄阳": ("湖北省", "襄阳市"),
    # 陕西
    "西安": ("陕西省", "西安市"), "铜川": ("陕西省", "铜川市"),
    # 云南
    "昆明": ("云南省", "昆明市"), "大理": ("云南省", "大理市"),
    "迪庆": ("云南省", "迪庆州"), "维西": ("云南省", "迪庆州"),
    # 直辖市
    "重庆": ("重庆市", ""), "北京": ("北京市", ""),
    "上海": ("上海市", ""), "天津": ("天津市", ""),
}


def extract_admin_region(source: str, title: str) -> str:
    """
    只从 source 和 notice_title 提取，避免正文里的投标人公司名干扰。
    （比如"成都立行建设工程..."会让 admin_region 错认成成都）
    """
    text = (source or "") + " " + (title or "")
    for key, (prov, city) in CITY_MAP.items():
        if key in text:
            if city:
                return f"{prov}/{city}"
            return prov
    return ""


# ========================= 工具 =========================
def normalize_url(url: str) -> str:
    return re.sub(r"/\./", "/", url or "")


def extract_text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.xpath(".//text()"))).strip()


def parse_datetime(raw: str) -> str:
    """统一转成 YYYY-MM-DD HH:MM，支持中文/ISO/英文月份格式"""
    if not raw:
        return ""
    raw = raw.strip()

    # 格式1：中文或 ISO：2026-09-10 / 2026年9月10日 / 2026-09-10 09:30
    m = re.search(
        r"(\d{4})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})\s*日?\s*"
        r"(?:(\d{1,2})\s*[时:：点]\s*(\d{1,2})\s*分?)?",
        raw
    )
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        h = int(m.group(4)) if m.group(4) else 0
        mi = int(m.group(5)) if m.group(5) else 0
        return f"{y}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"

    # 格式2：英文月份：Sep 10, 2026 / September 10, 2026
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", raw)
    if m:
        month_name = m.group(1)
        d = int(m.group(2))
        y = m.group(3)
        mo = MONTH_MAP_EN.get(month_name) or MONTH_MAP_EN.get(month_name[:3])
        if mo:
            return f"{y}-{mo:02d}-{d:02d} 00:00"

    return raw.strip()


# ========================= 抓取 =========================
def fetch_detail_tree(url: str, timeout: int = 20, max_hops: int = 3):
    def _fetch(u):
        try:
            r = requests.get(u, impersonate="chrome120", timeout=timeout)
            r.raise_for_status()
            r.encoding = "utf-8"
            return etree.HTML(r.text)
        except Exception:
            return None

    current = normalize_url(url)
    visited = set()
    for _ in range(max_hops):
        if current in visited:
            break
        visited.add(current)
        tree = _fetch(current)
        if tree is None:
            return None
        iframe_src = tree.xpath(
            '//div[contains(@class,"detailShow")]//iframe/@src '
            '| //div[contains(@class,"fully_toggle_cont")]//iframe/@src'
        )
        next_url = None
        if iframe_src:
            s = (iframe_src[0] or "").strip()
            if s:
                next_url = s if s.startswith("http") else "https://www.ggzy.gov.cn" + s
        if not next_url and "/information/deal/html/a/" in current:
            next_url = current.replace("/information/deal/html/a/", "/information/deal/html/b/")
        if not next_url or next_url in visited or next_url == current:
            return tree
        current = next_url
    return None


def fetch_metadata_from_a_page(url: str, timeout: int = 20) -> dict:
    result = {"project_code": "", "source": ""}
    a_url = url.replace("/html/b/", "/html/a/")
    if a_url == url:
        return result
    try:
        r = requests.get(a_url, impersonate="chrome120", timeout=timeout)
        r.raise_for_status()
        r.encoding = "utf-8"
        tree = etree.HTML(r.text)
    except Exception:
        return result
    po_text = "".join(tree.xpath('//p[@class="p_o"]//text()'))
    m = re.search(
        r"(?:招标项目编号|项目编号|招标编号|采购编号|标段编号)[\s:：]*([^\s]+)",
        po_text
    )
    if m:
        result["project_code"] = m.group(1).strip()
    src = tree.xpath('//label[@id="platformName"]/text()')
    if src:
        result["source"] = src[0].strip()
    return result


def extract_content_text(tree) -> str:
    for selector in [
        '//div[contains(@class, "detail_content")]',
        '//div[@id="mycontent"]',
    ]:
        nodes = tree.xpath(selector)
        if not nodes:
            continue
        root = nodes[0]
        parts = []
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            if el.tag in ("p", "div", "td", "th", "li", "br",
                          "h1", "h2", "h3", "h4", "tr"):
                parts.append("\n")
            if el.text:
                parts.append(el.text)
            if el.tail:
                parts.append(el.tail)
        text = "".join(parts)
        text = re.sub(r"[ \t\u3000]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        return text
    return ""


# ========================= 唱标记录解析（三路兜底） =========================
def extract_bidders(tree) -> List[Dict[str, str]]:
    bidders = _extract_from_table_rows(tree)
    if bidders:
        log.info(f"[唱标] 路1-标准表格，{len(bidders)} 个投标人")
        return bidders

    bidders = _extract_from_td_cells(tree)
    if bidders:
        log.info(f"[唱标] 路2-单元格文本，{len(bidders)} 个投标人")
        return bidders

    bidders = _extract_from_full_text(tree)
    if bidders:
        log.info(f"[唱标] 路3-全文文本，{len(bidders)} 个投标人")
        return bidders

    log.warning("[唱标] 三路均未找到投标人明细")
    return []


def _extract_from_table_rows(tree) -> List[Dict[str, str]]:
    """路 1：标准多列表格，每行一个投标人"""
    bidders = []
    for root_sel in [
        '//div[contains(@class, "detail_content")]',
        '//div[@id="mycontent"]',
    ]:
        roots = tree.xpath(root_sel)
        if not roots:
            continue
        root = roots[0]

        for table in root.xpath('.//table'):
            rows = table.xpath('./tbody/tr | ./tr')
            if len(rows) < 2:
                continue
            header_cells = rows[0].xpath('./td|./th')
            if not header_cells:
                continue
            headers = [extract_text(td) for td in header_cells]
            header_join = " ".join(headers)

            if "投标人" not in header_join and "投标单位" not in header_join:
                continue

            # 排除"4行kv"的表：只有2列且第二列内容很长（其实是文本单元格）
            if len(headers) == 2 and ("开标记录内容" in headers[0] or "开标参与人" in headers[0]):
                continue

            col_map = {}
            for i, h in enumerate(headers):
                for k in sorted(BIDDER_FIELD_MAP.keys(), key=len, reverse=True):
                    if k == h or k in h:
                        col_map[i] = BIDDER_FIELD_MAP[k]
                        break

            if not col_map:
                continue

            for tr in rows[1:]:
                cells = [extract_text(td) for td in tr.xpath('./td|./th')]
                if not any(cells):
                    continue
                if cells[0] and ("合计" in cells[0] or "总计" in cells[0]):
                    continue
                row = {}
                for i, cell in enumerate(cells):
                    if i in col_map and cell:
                        row[col_map[i]] = cell
                if row.get("投标人"):
                    bidders.append(row)
            if bidders:
                return bidders
    return bidders


def _extract_from_td_cells(tree) -> List[Dict[str, str]]:
    """路 2：4行kv表格里，"开标记录内容"单元格里是文本"""
    all_bidders = []

    for td in tree.xpath('//div[contains(@class,"detail_content")]//td | //div[@id="mycontent"]//td'):
        text = "".join(td.xpath(".//text()"))
        text = re.sub(r"\s+", " ", text).strip()

        if "投标人名称" not in text and "投标单位名称" not in text:
            continue
        if len(text) < 30:
            continue

        bidders = _parse_bidders_from_text(text)
        if bidders:
            all_bidders.extend(bidders)

    # 去重
    seen = set()
    unique = []
    for b in all_bidders:
        name = b.get("投标人", "")
        if name and name not in seen:
            seen.add(name)
            unique.append(b)
    return unique


def _extract_from_full_text(tree) -> List[Dict[str, str]]:
    content = extract_content_text(tree)
    return _parse_bidders_from_text(content)


def _parse_bidders_from_text(text: str) -> List[Dict[str, str]]:
    parts = re.split(r"(?=(?:投标人名称|投标单位名称|投标人)\s*[：:])", text)

    bidders = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.search(r"(?:投标人名称|投标单位名称|投标人)\s*[：:]", part)
        if not m:
            continue
        part = part[m.start():]
        bidder = _parse_one_bidder_text(part)
        if bidder and bidder.get("投标人"):
            bidders.append(bidder)
    return bidders


def _parse_one_bidder_text(text: str) -> Dict[str, str]:
    result = {}
    parts = re.split(r"[；;]", text)

    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([^：:]{1,30})\s*[：:]\s*(.+)$", part)
        if not m:
            continue
        key = m.group(1).strip()
        value = re.sub(r"[，,。；;、]+$", "", m.group(2).strip()).strip()

        std_key = None
        for k in sorted(BIDDER_FIELD_MAP.keys(), key=len, reverse=True):
            if k == key:
                std_key = BIDDER_FIELD_MAP[k]
                break
        if not std_key:
            for k in sorted(BIDDER_FIELD_MAP.keys(), key=len, reverse=True):
                if k in key:
                    std_key = BIDDER_FIELD_MAP[k]
                    break

        if std_key and value and std_key not in result:
            result[std_key] = value
    return result


# ========================= 提取开标时间/地点 =========================
def extract_open_info(tree, content: str) -> Dict[str, str]:
    result = {"bid_open_time": "", "bid_open_place": "", "bid_open_record": ""}

    # 从 4 行 kv 表格精确提取
    for table in tree.xpath('//div[contains(@class,"detail_content")]//table | //div[@id="mycontent"]//table'):
        for tr in table.xpath('./tbody/tr | ./tr'):
            cells = tr.xpath('./td|./th')
            if len(cells) != 2:
                continue
            key = extract_text(cells[0]).strip("：: ")
            val = extract_text(cells[1]).strip()
            if not key or not val:
                continue
            if key == "开标时间":
                result["bid_open_time"] = parse_datetime(val)
            elif key == "开标地点":
                result["bid_open_place"] = val

    # 全文兜底
    if not result["bid_open_time"]:
        m = re.search(r"开标时间\s*[：:]\s*([^\n]+)", content)
        if m:
            result["bid_open_time"] = parse_datetime(m.group(1))
    if not result["bid_open_place"]:
        m = re.search(r"开标地点\s*[：:]\s*([^\n]+)", content)
        if m:
            result["bid_open_place"] = m.group(1).strip()

    # 开标记录内容（正常开标 / 流标 / 等）
    m = re.search(r"开标记录内容\s*[：:]?\s*\n?\s*([^\n]+)", content)
    if m:
        first = m.group(1).strip()
        if "投标人" not in first:
            result["bid_open_record"] = first

    return result


# ========================= 主解析 =========================
def parse_notice(tree, url: str) -> Dict[str, Any]:
    data = {k: "" for k in FIELDS}
    data["notice_url"] = normalize_url(url)
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["notice_type"] = "开标记录"

    t = tree.xpath('//h4[@class="h4_o"]/text()')
    data["notice_title"] = t[0].strip() if t else ""

    pub_nodes = tree.xpath(
        '//p[@class="p_o"]/span[contains(text(),"发布时间")]//text()'
    )
    for s in pub_nodes:
        m = re.search(r"发布时间[：:]\s*(.+)", s)
        if m:
            data["publish_time"] = m.group(1).strip()
            break

    src = tree.xpath('//label[@id="platformName"]/text()')
    data["source"] = src[0].strip() if src else ""

    po_text = "".join(tree.xpath('//p[@class="p_o"]//text()'))
    m = re.search(
        r"(?:招标项目编号|项目编号|招标编号|采购编号|标段编号)[\s:：]*([^\s]+)",
        po_text
    )
    if m:
        data["project_code"] = m.group(1).strip()

    content = extract_content_text(tree)
    data["content_text"] = content

    # 开标信息
    open_info = extract_open_info(tree, content)
    data["bid_open_time"] = open_info["bid_open_time"]
    data["bid_open_place"] = open_info["bid_open_place"]
    data["bid_open_record"] = open_info["bid_open_record"]

    # 唱标记录
    bidders = extract_bidders(tree)
    data["bidder_count"] = len(bidders)
    data["bidders_json"] = json.dumps(bidders, ensure_ascii=False) if bidders else ""

    # 招标人 / 代理机构
    m = re.search(r"招标人\s*[：:]\s*([^\n，。；]+)", content)
    if m:
        data["buyer_name"] = m.group(1).strip()
    m = re.search(r"(?:招标代理机构|采购代理机构|代理机构)\s*[：:]\s*([^\n，。；]+)", content)
    if m:
        data["agent_name"] = m.group(1).strip()

    # 行业 / 行政区域（行政区只从 source + title 提取，避免投标人公司名干扰）
    data["industry"] = classify_industry(data["notice_title"], content)
    data["admin_region"] = extract_admin_region(data["source"], data["notice_title"])

    # 附件
    attach = []
    for a in tree.xpath('//a[@class="bizDownload"]'):
        name = extract_text(a)
        uuid = a.xpath("./@id")
        href = a.xpath("./@href")
        if uuid:
            full = f"https://download.ggzy.gov.cn/oss/download?uuid={uuid[0].strip()}"
        elif href:
            full = href[0].strip()
        else:
            continue
        if name:
            attach.append({"attach_name": name, "attach_url": full})
    if not attach:
        for a in tree.xpath(
            '//div[contains(@class,"detail_content")]//a'
            '[contains(@href, "downloadFile") or contains(@href, ".pdf") '
            'or contains(@href, ".doc") or contains(@href, ".xls")]'
        ):
            href = a.xpath("./@href")
            name = extract_text(a)
            if href and name:
                attach.append({"attach_name": name, "attach_url": href[0].strip()})
    if attach:
        data["attach_file_names"] = ",".join(x["attach_name"] for x in attach)
        data["attach_json"] = json.dumps(attach, ensure_ascii=False)

    # /a/ 补全编号
    if not data["project_code"]:
        a_meta = fetch_metadata_from_a_page(url)
        if a_meta.get("project_code"):
            data["project_code"] = a_meta["project_code"]
            log.info(f"[/a/补全] 项目编号: {data['project_code']}")
        if not data["source"] and a_meta.get("source"):
            data["source"] = a_meta["source"]

    return data


def parse_one(url: str) -> Dict[str, Any]:
    tree = fetch_detail_tree(url)
    if tree is None:
        return {}
    return parse_notice(tree, url)


def parse_batch(urls, out_csv: str = "kaibiao.csv", sleep_range=(1.0, 2.0)):
    import os
    done_urls = set()
    if os.path.exists(out_csv):
        try:
            with open(out_csv, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if row.get("notice_url"):
                        done_urls.add(normalize_url(row["notice_url"]))
        except Exception:
            pass
    todo = [u for u in urls if normalize_url(u) not in done_urls]
    print(f"待处理 {len(todo)} 条（已跳过 {len(done_urls)} 条）")
    is_new = len(done_urls) == 0
    with open(out_csv, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        for i, url in enumerate(todo, 1):
            print(f"\n[{i}/{len(todo)}] {url}")
            try:
                data = parse_one(url)
                if data:
                    writer.writerow(data)
                    f.flush()
                    print(f"  ✓ {data.get('notice_title', '')[:50]}")
                else:
                    print(f"  ✗ 解析失败")
            except Exception as e:
                print(f"  ✗ 异常: {e}")
            time.sleep(random.uniform(*sleep_range))
    print(f"\n完成，输出：{out_csv}")


if __name__ == "__main__":
    test_urls = [
        # 四川（4行kv，唱标在单元格文本里）
        "https://www.ggzy.gov.cn/information/deal/html/b/510000/0102/20260910/005121314a00f8f940fc9a63a9a8edaac676.html",
        # 贵州（页面里只有"正常开标"）
        "https://www.ggzy.gov.cn/information/deal/html/b/520000/0102/20260910/00520272d08b2d354a72bbc27cd026ce990d.html",
    ]
    for url in test_urls:
        print(f"\n{'=' * 80}\nURL: {url}\n{'=' * 80}")
        data = parse_one(url)
        if not data:
            print("抓取失败")
            continue
        for k in FIELDS:
            v = data.get(k)
            if v in ("", None):
                continue
            if isinstance(v, str) and len(v) > 300:
                v = v[:300] + "..."
            print(f"{k}: {v}")