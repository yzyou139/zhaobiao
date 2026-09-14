# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 招标/资审公告（0101）分层解析器
- 第 1 层：HTML 结构化（table、p_o）
- 第 2 层：正文按行解析
- 第 3 层：全文正则兜底（针对日期、金额）
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
    "budget", "budget_source",
    "bid_deadline", "open_bid_time", "bid_file_get_deadline",
    "purchase_method",
    "buyer_name", "buyer_address", "buyer_contact",
    "agent_name", "agent_address", "agent_contact",
    "contact_name", "contact_tel",
    "content_text",
    "attach_file_names", "attach_json",
]


# ========================= 字段别名 =========================
DIRECT_ALIASES: Dict[str, List[str]] = {
    "project_code": [
        "招标项目编号", "招标登记编号", "报建编号", "项目编号",
        "招标编号", "采购编号", "标段编号", "项目代码",
    ],
    "project_name": [
        "招标项目名称", "项目名称", "标段名称",
    ],
    "buyer_name": [
        "招标人", "采购人", "招标单位", "建设单位",
        "出让人", "出让单位",
    ],
    "agent_name": [
        "招标代理机构", "采购代理机构", "代理机构",
        "招标代理", "代理单位",
    ],
    "buyer_address": [
        "招标人地址", "招标人办公地址", "采购人地址",
        "招标单位地址", "建设单位地址", "出让人地址",
    ],
    "agent_address": [
        "招标代理机构地址", "代理机构地址", "采购代理机构地址",
    ],
    "buyer_contact_tel": [
        "招标人联系电话", "招标人办公电话", "招标人联系方式",
        "采购人联系电话", "采购人联系方式",
        "招标联系电话", "招标电话",
    ],
    "agent_contact_tel": [
        "招标代理机构联系电话", "招标代理机构办公电话",
        "招标代理机构联系方式", "采购代理机构联系电话",
        "代理机构联系电话", "代理机构办公电话", "代理机构联系方式",
        "招标代理联系电话", "招标代理电话",
    ],
    "buyer_contact_name": [
        "招标人联系人", "招标人经办人", "采购人联系人",
        "招标联系人",
    ],
    "agent_contact_name": [
        "代理机构联系人", "代理机构经办人", "代理机构项目负责人",
        "招标代理联系人", "招标代理经办人", "招标代理项目负责人",
        "招标代理机构联系人",
    ],
    "bid_deadline": [
        "投标文件递交截止时间", "投标文件递交的截止时间",
        "递交投标文件截止时间", "投标截止时间",
        "响应文件递交截止时间", "递交响应文件截止时间",
        "响应文件提交截止时间", "响应截止时间",
    ],
    "bid_file_get_deadline": [
        "招标文件获取截止时间", "招标文件领取截止时间",
        "获取招标文件截止时间", "招标文件下载截止时间",
    ],
    "open_bid_time": ["开标时间", "开标日期"],
    "budget": [
        "标段合同估算价", "招标控制价", "最高限价",
        "预算金额", "预算总金额", "采购预算",
        "本次招标金额", "招标金额", "合同估算价",
        "本次招标工程投资额", "招标工程投资额", "工程投资额",
        "本次招标投资额", "项目总投资", "总投资额","最高投标限价","本工程投资"
    ],
    "purchase_method": ["招标方式", "采购方式", "交易方式"],
}

GENERIC_ALIASES: Dict[str, List[str]] = {
    "address": ["地址", "办公地址"],
    "contact_name": ["经办人", "联系人", "项目联系人", "项目负责人"],
    "contact_tel": ["办公电话", "联系电话", "联系方式", "移动电话", "电话"],
}

CONTEXT_MARKERS = {
    "buyer": ["招标人", "采购人", "招标单位", "建设单位", "出让人", "出让单位"],
    "agent": ["招标代理机构", "采购代理机构", "代理机构", "招标代理", "代理单位"],
}

# 值尾部遇到这些词就切断
SECONDARY_KEYS = [
    "电子邮件", "电子邮箱", "传真", "邮编", "邮政编码",
    "QQ", "邮箱", "微信", "网址", "官网",
    "行政监督", "监督部门", "监管部门",
    "招标人法定代表人", "法定代表人",
    "报建名称", "报建编号", "招标登记编号",
    "备注", "注",
]


