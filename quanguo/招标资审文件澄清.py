# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 招标/资审文件澄清（0105）解析器
- 核心数据来自 /b/ 页面的 2 列 kv 表格
- /a/ 页面补全项目编号
- 支持批量采集
"""
import re
import csv
import json
import time
import random
import datetime
import logging
from typing import Optional, Dict, Any, List, Tuple

from curl_cffi import requests
from lxml import etree

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ggzy")


# ========================= 字段顺序 =========================
FIELDS = [
    "notice_title", "notice_type", "project_code", "publish_time",
    "notice_url", "source", "crawl_time",
    "industry", "admin_region",
    # 0105 澄清公告特有字段
    "file_number",                # 文件编号
    "bid_qualification",          # 投标资格
    "bid_deadline",               # 投标文件递交截止时间
    "bid_validity_period",        # 投标有效期
    "bid_submit_method",          # 投标文件递交方法
    "bid_bond_payment_method",    # 投标保证金缴纳方式
    "bid_bond_amount",            # 投标保证金金额
    "price_ceiling",              # 控制价（最高限价）
    "evaluation_method",          # 评标办法
    "bid_open_time",              # 开标时间
    "bid_open_place",             # 开标地点
    "bid_open_method",            # 开标方式
    "qualification_review_method",# 资格审查方式
    "clarification_time",         # 答疑澄清时间
    "is_postponed",               # 是否延期
    "new_bid_open_time",          # 延期后开标时间
    "new_bid_open_place",         # 延期后开标地点
    "clarification_content",      # 对文件澄清与修改的主要内容
    "content_text", "attach_file_names", "attach_json",
]


# ========================= 行业分类 =========================
INDUSTRY_RULES = [
    ("工程建设", ["工程", "施工", "建筑", "装修", "改造", "修缮", "监理",
                 "市政", "道路", "桥梁", "管网", "电梯", "产业园", "厂房", "公路", "养护"]),
    ("医疗卫生", ["医疗", "医院", "卫生", "医药", "医学", "器械", "疾控", "保健", "药品"]),
    ("教育培训", ["学校", "大学", "学院", "教育", "培训", "教学", "体育"]),
    ("公共安全", ["消防", "公安", "警务", "安防", "应急", "救援", "司法"]),
    ("信息技术", ["软件", "信息化", "数字化", "智慧", "信息系统", "系统集成",
                 "计算机", "机房", "云计算", "大数据", "运维", "网络安全"]),
    ("市政交通", ["轨道", "公交", "停车", "路灯", "隧道"]),
    ("水利环保", ["水利", "水务", "供水", "排水", "污水", "环保", "生态", "垃圾"]),
    ("能源电力", ["电力", "电网", "光伏", "风电", "能源", "燃气", "配电"]),
    ("农林牧渔", ["农业", "农村", "林业", "畜牧", "渔业", "种植", "农田"]),
    ("文化旅游", ["文化", "旅游", "图书", "博物馆", "景区", "文物"]),
    ("服务外包", ["物业", "保洁", "保安", "绿化", "食堂", "餐饮", "劳务",
                 "外包", "维保", "租赁", "印刷", "审计", "咨询"]),
    ("科研检测", ["实验室", "科研", "检测", "试验", "认证", "计量"]),
    ("设备购置", ["设备", "仪器", "车辆", "空调", "家具", "购置"]),
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


# ========================= 行政区域 =========================
CITY_MAP = {
    "广州": ("广东省", "广州市"), "深圳": ("广东省", "深圳市"),
    "珠海": ("广东省", "珠海市"), "佛山": ("广东省", "佛山市"),
    "南沙": ("广东省", "广州市"), "青岛": ("山东省", "青岛市"),
    "成都": ("四川省", "成都市"), "阿坝": ("四川省", "阿坝州"),
    "武汉": ("湖北省", "武汉市"), "西安": ("陕西省", "西安市"),
    "重庆": ("重庆市", ""), "北京": ("北京市", ""),
    "上海": ("上海市", ""), "天津": ("天津市", ""),
}


def extract_admin_region(source: str, title: str) -> str:
    text = (source or "") + " " + (title or "")
    for key, (prov, city) in CITY_MAP.items():
        if key in text:
            return f"{prov}/{city}" if city else prov
    return ""


# ========================= 工具 =========================
def normalize_url(url: str) -> str:
    return re.sub(r"/\./", "/", url or "")


def extract_text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.xpath(".//text()"))).strip()


def parse_datetime(raw: str) -> str:
    if not raw:
        return ""
    m = re.search(
        r"(\d{4})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})\s*日?\s*"
        r"(?:(\d{1,2})\s*[时:：点]\s*(\d{1,2})\s*分?(?::\d{2})?)?",
        raw
    )
    if not m:
        return raw.strip()
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    h = int(m.group(4)) if m.group(4) else 0
    mi = int(m.group(5)) if m.group(5) else 0
    return f"{y}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"


def parse_amount(raw: str) -> Optional[float]:
    if not raw:
        return None
    m = re.search(r"([\d,]+\.?\d*)", raw)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if "万元" in raw:
        return round(v, 6)
    return round(v / 10000, 6)


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
        r"(?:招标项目编号|项目编号|招标编号|采购编号|标段编号)[\s:：]*([A-Za-z0-9\-_]+)",
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


# ========================= 表格解析 =========================
def extract_kv_from_tables(tree) -> Dict[str, str]:
    """
    从 /b/ 页面的表格中提取 key-value 对。
    0105 澄清公告的表格是标准的 2 列结构：<tr><td>key</td><td>value</td></tr>
    """
    result = {}

    for root_sel in [
        '//div[contains(@class, "detail_content")]',
        '//div[@id="mycontent"]',
    ]:
        roots = tree.xpath(root_sel)
        if not roots:
            continue
        root = roots[0]

        for table in root.xpath('.//table'):
            for tr in table.xpath('./tbody/tr | ./tr'):
                cells = tr.xpath('./td|./th')
                if len(cells) != 2:
                    continue
                key = extract_text(cells[0]).strip().rstrip("：:")
                val = extract_text(cells[1]).strip()
                if key and val and key not in result:
                    result[key] = val
        break

    return result


# ========================= 主解析 =========================
def parse_notice(tree, url: str) -> Dict[str, Any]:
    data = {k: "" for k in FIELDS}
    data["notice_url"] = normalize_url(url)
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["notice_type"] = "招标/资审文件澄清"

    # 标题
    t = tree.xpath('//h4[@class="h4_o"]/text()')
    data["notice_title"] = t[0].strip() if t else ""

    # 发布时间
    pub_nodes = tree.xpath('//p[@class="p_o"]/span[contains(text(),"发布时间")]//text()')
    for s in pub_nodes:
        m = re.search(r"发布时间[：:]\s*(.+)", s)
        if m:
            data["publish_time"] = m.group(1).strip()
            break

    # 来源
    src = tree.xpath('//label[@id="platformName"]/text()')
    data["source"] = src[0].strip() if src else ""

    # 项目编号：优先从 p_o 提取
    po_text = "".join(tree.xpath('//p[@class="p_o"]//text()'))
    m = re.search(
        r"(?:招标项目编号|项目编号|招标编号|采购编号|标段编号)[\s:：]*([A-Za-z0-9\-_]+)",
        po_text
    )
    if m:
        data["project_code"] = m.group(1).strip()

    # 正文
    content = extract_content_text(tree)
    data["content_text"] = content

    # ============ 核心：从表格提取 kv ============
    kv = extract_kv_from_tables(tree)
    log.info(f"[表格] 提取到 {len(kv)} 个 kv 字段: {list(kv.keys())[:8]}...")

    # 映射到标准字段
    data["file_number"] = kv.get("文件编号", "")
    data["bid_qualification"] = kv.get("投标资格", "")
    data["bid_deadline"] = kv.get("投标文件递交截止时间", "")
    data["bid_validity_period"] = kv.get("投标有效期", "")
    data["bid_submit_method"] = kv.get("投标文件递交方法", "")
    data["bid_bond_payment_method"] = kv.get("投标保证金缴纳方式", "")
    data["bid_bond_amount"] = kv.get("投标保证金金额", "")
    data["price_ceiling"] = kv.get("控制价（最高限价）", "") or kv.get("控制价(最高限价)", "")
    data["evaluation_method"] = kv.get("评标办法", "")
    data["bid_open_time"] = kv.get("开标时间", "")
    data["bid_open_place"] = kv.get("开标地点", "")
    data["bid_open_method"] = kv.get("开标方式", "")
    data["qualification_review_method"] = kv.get("资格审查方式", "")
    data["clarification_time"] = kv.get("答疑澄清时间", "")
    data["is_postponed"] = kv.get("是否延期", "")
    data["new_bid_open_time"] = kv.get("延期后开标时间", "")
    data["new_bid_open_place"] = kv.get("延期后开标地点", "")
    data["clarification_content"] = kv.get("对文件澄清与修改的主要内容", "")

    # 时间字段格式化
    for k in ("bid_deadline", "bid_open_time", "clarification_time", "new_bid_open_time"):
        if data.get(k):
            data[k] = parse_datetime(data[k])

    # 项目编号兜底
    if not data["project_code"]:
        m = re.search(
            r"(?:招标项目编号|项目编号|招标编号|采购编号)[\s:：]*([A-Za-z0-9\-_]+)",
            content
        )
        if m:
            data["project_code"] = m.group(1).strip()

    # 行业 / 行政区域
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

    # /a/ 补全项目编号
    if not data["project_code"]:
        a_meta = fetch_metadata_from_a_page(url)
        if a_meta.get("project_code"):
            data["project_code"] = a_meta["project_code"]
            log.info(f"[/a/补全] 项目编号: {data['project_code']}")
        if not data["source"] and a_meta.get("source"):
            data["source"] = a_meta["source"]

    return data


# ========================= 单条 / 批量 =========================
def parse_one(url: str) -> Dict[str, Any]:
    tree = fetch_detail_tree(url)
    if tree is None:
        return {}
    return parse_notice(tree, url)


def parse_batch(urls, out_csv: str = "clarification.csv", sleep_range=(1.0, 2.0)):
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


# ========================= 测试 =========================
if __name__ == "__main__":
    test_urls = [
        # 广东（0105 澄清公告）
        "https://www.ggzy.gov.cn/information/deal/html/b/130000/0105/20260910/00134eb6c2bfcb20430396d3b687bdd0785f.html",
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