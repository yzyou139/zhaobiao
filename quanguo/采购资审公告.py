# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 采购/资审公告（0201）解析器 v5
修复：
1. contact_tel 用 re.search 全局找第一个 ≥7 位号码
2. contact_name 剔除标签词后取第一个中文名
3. 行业分类：信息技术加"平台建设"，设备购置收紧关键词
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
    "buyer_name", "buyer_address", "buyer_contact", "buyer_tel",
    "agent_name", "agent_address", "agent_contact", "agent_tel",
    "project_name", "purchase_method",
    "budget", "price_ceiling",
    "bid_deadline", "bid_open_time", "bid_open_place",
    "bid_file_get_time", "bid_file_get_place", "bid_file_price",
    "qualification_requirement", "procurement_policy",
    "contact_name", "contact_tel",
    "content_text", "attach_file_names", "attach_json",
]


# ========================= 行业分类 =========================
INDUSTRY_RULES = [
    ("工程建设", ["工程", "施工", "建筑", "装修", "改造", "修缮", "监理",
                 "市政", "道路", "桥梁", "管网", "电梯", "产业园", "厂房", "公路", "养护"]),
    ("医疗卫生", ["医疗", "医院", "卫生", "医药", "医学", "器械", "疾控", "保健", "药品"]),
    ("教育培训", ["学校", "大学", "学院", "教育", "培训", "教学", "体育", "课桌椅", "桌椅"]),
    ("公共安全", ["消防", "公安", "警务", "安防", "应急", "救援", "司法"]),
    ("信息技术", ["软件", "信息化", "数字化", "智慧", "信息系统", "系统集成",
                 "计算机", "机房", "云计算", "大数据", "运维", "网络安全",
                 "平台建设", "平台开发", "平台采购", "管理系统", "电子化", "电子政务"]),
    ("市政交通", ["轨道", "公交", "停车", "路灯", "隧道"]),
    ("水利环保", ["水利", "水务", "供水", "排水", "污水", "环保", "生态", "垃圾"]),
    ("能源电力", ["电力", "电网", "光伏", "风电", "能源", "燃气", "配电"]),
    ("农林牧渔", ["农业", "农村", "林业", "畜牧", "渔业", "种植", "农田"]),
    ("文化旅游", ["文化", "旅游", "图书", "博物馆", "景区", "文物"]),
    ("服务外包", ["物业", "保洁", "保安", "绿化", "食堂", "餐饮", "劳务",
                 "外包", "维保", "租赁", "印刷", "审计", "咨询"]),
    ("科研检测", ["实验室", "科研", "检测", "试验", "认证", "计量"]),
    ("设备购置", ["设备采购", "设备购置", "仪器采购", "车辆采购", "家具采购",
                 "实验设备", "办公设备"]),
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
    "赣州": ("江西省", "赣州市"), "南昌": ("江西省", "南昌市"),
    "安远": ("江西省", "赣州市"), "萍乡": ("江西省", "萍乡市"),
    "遂宁": ("四川省", "遂宁市"), "成都": ("四川省", "成都市"),
    "广州": ("广东省", "广州市"), "深圳": ("广东省", "深圳市"),
    "青岛": ("山东省", "青岛市"), "武汉": ("湖北省", "武汉市"),
    "西安": ("陕西省", "西安市"),
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
        r"(?:采购项目编号|招标项目编号|项目编号|招标编号|采购编号|标段编号)[\s:：]*([A-Za-z0-9\-_]+)",
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
        # 删除 style/script 节点
        for bad in root.xpath('.//style | .//script'):
            bad.getparent().remove(bad)
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


# ========================= 字段别名（统一到英文 key） =========================
FIELD_ALIASES = {
    "project_name": ["项目名称", "采购项目名称", "项目名称及编号"],
    "purchase_method": ["采购方式", "招标方式"],
    "budget": ["预算金额", "采购预算", "预算总金额"],
    "price_ceiling": ["最高限价", "控制价", "招标控制价", "最高投标限价"],
    "bid_deadline": ["响应文件提交截止时间", "响应文件递交截止时间",
                     "投标文件递交截止时间", "投标截止时间", "响应截止时间",
                     "递交响应文件截止时间"],
    "bid_open_time": ["开启时间", "开标时间", "开启时间（开标时间）"],
    "bid_open_place": ["开启地点", "开标地点"],
    "buyer_name": ["采购人", "采购人名称", "采购单位", "采购单位名称"],
    "buyer_contact": ["采购人联系人"],
    "buyer_tel": ["采购人联系电话"],
    "agent_name": ["代理机构", "采购代理机构", "代理机构名称"],
    "agent_contact": ["代理机构联系人"],
    "agent_tel": ["代理机构联系电话"],
    "bid_file_get_time": ["获取采购文件时间", "获取询价文件时间", "获取招标文件时间"],
    "bid_file_get_place": ["获取采购文件地点", "获取询价文件地点", "获取招标文件地点"],
    "bid_file_price": ["采购文件售价", "文件售价"],
    "qualification_requirement": ["供应商资格要求", "供应商的资格要求",
                                   "申请人资格要求", "资格要求", "投标人资格要求"],
    "procurement_policy": ["落实的政府采购政策", "采购项目需要落实的政府采购政策",
                            "政府采购政策"],
}


def extract_kv_from_content(content: str) -> Dict[str, str]:
    result = {}
    lines = [l.strip() for l in content.split("\n")]
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        m = re.match(r"^([^：:]{1,30})\s*[：:]\s*(.*)$", line)
        if m:
            key_raw = m.group(1).strip()
            value = m.group(2).strip()
            matched_field = None
            for field, aliases in FIELD_ALIASES.items():
                if field in result:
                    continue
                for alias in aliases:
                    if alias == key_raw or alias in key_raw:
                        matched_field = field
                        break
                if matched_field:
                    break
            if matched_field:
                if value:
                    result[matched_field] = value
                elif i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line and not re.match(r"^[^：:]{1,30}\s*[：:]", next_line):
                        result[matched_field] = next_line
        i += 1
    return result


def extract_kv_from_tables(tree) -> Dict[str, str]:
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
            pending_key = None
            for tr in table.xpath('./tbody/tr | ./tr'):
                cells = tr.xpath('./td|./th')
                if not cells:
                    continue
                if len(cells) >= 2:
                    key_raw = extract_text(cells[0]).strip().rstrip("：:")
                    val = extract_text(cells[1]).strip()
                    if not key_raw or not val:
                        continue
                    for field, aliases in FIELD_ALIASES.items():
                        if field in result:
                            continue
                        for alias in aliases:
                            if alias == key_raw or alias in key_raw:
                                result[field] = val
                                break
                        else:
                            continue
                        break
                    continue
                text = extract_text(cells[0]).strip()
                if not text:
                    continue
                if pending_key is None:
                    pending_key = text.rstrip("：:")
                else:
                    for field, aliases in FIELD_ALIASES.items():
                        if field in result:
                            continue
                        for alias in aliases:
                            if alias == pending_key or alias in pending_key:
                                result[field] = text
                                break
                        else:
                            continue
                        break
                    pending_key = None
        break
    return result


# ========================= 联系方式章节（上下文感知） =========================
def extract_contact_section(content: str) -> Dict[str, str]:
    """
    从"1.采购人信息 / 2.采购代理机构信息 / 3.项目联系方式"章节提取。
    兼容多种字段名写法：
    - 采购人 / 采购人名称 / 名称
    - 采购人地址 / 地址
    - 采购人电话 / 联系电话 / 电话
    - 代理机构 / 采购代理机构 / 名称
    - 代理机构地址 / 地址
    - 代理机构电话 / 联系电话 / 电话
    - 项目联系人电话 / 电话
    """
    result = {}

    # 预处理：在章节标记前强制加换行
    content_norm = re.sub(
        r"(?<!^)(\d\s*[.、]\s*(?:采购人信息|采购代理机构信息|代理机构信息|项目联系方式))",
        r"\n\1",
        content
    )
    lines = [l.strip() for l in content_norm.split("\n")]
    context = None

    for line in lines:
        if not line:
            continue

        # 上下文切换（兼容 . 和 、）
        if re.match(r"^1\s*[.、]\s*采购人信息", line):
            context = "buyer"
            continue
        if re.match(r"^2\s*[.、]\s*(?:采购)?代理机构信息", line):
            context = "agent"
            continue
        if re.match(r"^3\s*[.、]\s*项目联系方式", line):
            context = "contact"
            continue

        if context is None:
            continue

        # ★ 扩展字段名正则
        m = re.match(
            r"^(名称|地址|联系方式|联系电话|联系人|电话|项目联系人|项目联系人电话|"
            r"采购人|采购人名称|采购经办人|采购人电话|采购人地址|"
            r"代理机构|代理机构名称|代理机构经办人|代理机构电话|代理机构地址)"
            r"\s*[：:]\s*(.+)$",
            line
        )
        if not m:
            continue
        key = m.group(1).strip()
        val = m.group(2).strip()

        # 电话：全局找第一个 ≥7 位号码
        if "电话" in key or "联系" in key:
            m2 = re.search(r"(\d{3,4}[-—]?\d{7,8}|\d{7,12})", val)
            if m2:
                val = m2.group(1)
            else:
                continue

        if context == "buyer":
            # 名称类
            if key in ("名称", "采购人", "采购人名称") and "buyer_name" not in result:
                result["buyer_name"] = val
            # 地址类
            elif key in ("地址", "采购人地址") and "buyer_address" not in result:
                result["buyer_address"] = val
            # 电话类
            elif key in ("联系方式", "联系电话", "电话", "采购人电话") and "buyer_tel" not in result:
                result["buyer_tel"] = val
            # 联系人（采购经办人）
            elif key in ("联系人", "采购经办人") and "buyer_contact" not in result:
                result["buyer_contact"] = val

        elif context == "agent":
            if key in ("名称", "代理机构", "代理机构名称") and "agent_name" not in result:
                result["agent_name"] = val
            elif key in ("地址", "代理机构地址") and "agent_address" not in result:
                result["agent_address"] = val
            elif key in ("联系方式", "联系电话", "电话", "代理机构电话") and "agent_tel" not in result:
                result["agent_tel"] = val
            elif key in ("联系人", "代理机构经办人") and "agent_contact" not in result:
                result["agent_contact"] = val

        elif context == "contact":
            if key in ("项目联系人", "联系人") and "contact_name" not in result:
                cleaned = re.sub(
                    r"(项目负责人|负责人|经办人|联系人|技术审核|项目联系人|代理机构|采购人)",
                    "", val
                )
                cleaned = re.sub(r"\d+\s*[.、]", " ", cleaned)
                m3 = re.search(r"([\u4e00-\u9fa5]{2,6})", cleaned)
                if m3:
                    result["contact_name"] = m3.group(1)
            elif key in ("电话", "项目联系人电话", "联系方式", "联系电话") and "contact_tel" not in result:
                result["contact_tel"] = val

    return result


# ========================= 主解析 =========================
def parse_notice(tree, url: str) -> Dict[str, Any]:
    data = {k: "" for k in FIELDS}
    data["notice_url"] = normalize_url(url)
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["notice_type"] = "采购/资审公告"

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

    # 项目编号
    po_text = "".join(tree.xpath('//p[@class="p_o"]//text()'))
    m = re.search(
        r"(?:采购项目编号|招标项目编号|项目编号|招标编号|采购编号|标段编号)[\s:：]*([A-Za-z0-9\-_]+)",
        po_text
    )
    if m:
        data["project_code"] = m.group(1).strip()

    # 正文
    content = extract_content_text(tree)
    data["content_text"] = content

    # ============ kv 提取（表格优先，再正文） ============
    kv_table = extract_kv_from_tables(tree)
    kv_content = extract_kv_from_content(content)
    kv = dict(kv_content)
    for k, v in kv_table.items():
        if v:
            kv[k] = v

    log.info(f"[kv] 提取到 {len(kv)} 个字段: {list(kv.keys())}")

    data["project_name"] = kv.get("project_name", "")
    data["purchase_method"] = kv.get("purchase_method", "")
    data["budget"] = kv.get("budget", "")
    data["price_ceiling"] = kv.get("price_ceiling", "")
    data["bid_deadline"] = kv.get("bid_deadline", "")
    data["bid_open_time"] = kv.get("bid_open_time", "")
    data["bid_open_place"] = kv.get("bid_open_place", "")
    data["buyer_name"] = kv.get("buyer_name", "")
    data["buyer_contact"] = kv.get("buyer_contact", "")
    data["buyer_tel"] = kv.get("buyer_tel", "")
    data["agent_name"] = kv.get("agent_name", "")
    data["agent_contact"] = kv.get("agent_contact", "")
    data["agent_tel"] = kv.get("agent_tel", "")
    data["bid_file_get_time"] = kv.get("bid_file_get_time", "")
    data["bid_file_get_place"] = kv.get("bid_file_get_place", "")
    data["bid_file_price"] = kv.get("bid_file_price", "")
    data["qualification_requirement"] = kv.get("qualification_requirement", "")
    data["procurement_policy"] = kv.get("procurement_policy", "")

    # ============ 联系方式章节（上下文感知） ============
    contact_info = extract_contact_section(content)
    log.info(f"[联系方式] 提取到: {list(contact_info.keys())}")
    for k, v in contact_info.items():
        if not data.get(k):
            data[k] = v

    # ============ 兜底：截止时间 ============
    if not data["bid_deadline"]:
        m = re.search(
            r"并于\s*(\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}[时点]\d{1,2}分?)"
            r"[^。；\n]{0,30}?前\s*(?:递交|提交|上传)",
            content
        )
        if m:
            data["bid_deadline"] = parse_datetime(m.group(1))

    # 兜底：招标人
    if not data["buyer_name"]:
        m = re.search(r"采购人(?:名称)?\s*[：:]\s*([^\n，,。]+)", content)
        if m:
            data["buyer_name"] = m.group(1).strip()

    # 兜底：代理机构
    if not data["agent_name"]:
        m = re.search(r"(?:采购)?代理机构(?:名称)?\s*[：:]\s*([^\n，,。]+)", content)
        if m:
            data["agent_name"] = m.group(1).strip()

    # 金额转换
    if data.get("budget"):
        data["budget"] = parse_amount(data["budget"])
    if data.get("price_ceiling"):
        data["price_ceiling"] = parse_amount(data["price_ceiling"])

    # 时间格式化
    for k in ("bid_deadline", "bid_open_time", "bid_file_get_time"):
        if data.get(k):
            data[k] = parse_datetime(data[k])

    # 项目编号兜底
    if not data["project_code"]:
        m = re.search(
            r"(?:采购项目编号|项目编号|招标编号|采购编号)[\s:：]*([A-Za-z0-9\-_]+)",
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

    # /a/ 补全编号
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


def parse_batch(urls, out_csv: str = "caigou.csv", sleep_range=(1.0, 2.0)):
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
        # 江西赣州（0201 询价）
        "https://www.ggzy.gov.cn/information/deal/html/b/500000/0201/20260910/00509a51d3cc47024d2a977fa0cea56d6d2e.html",
        # 四川遂宁（0201 竞争性磋商）
        "https://www.ggzy.gov.cn/information/deal/html/b/360000/0201/20260913/00369ea13c45fc554b898b4bb144b2db8575.html",
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