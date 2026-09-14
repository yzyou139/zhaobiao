# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 中标公告（0202）解析器 v5
- 优先走江西旧版模板（#jiangxi-gg-wrapper）
- 其他走通用多包 / 单包
- 联系方式章节：别名表驱动（兼容多省份写法）
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
    "win_supplier", "win_total_amount", "win_detail_json",
    "pack_count",
    "buyer_name", "buyer_address", "buyer_contact", "buyer_contact_name",
    "agent_name", "agent_address", "agent_contact", "agent_contact_name",
    "contact_name", "contact_tel",
    "judge_experts", "agent_service_fee", "publicity_period",
    "content_text", "attach_file_names", "attach_json",
]


# ========================= 行业分类 =========================
INDUSTRY_RULES = [
    ("工程建设", ["工程", "施工", "建筑", "装修", "改造", "修缮", "监理",
                 "市政", "道路", "桥梁", "管网", "电梯", "产业园", "厂房", "公路", "养护"]),
    ("医疗卫生", ["医疗", "医院", "卫生", "医药", "医学", "器械", "疾控", "保健",
                 "药品", "煎药", "中药", "结核"]),
    ("教育培训", ["学校", "大学", "学院", "教育", "培训", "教学", "体育", "课桌椅", "桌椅",
                 "运动会", "火炬"]),
    ("公共安全", ["消防", "公安", "警务", "安防", "应急", "救援", "司法"]),
    ("信息技术", ["软件", "信息化", "数字化", "智慧", "信息系统", "系统集成",
                 "计算机", "机房", "云计算", "大数据", "运维", "网络安全",
                 "平台建设", "平台开发", "管理系统", "电子化"]),
    ("市政交通", ["轨道", "公交", "停车", "路灯", "隧道"]),
    ("水利环保", ["水利", "水务", "供水", "排水", "污水", "环保", "生态", "垃圾"]),
    ("能源电力", ["电力", "电网", "光伏", "风电", "能源", "燃气", "配电"]),
    ("农林牧渔", ["农业", "农村", "林业", "畜牧", "渔业", "种植", "农田"]),
    ("文化旅游", ["文化", "旅游", "图书", "博物馆", "景区", "文物", "舞台", "音响", "灯光"]),
    ("服务外包", ["物业", "保洁", "保安", "绿化", "食堂", "餐饮", "劳务",
                 "外包", "维保", "租赁", "印刷", "审计", "咨询"]),
    ("科研检测", ["实验室", "科研", "检测", "试验", "认证", "计量", "特种设备"]),
    ("设备购置", ["设备采购", "设备更新", "设备购置", "仪器采购", "车辆采购", "家具采购"]),
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
    "沧州": ("河北省", "沧州市"), "石家庄": ("河北省", "石家庄市"),
    "海口": ("海南省", "海口市"), "三亚": ("海南省", "三亚市"),
    "海南": ("海南省", ""),
    "南京": ("江苏省", "南京市"), "苏州": ("江苏省", "苏州市"),
    "无锡": ("江苏省", "无锡市"), "江苏": ("江苏省", ""),
    "赣州": ("江西省", "赣州市"), "南昌": ("江西省", "南昌市"),
    "萍乡": ("江西省", "萍乡市"), "抚州": ("江西省", "抚州市"),
    "遂宁": ("四川省", "遂宁市"), "成都": ("四川省", "成都市"),
    "重庆": ("重庆市", ""), "青岛": ("山东省", "青岛市"),
    "武汉": ("湖北省", "武汉市"), "西安": ("陕西省", "西安市"),
    "北京": ("北京市", ""), "上海": ("上海市", ""), "天津": ("天津市", ""),
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


def parse_amount_yuan_to_wan(raw: str) -> Optional[float]:
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
        r"(?:采购项目编号|招标项目编号|项目编号|招标编号|采购编号|标段编号)"
        r"[\s:：]*([A-Za-z0-9\-_\[\]]+)",
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
        '//div[@id="jiangxi-gg-wrapper"]',
        '//div[contains(@class, "detail_content")]',
        '//div[@id="mycontent"]',
    ]:
        nodes = tree.xpath(selector)
        if not nodes:
            continue
        root = nodes[0]
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