# ========================= 行业 / 行政区域 =========================
INDUSTRY_RULES = [
    ("医疗卫生", ["医疗", "医院", "卫生", "药", "医学", "器械", "疾控", "保健"]),
    ("教育培训", ["学校", "大学", "学院", "教育", "培训", "教学", "体育"]),
    ("公共安全", ["消防", "公安", "警务", "安防", "应急", "救援", "司法"]),
    ("信息技术", ["软件", "信息化", "数字化", "智慧", "信息系统", "系统集成",
                 "计算机", "机房", "云计算", "大数据", "运维", "网络安全"]),
    ("工程建设", ["工程", "施工", "建筑", "装修", "改造", "修缮", "监理",
                 "市政", "道路", "桥梁", "管网", "杆线", "EPC"]),
    ("市政交通", ["公路", "交通", "轨道", "公交", "停车", "路灯", "隧道"]),
    ("水利环保", ["水利", "水务", "供水", "排水", "污水", "环保", "生态", "垃圾"]),
    ("能源电力", ["电力", "电网", "光伏", "风电", "能源", "燃气", "配电"]),
    ("农林牧渔", ["农业", "农村", "林业", "畜牧", "渔业", "种植", "农田",
                 "高标准农田", "林麝", "养殖"]),
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


CITY_MAP = {
    "西安": ("陕西省", "西安市"), "铜川": ("陕西省", "铜川市"),
    "宝鸡": ("陕西省", "宝鸡市"), "咸阳": ("陕西省", "咸阳市"),
    "渭南": ("陕西省", "渭南市"), "延安": ("陕西省", "延安市"),
    "宜君": ("陕西省", "铜川市"),
    "萍乡": ("江西省", "萍乡市"), "南昌": ("江西省", "南昌市"),
    "赣州": ("江西省", "赣州市"), "九江": ("江西省", "九江市"),
    "抚州": ("江西省", "抚州市"), "宜春": ("江西省", "宜春市"),
    "成都": ("四川省", "成都市"), "绵阳": ("四川省", "绵阳市"),
    "广安": ("四川省", "广安市"), "岳池": ("四川省", "广安市"),
    "银川": ("宁夏", "银川市"), "吴忠": ("宁夏", "吴忠市"),
    "昆明": ("云南省", "昆明市"), "大理": ("云南省", "大理市"),
    "迪庆": ("云南省", "迪庆州"), "维西": ("云南省", "迪庆州"),
    "武汉": ("湖北省", "武汉市"), "宜昌": ("湖北省", "宜昌市"),
    "襄阳": ("湖北省", "襄阳市"),
    "重庆": ("重庆市", ""),
    "北京": ("北京市", ""),
    "上海": ("上海市", ""),
    "天津": ("天津市", ""),
}


def extract_admin_region(source: str, content: str) -> str:
    text = (source or "") + " " + (content or "")[:400]
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


def parse_amount_wan(raw: str) -> Optional[float]:
    if not raw:
        return None
    m = re.search(r"([\d,]+\.?\d*)", raw)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if "万元" in raw or "万" in raw:
        return round(v, 6)
    return round(v / 10000, 6)


def parse_datetime(raw: str) -> str:
    if not raw:
        return ""
    m = re.search(
        r"(\d{4})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})\s*日?\s*"
        r"(?:(\d{1,2})\s*[时:：点]\s*(\d{1,2})\s*分?)?",
        raw
    )
    if not m:
        return raw.strip()
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    h = int(m.group(4)) if m.group(4) else 0
    mi = int(m.group(5)) if m.group(5) else 0
    return f"{y}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"


def clean_value(raw: str) -> str:
    """值清理：遇次级字段停、去首尾、限长"""
    if not raw:
        return ""
    for kw in SECONDARY_KEYS:
        m = re.search(r"\s+" + re.escape(kw), raw)
        if m:
            raw = raw[:m.start()]
    m = re.search(r"\s*[一二三四五六七八九十]{1,3}\s*[、.]", raw)
    if m:
        raw = raw[:m.start()]
    raw = re.sub(r"^[\s：:，,。;；、]+", "", raw)
    raw = re.sub(r"[\s，,、；;。]+$", "", raw)
    raw = re.sub(r"\s*\n\s*", " ", raw)
    return raw[:200].strip()


def clean_key(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"^[\s：:]+", "", s)
    s = re.sub(r"[\s：:]+$", "", s)
    s = re.sub(r"[（(][^）)]*[）)]\s*$", "", s)
    return s.strip()


def match_direct_field(key: str) -> Optional[str]:
    key = clean_key(key)
    if not key:
        return None
    for field, aliases in DIRECT_ALIASES.items():
        for a in aliases:
            if a == key:
                return field
    candidates = []
    for field, aliases in DIRECT_ALIASES.items():
        for a in aliases:
            if a in key:
                candidates.append((len(a), field))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return None


def match_generic_field(key: str) -> Optional[str]:
    key = clean_key(key)
    if not key:
        return None
    for field, aliases in GENERIC_ALIASES.items():
        for a in aliases:
            if a == key:
                return field
    return None


def detect_context(line: str) -> Optional[str]:
    if not line:
        return None
    head = line[:20]
    for kw in CONTEXT_MARKERS["agent"]:
        if kw in head:
            return "agent"
    for kw in CONTEXT_MARKERS["buyer"]:
        if kw in head:
            return "buyer"
    return None


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
        r"(?:招标项目编号|项目编号|招标编号|采购编号|标段编号)[\s:：]*([A-Za-z0-9\-_]{6,})",
        po_text
    )
    if m:
        result["project_code"] = m.group(1).strip()
    src = tree.xpath('//label[@id="platformName"]/text()')
    if src:
        result["source"] = src[0].strip()
    return result


# ========================= 正文文本 =========================
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


# ========================= 第 1 层：HTML 结构化 =========================
def _apply_kv(result, key_raw, value_raw, context):
    """把 (key, value) 对写入 result，带上下文处理"""
    key = clean_key(key_raw)
    if not key:
        return
    field = match_direct_field(key)
    if not field:
        gfield = match_generic_field(key)
        if gfield and context:
            field = f"{context}_{gfield}"
    if not field:
        return
    if field in result:
        return
    # 从 key 里补单位（如"（万元）"）
    if "万元" in key_raw and "万元" not in value_raw:
        value_raw = value_raw + " 万元"
    val = clean_value(value_raw)
    if val and val not in ("/", "无", "—", "——"):
        result[field] = val


def extract_from_html_tables(tree) -> Dict[str, str]:
    """
    从 HTML table 里提取字段。
    - 2 列 tr：td1=key, td2=value
    - 4 列 tr：td1=key, td2=val, td3=key, td4=val
    - 上下文切换：遇到"招标代理机构"等切换归属
    """
    result: Dict[str, str] = {}

    for root_sel in [
        '//div[contains(@class, "detail_content")]',
        '//div[@id="mycontent"]',
    ]:
        roots = tree.xpath(root_sel)
        if not roots:
            continue
        root = roots[0]
        context: Optional[str] = None

        for table in root.xpath('.//table'):
            for tr in table.xpath('./tbody/tr | ./tr'):
                cells = tr.xpath('./td|./th')
                if len(cells) < 2:
                    continue

                # 行内单元格文本
                cell_texts = [extract_text(td) for td in cells]

                # 上下文切换：只看第一个 cell
                for t in cell_texts[:1]:
                    tk = clean_key(t)
                    for kw in CONTEXT_MARKERS["agent"]:
                        if tk == kw:
                            context = "agent"
                    for kw in CONTEXT_MARKERS["buyer"]:
                        if tk == kw:
                            context = "buyer"

                # 2 列
                if len(cells) == 2:
                    _apply_kv(result, cell_texts[0], cell_texts[1], context)
                # 4 列
                elif len(cells) == 4:
                    _apply_kv(result, cell_texts[0], cell_texts[1], context)
                    _apply_kv(result, cell_texts[2], cell_texts[3], context)
                # 其他列数：交替 kv
                else:
                    for i in range(0, len(cells) - 1, 2):
                        _apply_kv(result, cell_texts[i], cell_texts[i + 1], context)
        break

    return result