# ========================= 江西旧版模板 =========================
def find_section_node(tree, section_title: str):
    xp = (f'//div[normalize-space(.)="{section_title}"]'
          f' | //p[normalize-space(.)="{section_title}"]')
    nodes = tree.xpath(xp)
    return nodes[0] if nodes else None


def collect_after(node, stops: List[str]):
    if node is None:
        return []
    out = []
    for sib in node.itersiblings():
        t = extract_text(sib)
        if not t:
            continue
        if any(s in t for s in stops):
            break
        out.append(t)
    return out


def parse_jiangxi_template(tree) -> Dict[str, Any]:
    """江西旧版模板（#jiangxi-gg-wrapper）"""
    result = {"_win_details": []}

    n = find_section_node(tree, "一、项目编号")
    vals = collect_after(n, ["二、项目名称"])
    if vals:
        result["project_code"] = vals[0].strip()

    n3 = find_section_node(tree, "三、中标（成交）信息")
    if n3 is None:
        n3 = find_section_node(tree, "三、中标(成交)信息")
    if n3 is not None:
        for line in collect_after(n3, ["四、主要标的信息", "四、主要标的"]):
            if line.startswith("供应商名称"):
                result["win_supplier"] = re.sub(r"^供应商名\s*称[：:]\s*", "", line).strip()
            elif line.startswith("供应商联系人"):
                result["win_supplier_contact"] = re.sub(r"^供应商联系人[：:]\s*", "", line).strip()
            elif line.startswith("供应商联系电话"):
                result["win_supplier_tel"] = re.sub(r"^供应商联系电话[：:]\s*", "", line).strip()
            elif line.startswith("供应商地址"):
                result["win_supplier_address"] = re.sub(r"^供应商地址[：:]\s*", "", line).strip()
            elif "中标（成交）金额" in line or "中标(成交)金额" in line:
                m = re.search(r"[：:]\s*([\d,]+\.?\d*)", line)
                if m:
                    result["win_total_amount_raw"] = m.group(1)

    n5 = find_section_node(tree, "五、评审专家名单")
    vals = collect_after(n5, ["六、代理服务收费标准及金额"])
    if vals:
        result["judge_experts"] = vals[0].strip()

    n6 = find_section_node(tree, "六、代理服务收费标准及金额")
    vals = collect_after(n6, ["七、公告期限"])
    if vals:
        m = re.search(r"([\d,]+\.?\d*)\s*元", vals[0])
        if m:
            result["agent_service_fee_raw"] = m.group(1)
    if "agent_service_fee_raw" not in result:
        wrapper = tree.xpath('//div[@id="jiangxi-gg-wrapper"]')
        if wrapper:
            full = "".join(wrapper[0].xpath(".//text()"))
            m = re.search(r"本项目代理费用金额为([\d,]+\.?\d*)\s*元", full)
            if m:
                result["agent_service_fee_raw"] = m.group(1)

    n7 = find_section_node(tree, "七、公告期限")
    vals = collect_after(n7, ["八、其他补充事宜"])
    if vals:
        result["publicity_period"] = vals[0].strip()

    # 联系方式（旧版直接用文本行的方式）
    n9 = find_section_node(tree, "九、凡对本次公告内容提出询问，请按以下方式联系")
    if n9 is None:
        nodes = tree.xpath('//div[contains(normalize-space(.), "凡对本次公告内容提出询问")]')
        if nodes:
            n9 = nodes[0]
    if n9 is None:
        nodes = tree.xpath('//div[contains(normalize-space(.), "凡对本次采购提出询问")]')
        if nodes:
            n9 = nodes[0]

    if n9 is not None:
        context = None
        for sib in n9.itersiblings():
            t = extract_text(sib)
            if not t:
                continue
            if re.match(r"^1[.、]\s*采购人信息", t):
                context = "buyer"; continue
            if re.match(r"^2[.、]\s*(?:采购)?代理机构信息", t):
                context = "agent"; continue
            if re.match(r"^3[.、]\s*项目联系方式", t):
                context = "contact"; continue
            if context == "buyer":
                if t.startswith("名称："): result["buyer_name"] = t[3:].strip()
                elif t.startswith("地址："): result["buyer_address"] = t[3:].strip()
                elif t.startswith("联系方式："): result["buyer_contact"] = t[5:].strip()
            elif context == "agent":
                if t.startswith("名称："): result["agent_name"] = t[3:].strip()
                elif t.startswith("地址："): result["agent_address"] = t[3:].strip()
                elif t.startswith("联系方式："): result["agent_contact"] = t[5:].strip()
            elif context == "contact":
                if t.startswith("项目联系人："): result["contact_name"] = t[6:].strip()
                elif t.startswith("电话："): result["contact_tel"] = t[3:].strip()

    # 主要标的信息表
    details = []
    for table in tree.xpath('//div[@id="jiangxi-gg-wrapper"]//table'):
        rows = table.xpath('.//tr')
        if len(rows) < 2:
            continue
        headers = [extract_text(td) for td in rows[0].xpath('./td|./th')]
        if "名称" not in headers:
            continue
        if not any(h in headers for h in (
            "服务范围", "服务要求", "服务标准", "服务时间",
            "施工范围", "施工工期", "项目经理", "规格型号",
        )):
            continue
        for tr in rows[1:]:
            cells = [extract_text(td) for td in tr.xpath('./td|./th')]
            if not any(cells):
                continue
            row = {}
            for i, c in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                if c:
                    row[key] = c
            if row:
                details.append(row)
        if details:
            break
    result["_win_details"] = details

    return result