# ========================= 第 2 层：正文按行解析 =========================
def extract_from_text_lines(content: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    lines = [l.rstrip() for l in content.split("\n")]
    context: Optional[str] = None

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # 去行首章节号
        line_clean = re.sub(r"^[一二三四五六七八九十]{1,3}\s*[、.]\s*", "", line)
        line_clean = re.sub(r"^\d{1,3}\s*[、.]\s*", "", line_clean)

        ctx = detect_context(line_clean)
        if ctx:
            context = ctx

        # 匹配 "key:value" 或 "key:"
        m = re.match(r"^([^：:，,。；;\n]{1,30}?)\s*[：:]\s*(.*)$", line_clean)
        if not m:
            i += 1
            continue

        key_raw = m.group(1).strip()
        value_raw = m.group(2).strip()

        field = match_direct_field(key_raw)
        if not field:
            gfield = match_generic_field(key_raw)
            if gfield and context:
                field = f"{context}_{gfield}"

        if not field or field in result:
            i += 1
            continue

        if value_raw:
            # 从 key 继承单位
            if "万元" in key_raw and "万元" not in value_raw:
                value_raw = value_raw + " 万元"
            val = clean_value(value_raw)
            if val and val not in ("/", "无", "—", "——"):
                result[field] = val
        else:
            # 值在下一行
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt and not re.match(r"^[^：:]{1,30}\s*[：:]", nxt):
                    nxt_clean = re.sub(r"^[一二三四五六七八九十]{1,3}\s*[、.]\s*", "", nxt)
                    if "万元" in key_raw and "万元" not in nxt_clean:
                        nxt_clean = nxt_clean + " 万元"
                    val = clean_value(nxt_clean)
                    if val and val not in ("/", "无", "—", "——"):
                        result[field] = val
                        i += 1
        i += 1

    return result


# ========================= 第 3 层：全文正则兜底 =========================
def extract_from_regex(content: str) -> Dict[str, str]:
    """
    针对有强模式的字段，全文正则兜底。
    只处理：日期类（跨行）、金额类
    """
    result: Dict[str, str] = {}

    # 投标截止时间
    for pat in [
        r"投标文件(?:递交|提交)的?截止时间[^\n]{0,40}?为\s*"
        r"(\d{4}\s*[-年/]\s*\d{1,2}\s*[-月/]\s*\d{1,2}\s*日?\s*\d{1,2}\s*[时:：]\s*\d{1,2}\s*分?)",
        r"(?:投标|响应)截止时间[^\n]{0,20}?[为：:]\s*"
        r"(\d{4}\s*[-年/]\s*\d{1,2}\s*[-月/]\s*\d{1,2}\s*日?\s*\d{1,2}\s*[时:：]\s*\d{1,2}\s*分?)",
    ]:
        m = re.search(pat, content)
        if m:
            result["bid_deadline"] = m.group(1)
            break

    # 招标文件获取截止时间
    m = re.search(
        r"(?:请于|获取时间|下载时间)[^\n]{0,30}?"
        r"(\d{4}\s*[-年/]\s*\d{1,2}\s*[-月/]\s*\d{1,2}\s*日?\s*\d{1,2}\s*[时:：]\s*\d{1,2}\s*分?)"
        r"[^\n]{0,30}?(?:起)?至"
        r"(\d{4}\s*[-年/]\s*\d{1,2}\s*[-月/]\s*\d{1,2}\s*日?\s*\d{1,2}\s*[时:：]\s*\d{1,2}\s*分?)",
        content
    )
    if m:
        result["bid_file_get_deadline"] = m.group(2)

    # 金额：本次招标金额 XX 万元
    m = re.search(
        r"(?:本次招标金额|标段合同估算价|招标控制价|最高限价|预算金额|"
        r"本次招标工程投资额|招标工程投资额|工程投资额|项目总投资)"
        r"\s*[：: ]?\s*([\d,]+\.?\d*)\s*万元",
        content
    )
    if m:
        result["budget"] = m.group(0)

    return result


# ========================= 主解析 =========================
def parse_notice(tree, url: str) -> Dict[str, Any]:
    data = {k: "" for k in FIELDS}
    data["notice_url"] = normalize_url(url)
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["notice_type"] = "招标/资审公告"

    # 标题
    t = tree.xpath('//h4[@class="h4_o"]/text()')
    data["notice_title"] = t[0].strip() if t else ""

    # 发布时间
    pub_nodes = tree.xpath(
        '//p[@class="p_o"]/span[contains(text(),"发布时间")]//text()'
    )
    for s in pub_nodes:
        m = re.search(r"发布时间[：:]\s*(.+)", s)
        if m:
            data["publish_time"] = m.group(1).strip()
            break

    # 来源
    src = tree.xpath('//label[@id="platformName"]/text()')
    data["source"] = src[0].strip() if src else ""

    # 项目编号：p_o 优先
    po_text = "".join(tree.xpath('//p[@class="p_o"]//text()'))
    m = re.search(
        r"(?:招标项目编号|项目编号|招标编号|采购编号|标段编号)[\s:：]*([A-Za-z0-9\-_]{6,})",
        po_text
    )
    if m:
        data["project_code"] = m.group(1).strip()

    # 正文
    content = extract_content_text(tree)
    data["content_text"] = content

    # ============ 第 1 层：HTML 结构化 ============
    html_fields = extract_from_html_tables(tree)
    log.debug(f"[层1-HTML表格] {list(html_fields.keys())}")

    # ============ 第 2 层：正文按行 ============
    text_fields = extract_from_text_lines(content)
    log.debug(f"[层2-按行解析] {list(text_fields.keys())}")

    # ============ 第 3 层：全文正则 ============
    regex_fields = extract_from_regex(content)
    log.debug(f"[层3-全文正则] {list(regex_fields.keys())}")

    # ============ 合并（表格 > 按行 > 正则） ============
    source_map = {}  # 记录每字段来源
    merged: Dict[str, str] = {}
    for fields, layer in [
        (regex_fields, "regex"),
        (text_fields, "text"),
        (html_fields, "html"),
    ]:
        for k, v in fields.items():
            merged[k] = v
            source_map[k] = layer

    # 映射到标准字段
    if merged.get("buyer_contact_tel"):
        merged["buyer_contact"] = merged["buyer_contact_tel"]
    elif merged.get("buyer_contact_name"):
        merged["buyer_contact"] = merged["buyer_contact_name"]

    if merged.get("agent_contact_tel"):
        merged["agent_contact"] = merged["agent_contact_tel"]

    if merged.get("agent_contact_name"):
        merged["contact_name"] = merged["agent_contact_name"]
        merged["contact_tel"] = merged.get("agent_contact_tel", "")
    elif merged.get("buyer_contact_name"):
        merged["contact_name"] = merged["buyer_contact_name"]
        merged["contact_tel"] = merged.get("buyer_contact_tel", "")

    if not merged.get("contact_tel") and merged.get("agent_contact_tel"):
        merged["contact_tel"] = merged["agent_contact_tel"]

    # 应用到 data（不覆盖已从 p_o 提取的 project_code）
    for k, v in merged.items():
        if k == "project_code":
            if not data["project_code"]:
                data["project_code"] = v
        elif k in data and not data[k]:
            data[k] = v

    # 金额单位转换
    if data.get("budget"):
        data["budget"] = parse_amount_wan(data["budget"])
        data["budget_source"] = "content" if data["budget"] else "default"
    else:
        data["budget_source"] = "default"

    # 时间归一化
    for k in ("bid_deadline", "bid_file_get_deadline", "open_bid_time"):
        if data.get(k):
            data[k] = parse_datetime(data[k])

    # 采购方式兜底
    if not data.get("purchase_method"):
        for kw in ("公开招标", "邀请招标", "竞争性磋商", "竞争性谈判", "询价"):
            if kw in content:
                data["purchase_method"] = kw
                break

    # 行业 / 行政区域
    data["industry"] = classify_industry(data["notice_title"], content)
    data["admin_region"] = extract_admin_region(data["source"], content)

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
            log.info(f"[层1-/a/补全] 项目编号: {data['project_code']}")
        if not data["source"] and a_meta.get("source"):
            data["source"] = a_meta["source"]

    # 打印每字段来源
    log.info(f"字段来源: {source_map}")

    return data


# ========================= 单条 / 批量 =========================
def parse_one(url: str) -> Dict[str, Any]:
    tree = fetch_detail_tree(url)
    if tree is None:
        return {}
    return parse_notice(tree, url)


def parse_batch(urls, out_csv: str = "zishen.csv", sleep_range=(1.0, 2.0)):
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
        "https://www.ggzy.gov.cn/information/deal/html/b/610000/0101/20260910/00611366d5a101d14f69bc49bfcafb8707b9.html",
        "https://www.ggzy.gov.cn/information/deal/html/b/530000/0101/20260910/0053108d6bd0fa394724a88cd544a8d03cbe.html",
        "https://www.ggzy.gov.cn/information/deal/html/b/420000/0101/20260910/0042f46caf3924b741adb59d08969f11bb66.html",
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
            if isinstance(v, str) and len(v) > 250:
                v = v[:250] + "..."
            print(f"{k}: {v}")