# ========================= 通用单包 / 多包解析 =========================
VALUE_STOP = r"(?=\s*[一二三四五六七八九十]+、|\s*$|\n)"


def _clean_value(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    m = re.search(r"[一二三四五六七八九十]+、", raw)
    if m:
        raw = raw[:m.start()]
    raw = re.sub(r"[\s，,、；;。]+$", "", raw)
    return raw.strip()


HEADER_MAP = {
    "序号": "_seq",
    "供应商名称": "supplier",
    "中标供应商名称": "supplier",
    "成交供应商名称": "supplier",
    "中标单位名称": "supplier",
    "供应商地址": "supplier_address",
    "中标供应商地址": "supplier_address",
    "成交供应商地址": "supplier_address",
    "社会信用代码": "credit_code",
    "评审总得分": "score",
    "中标/成交金额": "amount",
    "中标（成交）金额": "amount",
    "中标(成交)金额": "amount",
    "中标金额": "amount",
    "成交金额": "amount",
}


def _parse_single_pack(pack_no: str, segment: str) -> Optional[Dict[str, Any]]:
    info = {"pack_no": pack_no}
    lines = [l.strip() for l in segment.split("\n") if l.strip()]

    # 方式A：同行 kv
    for line in lines:
        m = re.match(
            r"^(供应商名称|中标供应商名称|成交供应商名称|中标单位名称|"
            r"供应商地址|中标供应商地址|成交供应商地址|"
            r"中标（成交）金额|中标\(成交\)金额|中标/成交金额|"
            r"中标金额|成交金额|评审总得分|社会信用代码)"
            r"\s*[：:]\s*(.+)$",
            line
        )
        if m:
            std = HEADER_MAP.get(m.group(1))
            if std:
                info[std] = _clean_value(m.group(2))

    # 方式B：表头顺序 + 数据顺序配对
    if not info.get("supplier"):
        header_keys = []
        data_values = []
        in_data = False
        for line in lines:
            if line in HEADER_MAP and not in_data:
                header_keys.append(line)
                continue
            if header_keys:
                in_data = True
            if in_data:
                if re.match(r"^[一二三四五六七八九十]+、", line):
                    break
                data_values.append(line)

        if header_keys and data_values:
            for k, v in zip(header_keys, data_values):
                std = HEADER_MAP.get(k)
                if std and std not in info:
                    info[std] = _clean_value(v)

    if info.get("amount"):
        info["amount"] = parse_amount_yuan_to_wan(info["amount"])

    return info if len(info) > 1 else None


def parse_multi_pack(content: str) -> Dict[str, Any]:
    result = {"packs": [], "suppliers": [], "total_amount": None}

    pack_pattern = re.compile(
        r"(?:采购包|合同包|包号|包组)\s*[（(]?\s*(\d+)\s*[）)]?\s*[：:、.]?\s*",
        re.UNICODE
    )
    positions = [(m.start(), m.end(), m.group(1)) for m in pack_pattern.finditer(content)]
    positions = list({p[0]: p for p in positions}.values())
    positions.sort()

    if not positions:
        return result

    for i, (start, end, pack_no) in enumerate(positions):
        next_start = positions[i + 1][0] if i + 1 < len(positions) else len(content)
        segment = content[end:next_start]
        pack_info = _parse_single_pack(pack_no, segment)
        if pack_info:
            result["packs"].append(pack_info)
            if pack_info.get("supplier"):
                result["suppliers"].append(pack_info["supplier"])

    amounts = [p.get("amount") for p in result["packs"] if p.get("amount") is not None]
    if amounts:
        result["total_amount"] = round(sum(amounts), 6)

    return result


def parse_generic_single_pack(content: str) -> Dict[str, str]:
    result = {}

    for pat in [
        r"中标供应商名称\s*[：:]\s*([^\n，,。；]+?)" + VALUE_STOP,
        r"成交供应商名称\s*[：:]\s*([^\n，,。；]+?)" + VALUE_STOP,
        r"中标单位名称\s*[：:]\s*([^\n，,。；]+?)" + VALUE_STOP,
        r"(?:中标|成交)(?:供应商|单位|人)(?:名称)?[：:]\s*([^\n，,。；]+?)" + VALUE_STOP,
        r"供应商名称\s*[：:]\s*([^\n，,。；]+?)" + VALUE_STOP,
    ]:
        m = re.search(pat, content)
        if m:
            v = _clean_value(m.group(1))
            if v:
                result["win_supplier"] = v
                break

    for pat in [
        r"中标金额\s*[：:]\s*([\d,]+\.?\d*)",
        r"成交金额\s*[：:]\s*([\d,]+\.?\d*)",
        r"中标（成交）金额\s*[（(]?[^）)\n]*[）)]?[：:]\s*([\d,]+\.?\d*)",
        r"中标\(成交\)金额\s*[（(]?[^）)\n]*[）)]?[：:]\s*([\d,]+\.?\d*)",
    ]:
        m = re.search(pat, content)
        if m:
            result["win_total_amount_raw"] = m.group(1)
            break

    m = re.search(
        r"(?:中标|成交)?供应商地址\s*[：:]\s*([^\n，,。；]+?)" + VALUE_STOP,
        content
    )
    if m:
        result["win_supplier_address"] = _clean_value(m.group(1))

    return result


# ========================= 表格兜底 =========================
def parse_win_details(tree) -> List[Dict[str, str]]:
    details = []
    for table in tree.xpath('//div[@id="jiangxi-gg-wrapper"]//table'
                            ' | //div[contains(@class,"detail_content")]//table'
                            ' | //div[@id="mycontent"]//table'):
        rows = table.xpath('.//tr')
        if len(rows) < 2:
            continue
        headers = [extract_text(td) for td in rows[0].xpath('./td|./th')]
        header_join = " ".join(headers)
        if not any(k in header_join for k in ("供应商名称", "中标", "成交", "采购包", "标的名称")):
            continue
        for tr in rows[1:]:
            cells = [extract_text(td) for td in tr.xpath('./td|./th')]
            if not any(cells):
                continue
            row = {}
            for i, c in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                if c:
                    row[key] = c
            if row:
                details.append(row)
        if details:
            break
    return details


# ========================= 联系方式：别名表驱动 =========================
CONTACT_FIELD_ALIASES: Dict[str, List[str]] = {
    "name": [
        "名称", "采购人名称", "采购单位名称", "招标人名称", "建设单位名称",
        "代理机构名称", "采购代理机构名称", "采购机构名称", "招标代理机构名称",
    ],
    "address": [
        "地址", "采购人地址", "采购单位地址", "招标人地址", "建设单位地址",
        "代理机构地址", "采购代理机构地址", "采购机构地址", "招标代理机构地址",
    ],
    "tel": [
        "联系方式", "联系电话", "电话",
        "采购人电话", "采购单位电话", "招标人电话", "招标人联系电话",
        "代理机构电话", "采购机构电话", "代理机构联系电话",
    ],
    "contact_name": [
        "联系人", "经办人",
        "采购人联系人", "采购单位联系人", "采购经办人",
        "代理机构联系人", "代理机构经办人", "招标代理联系人", "招标代理经办人",
    ],
    "project_contact_name": [
        "项目联系人", "项目负责人",
    ],
    "project_contact_tel": [
        "项目联系人电话", "项目联系电话",
    ],
}


SECTION_MARKERS: Dict[str, List[str]] = {
    "buyer": [
        "采购人信息", "采购单位信息", "招标人信息", "建设单位信息",
        "采购人", "采购单位", "招标人", "建设单位",
    ],
    "agent": [
        "采购代理机构信息", "代理机构信息", "采购机构信息", "招标代理机构信息",
        "采购代理机构", "代理机构", "采购机构", "招标代理机构",
    ],
    "contact": [
        "项目联系方式", "项目联系信息",
    ],
}


CONTEXT_FIELD_MAP: Dict[str, Dict[str, str]] = {
    "buyer": {
        "name": "buyer_name",
        "address": "buyer_address",
        "tel": "buyer_contact",
        "contact_name": "buyer_contact_name",
    },
    "agent": {
        "name": "agent_name",
        "address": "agent_address",
        "tel": "agent_contact",
        "contact_name": "agent_contact_name",
    },
    "contact": {
        "project_contact_name": "contact_name",
        "contact_name": "contact_name",
        "tel": "contact_tel",
        "project_contact_tel": "contact_tel",
    },
}


def _match_section(line: str) -> Optional[str]:
    """匹配章节标题（去掉数字序号后比较）"""
    line_clean = re.sub(r"^\d+\s*[.、]\s*", "", line).strip()
    for context, markers in SECTION_MARKERS.items():
        for marker in markers:
            if line_clean == marker or line_clean.startswith(marker):
                return context
    return None


def _match_contact_field(key: str) -> Optional[str]:
    """匹配字段类型（长别名优先）"""
    all_aliases = []
    for field_type, aliases in CONTACT_FIELD_ALIASES.items():
        for alias in aliases:
            all_aliases.append((len(alias), field_type, alias))
    all_aliases.sort(reverse=True)

    for _, field_type, alias in all_aliases:
        if key == alias:
            return field_type
    for _, field_type, alias in all_aliases:
        if alias in key:
            return field_type
    return None


def extract_contact_section(content: str, result: Dict[str, str]):
    """通用的联系方式章节解析（别名表驱动）"""

    # 预处理1：在"凡对本次...提出询问"标题前后加换行
    content_norm = re.sub(
        r"(?<!^)((?:[一二三四五六七八九十]+、)?\s*凡对本次(?:采购|招标|公告内容)?"
        r"提出询问[，,]\s*请按以下方式联系。?)",
        r"\n\1\n", content)

    # 预处理2：在所有"数字.xxx" / "数字、xxx" 前加换行
    content_norm = re.sub(r"(?<!^)(\d)\s*([.、])\s*(?=\S)", r"\n\1\2", content_norm)

    # 预处理3：在"凡对本次..."后加换行
    content_norm = re.sub(
        r"(凡对本次[^\n]*联系[。.]?)(?=\S)", r"\1\n", content_norm)

    lines = [l.strip() for l in content_norm.split("\n")]
    context = None
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if not line:
            i += 1
            continue

        sec = _match_section(line)
        if sec:
            context = sec
            i += 1
            continue

        if re.match(r"^(?:[一二三四五六七八九十]+、)\s*凡对本次", line):
            context = None
            i += 1
            continue

        if context is None:
            i += 1
            continue

        m = re.match(r"^([^：:]{1,30})\s*[：:]\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key = m.group(1).strip()
        val = m.group(2).strip()

        field_type = _match_contact_field(key)
        if not field_type:
            i += 1
            continue

        if not val and i + 1 < n:
            nxt = lines[i + 1].strip()
            if nxt and not re.match(r"^[^：:]{1,30}\s*[：:]", nxt) \
                    and not re.match(r"^\d\s*[.、]", nxt):
                val = nxt
                i += 1

        if "tel" in field_type:
            m2 = re.search(r"(\d{3,4}[-—]?\d{7,8}|\d{7,12})", val)
            if m2:
                val = m2.group(1)
            else:
                i += 1
                continue

        if not val:
            i += 1
            continue

        target = CONTEXT_FIELD_MAP.get(context, {}).get(field_type)
        if target and target not in result:
            if field_type in ("contact_name", "project_contact_name"):
                cleaned = re.sub(
                    r"(项目负责人|负责人|经办人|联系人|技术审核|项目联系人|"
                    r"代理机构|采购人|采购单位|采购机构|招标人)", "", val)
                cleaned = re.sub(r"\d+\s*[.、]", " ", cleaned)
                m3 = re.search(r"([\u4e00-\u9fa5]{2,6})", cleaned)
                if m3:
                    result[target] = m3.group(1)
            else:
                result[target] = val

        i += 1


# ========================= 主解析 =========================
def parse_notice(tree, url: str) -> Dict[str, Any]:
    data = {k: "" for k in FIELDS}
    data["notice_url"] = normalize_url(url)
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["notice_type"] = "中标公告"

    t = tree.xpath('//h4[@class="h4_o"]/text()')
    data["notice_title"] = t[0].strip() if t else ""

    pub_nodes = tree.xpath('//p[@class="p_o"]/span[contains(text(),"发布时间")]//text()')
    for s in pub_nodes:
        m = re.search(r"发布时间[：:]\s*(.+)", s)
        if m:
            data["publish_time"] = m.group(1).strip()
            break

    src = tree.xpath('//label[@id="platformName"]/text()')
    data["source"] = src[0].strip() if src else ""

    po_text = "".join(tree.xpath('//p[@class="p_o"]//text()'))
    m = re.search(
        r"(?:采购项目编号|招标项目编号|项目编号|招标编号|采购编号|标段编号)"
        r"[\s:：]*([A-Za-z0-9\-_\[\]]+)",
        po_text
    )
    if m:
        data["project_code"] = m.group(1).strip()

    content = extract_content_text(tree)
    data["content_text"] = content

    has_jiangxi = bool(tree.xpath('//div[@id="jiangxi-gg-wrapper"]'))
    if has_jiangxi:
        log.info("[模板] 江西旧版模板")
        kv = parse_jiangxi_template(tree)
    else:
        multi = parse_multi_pack(content)
        kv = {}
        if multi["packs"]:
            log.info(f"[模板] 多采购包（{len(multi['packs'])} 个）")
            kv["_multi"] = multi
        else:
            log.info("[模板] 通用单包")
            kv = parse_generic_single_pack(content)

    # 应用字段
    if kv.get("_multi"):
        multi = kv["_multi"]
        data["pack_count"] = len(multi["packs"])
        if multi["suppliers"]:
            data["win_supplier"] = "，".join(multi["suppliers"])
        if multi["total_amount"] is not None:
            data["win_total_amount"] = multi["total_amount"]
        data["win_detail_json"] = json.dumps(multi["packs"], ensure_ascii=False)
    else:
        data["pack_count"] = 0
        data["win_supplier"] = kv.get("win_supplier", "")
        if kv.get("win_total_amount_raw"):
            data["win_total_amount"] = parse_amount_yuan_to_wan(kv["win_total_amount_raw"])

    data["judge_experts"] = kv.get("judge_experts", "")
    data["publicity_period"] = kv.get("publicity_period", "")
    data["buyer_name"] = kv.get("buyer_name", "")
    data["buyer_address"] = kv.get("buyer_address", "")
    data["buyer_contact"] = kv.get("buyer_contact", "")
    data["buyer_contact_name"] = kv.get("buyer_contact_name", "")
    data["agent_name"] = kv.get("agent_name", "")
    data["agent_address"] = kv.get("agent_address", "")
    data["agent_contact"] = kv.get("agent_contact", "")
    data["agent_contact_name"] = kv.get("agent_contact_name", "")
    data["contact_name"] = kv.get("contact_name", "")
    data["contact_tel"] = kv.get("contact_tel", "")
    if kv.get("agent_service_fee_raw"):
        data["agent_service_fee"] = parse_amount_yuan_to_wan(kv["agent_service_fee_raw"])

    if not data["win_detail_json"] and kv.get("_win_details"):
        data["win_detail_json"] = json.dumps(kv["_win_details"], ensure_ascii=False)
    if not data["win_detail_json"]:
        details = parse_win_details(tree)
        if details:
            data["win_detail_json"] = json.dumps(details, ensure_ascii=False)

    # 非江西模板的兜底
    if not has_jiangxi:
        if not data["judge_experts"]:
            m = re.search(
                r"(?:评审专家|评标专家)(?:名单|（单一来源采购人员）名单)?"
                r"[：:]\s*([^\n]+)", content)
            if m:
                data["judge_experts"] = _clean_value(m.group(1))

        if not data["agent_service_fee"]:
            m = re.search(
                r"(?:代理服务费|代理费|招标代理服务费)[^\n\d]{0,10}([\d,]+\.?\d*)",
                content)
            if m:
                data["agent_service_fee"] = parse_amount_yuan_to_wan(m.group(1))

        if not data["publicity_period"]:
            m = re.search(r"(?:公告期限|公示期)[：:]?\s*([^\n]+)", content)
            if m:
                data["publicity_period"] = _clean_value(m.group(1))

        # 联系方式（别名表驱动）
        extract_contact_section(content, data)

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


def parse_batch(urls, out_csv: str = "zhongbiao.csv", sleep_range=(1.0, 2.0)):
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
        # 江西旧版模板
        "https://www.ggzy.gov.cn/information/deal/html/b/360000/0202/20260911/0036df0f76b820ff42508532314870c996d3.html",
        # 海南多包 + 采购单位/采购机构
        "https://www.ggzy.gov.cn/information/deal/html/b/460000/0202/20260910/00465af21d6122e449309854eef8e7a2879c.html",
        # 河北单包
        "https://www.ggzy.gov.cn/information/deal/html/b/130000/0202/20260910/00137566d2ecdae24f8da3033395c7adbccf.html",
        # 江苏多包
        "https://www.ggzy.gov.cn/information/deal/html/b/320000/0202/20260910/0032e1a6b16e0672464394729c3f0eb0e384.html",
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