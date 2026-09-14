# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 结果公示解析器（通用版）
核心：文本 + 别名表 + 单位感知
- 不依赖具体 HTML 结构
- 字段别名覆盖各省常见写法
- 金额按原文单位（万元/元）自动判断
"""
import re
import json
import datetime
from typing import Optional, Dict, Any, List, Tuple

from curl_cffi import requests
from lxml import etree


# ========================= 字段顺序 =========================
FIELDS = [
    "notice_title", "notice_type", "project_code", "publish_time",
    "notice_url", "source", "crawl_time", "industry", "admin_region",
    "budget", "budget_source", "bid_deadline", "open_bid_time",
    "purchase_method", "content_text",
    "buyer_name", "buyer_address", "buyer_contact",
    "agent_name", "agent_address", "agent_contact",
    "contact_name", "contact_tel",
    "win_supplier", "win_total_amount", "win_detail_json",
    "judge_experts", "agent_service_fee",
    "change_item", "terminate_reason",
    "proposed_supplier", "single_source_reason", "publicity_period",
    "attach_file_names", "attach_json",
]


# ========================= 字段别名表 =========================
FIELD_ALIASES: Dict[str, List[str]] = {
    "project_code": [
        "项目编号", "招标编号", "采购编号", "项目代码",
        "询价书编号", "磋商文件编号", "谈判文件编号",
    ],
    "win_supplier": [
        "供应商名称", "成交供应商", "中标供应商",
        "成交单位", "中标单位", "中标人", "成交人",
        "受让单位", "竞得人", "竞得单位", "受让方",
    ],
    "win_total_amount": [
        "中标（成交）金额", "中标(成交)金额",
        "中标金额", "成交金额", "中标价", "成交价",
        "总中标金额", "总成交金额", "成交总价",
    ],
    "judge_experts": [
        "评审专家名单", "评审专家", "评标专家",
        "磋商小组成员", "谈判小组成员", "评标委员会成员",
        "磋商小组名单",
    ],
    "agent_service_fee": [
        "代理服务收费标准及金额", "代理服务费",
        "招标代理服务费", "采购代理服务费", "中标服务费",
        "本项目代理费", "本项目代理服务费",
    ],
    "publicity_period": [
        "公告期限", "公示期限", "公示期",
    ],
    "buyer_name": [
        "采购人名称", "采购单位名称", "采购人", "采购单位",
        "招标人", "出让人", "出让单位",
    ],
    "buyer_address": [
        "采购人地址", "采购单位地址", "招标人地址",
        "出让人地址", "地址",
    ],
    "buyer_contact": [
        "采购人联系方式", "采购单位联系方式", "联系方式",
    ],
    "agent_name": [
        "采购代理机构名称", "代理机构名称",
        "采购代理机构", "代理机构",
    ],
    "agent_address": [
        "采购代理机构地址", "代理机构地址",
    ],
    "agent_contact": [
        "采购代理机构联系方式", "代理机构联系方式",
    ],
    "contact_name": [
        "项目联系人",
    ],
    "contact_tel": [
        "项目联系电话", "联系电话",
    ],
}


# ========================= 行业分类 =========================
INDUSTRY_RULES = [
    ("医疗卫生", ["医疗", "医院", "卫生", "药", "医学", "器械", "疾控", "保健"]),
    ("教育培训", ["学校", "大学", "学院", "教育", "培训", "教学", "运动", "体育"]),
    ("公共安全", ["消防", "公安", "警务", "安防", "应急", "救援", "司法"]),
    ("信息技术", ["软件", "信息化", "数字化", "智慧", "信息系统", "系统集成",
                 "计算机", "机房", "云计算", "大数据", "运维", "网络安全"]),
    ("工程建设", ["工程", "施工", "建筑", "装修", "改造", "修缮", "监理", "市政"]),
    ("市政交通", ["公路", "交通", "轨道", "公交", "停车", "路灯", "隧道"]),
    ("水利环保", ["水利", "水务", "供水", "排水", "污水", "环保", "生态", "垃圾"]),
    ("能源电力", ["电力", "电网", "光伏", "风电", "能源", "燃气", "配电"]),
    ("农林牧渔", ["农业", "农村", "林业", "畜牧", "渔业", "种植"]),
    ("文化旅游", ["文化", "旅游", "图书", "博物馆", "景区", "文物", "赛事"]),
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


# ========================= 行政区域 =========================
PROVINCE_CITY_MAP = {
    "萍乡": ("江西省", "萍乡市"), "南昌": ("江西省", "南昌市"),
    "赣州": ("江西省", "赣州市"), "九江": ("江西省", "九江市"),
    "宜春": ("江西省", "宜春市"), "上饶": ("江西省", "上饶市"),
    "吉安": ("江西省", "吉安市"), "抚州": ("江西省", "抚州市"),
    "景德镇": ("江西省", "景德镇市"), "新余": ("江西省", "新余市"),
    "鹰潭": ("江西省", "鹰潭市"),
    "成都": ("四川省", "成都市"), "广安": ("四川省", "广安市"),
    "岳池": ("四川省", "广安市"), "邻水": ("四川省", "广安市"),
    "绵阳": ("四川省", "绵阳市"), "德阳": ("四川省", "德阳市"),
    "自贡": ("四川省", "自贡市"), "泸州": ("四川省", "泸州市"),
    "南充": ("四川省", "南充市"), "宜宾": ("四川省", "宜宾市"),
    "达州": ("四川省", "达州市"), "乐山": ("四川省", "乐山市"),
}


def extract_admin_region(source: str, content: str) -> str:
    text = (source or "") + " " + (content or "")[:300]
    for key, (prov, city) in PROVINCE_CITY_MAP.items():
        if key in text:
            return f"{prov}/{city}"
    return ""


# ========================= 通用工具 =========================
def normalize_url(url: str) -> str:
    return re.sub(r"/\./", "/", url or "")


def extract_text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.xpath(".//text()"))).strip()


def parse_amount(raw: str) -> Optional[float]:
    """
    按原文单位判断，返回万元。
    - "14.00万元" → 14.0
    - "836600 元" → 83.66
    - "8500元" → 0.85
    - "100" (无单位) → 按元处理 → 0.01
    """
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
    if "元" in raw:
        return round(v / 10000, 6)
    return round(v / 10000, 6)


# ========================= 抓取 =========================
def get_html_tree(url: str, timeout: int = 20):
    """请求页面并返回 lxml 树，自动处理 /html/a/ → /html/b/ 外壳页"""
    def _fetch(u):
        try:
            r = requests.get(u, impersonate="chrome120", timeout=timeout)
            r.raise_for_status()
            r.encoding = "utf-8"
            return etree.HTML(r.text)
        except Exception:
            return None

    tree = _fetch(url)
    if tree is None:
        return None

    # 从 iframe 提取真实内容 URL
    iframe_src = tree.xpath(
        '//div[@class="detailShow"]//iframe/@src '
        '| //div[@class="fully_toggle_cont"]//iframe/@src'
    )
    content_url = None
    if iframe_src:
        s = (iframe_src[0] or "").strip()
        if s:
            content_url = s if s.startswith("http") else "https://www.ggzy.gov.cn" + s

    # 兜底：/html/a/ → /html/b/
    if not content_url and "/information/deal/html/a/" in url:
        content_url = url.replace("/information/deal/html/a/", "/information/deal/html/b/")

    if content_url and content_url != url:
        tree2 = _fetch(content_url)
        if tree2 is not None:
            return tree2

    return tree


# ========================= 正文文本提取 =========================
def extract_content_text(tree) -> str:
    """
    提取正文文本。按优先级尝试已知容器：
    #jiangxi-gg-wrapper（旧江西）
    .detail_content（大多数）
    #mycontent
    body
    """
    for selector in [
        '//div[@id="jiangxi-gg-wrapper"]',
        '//div[contains(@class, "detail_content")]',
        '//div[@id="mycontent"]',
    ]:
        nodes = tree.xpath(selector)
        if nodes:
            root = nodes[0]
            # 按块级元素边界插入换行
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
            text = re.sub(r"\n+", "\n", text).strip()
            return text
    return ""


# ========================= 文本预处理 =========================
def preprocess_text(text: str) -> str:
    """去掉章节序号（一、/ 1. / 1、），规整结构"""
    # 章节号：中文数字或阿拉伯数字 + 、/.
    text = re.sub(
        r"(?:^|[\n\s])([一二三四五六七八九十]{1,3}|\d{1,3})[、.](?=\s*[\u4e00-\u9fa5])",
        "\n",
        text,
    )
    text = re.sub(r"\n+", "\n", text)
    return text


# ========================= 通用字段提取 =========================
def extract_fields_from_text(text: str) -> Dict[str, str]:
    """
    从文本提取字段：
    1. 找所有别名位置
    2. 每个字段只取第一次出现
    3. 值从字段名后（含可选冒号）→ 下一个字段名/句末
    """
    text = preprocess_text(text)

    # 收集所有别名匹配
    matches = []
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            # 别名 + 可选（单位）+ 冒号
            pattern = re.escape(alias) + r"\s*(?:[（(][^）)\n]{0,12}[）)])?\s*[：:]\s*"
            for m in re.finditer(pattern, text):
                matches.append({
                    "field": field,
                    "alias": alias,
                    "start": m.start(),
                    "value_start": m.end(),
                    "alias_len": len(alias),
                })

    # 长别名优先；每字段只取第一次
    matches.sort(key=lambda x: (x["start"], -x["alias_len"]))
    used_fields = set()
    filtered = []
    for m in matches:
        if m["field"] in used_fields:
            continue
        # 与已收集的位置是否重叠
        overlap = False
        for r in filtered:
            if abs(m["start"] - r["start"]) < 3:
                overlap = True
                break
        if overlap:
            continue
        filtered.append(m)
        used_fields.add(m["field"])

    # 按位置排序
    filtered.sort(key=lambda x: x["start"])

    # 切分值
    result: Dict[str, str] = {}
    for i, m in enumerate(filtered):
        next_start = filtered[i + 1]["start"] if i + 1 < len(filtered) else len(text)
        raw = text[m["value_start"]:next_start].strip()
        # 遇到句末/换行截断
        m_stop = re.match(r"([^。；;\n]{1,200})", raw)
        if m_stop:
            raw = m_stop.group(1).strip()
        # 去掉尾部标点
        raw = re.sub(r"[\s，,、；;。]+$", "", raw).strip()
        result[m["field"]] = raw

    return result


# ========================= 表格提取 =========================
def extract_from_table(tree) -> List[Dict[str, str]]:
    """提取主要标的信息表"""
    details = []
    for root_sel in [
        '//div[@id="jiangxi-gg-wrapper"]',
        '//div[contains(@class, "detail_content")]',
        '//div[@id="mycontent"]',
    ]:
        nodes = tree.xpath(root_sel)
        if not nodes:
            continue
        root = nodes[0]
        for table in root.xpath('.//table'):
            rows = table.xpath('.//tr')
            if len(rows) < 2:
                continue
            headers = [extract_text(td) for td in rows[0].xpath('./td|./th')]
            if "名称" not in headers:
                continue
            if not any(h in headers for h in (
                "施工范围", "服务范围", "服务要求", "施工工期", "项目经理",
                "服务标准", "服务时间", "规格型号",
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
                return details
    return details


# ========================= 主解析 =========================
def parse_notice(tree, url: str) -> Dict[str, Any]:
    data = {k: "" for k in FIELDS}
    data["notice_url"] = normalize_url(url)
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- 标题 ----
    title_nodes = tree.xpath('//h4[@class="h4_o"]/text()')
    data["notice_title"] = title_nodes[0].strip() if title_nodes else ""

    # ---- 公告类型 ----
    t = data["notice_title"]
    if "结果公示" in t or "中标结果" in t or "中标（成交）结果" in t:
        data["notice_type"] = "结果公示"
    elif "中标" in t or "成交" in t:
        data["notice_type"] = "中标公告"
    elif "流标" in t or "废标" in t:
        data["notice_type"] = "流标公告"
    elif "终止" in t:
        data["notice_type"] = "终止公告"
    elif "更正" in t or "变更" in t:
        data["notice_type"] = "更正公告"
    else:
        data["notice_type"] = "结果公示"

    # ---- 发布时间 ----
    pub_span = tree.xpath('//p[@class="p_o"]/span[contains(text(),"发布时间")]/text()')
    if pub_span:
        m = re.search(r"发布时间[：:]\s*(.+)", pub_span[0])
        if m:
            data["publish_time"] = m.group(1).strip()

    # ---- 来源平台 ----
    src = tree.xpath('//label[@id="platformName"]/text()')
    data["source"] = src[0].strip() if src else ""

    # ---- 正文文本 ----
    content_text = extract_content_text(tree)
    data["content_text"] = content_text

    # ---- 通用字段提取 ----
    fields = extract_fields_from_text(content_text)

    data["project_code"] = fields.get("project_code", "")
    data["win_supplier"] = fields.get("win_supplier", "")
    data["judge_experts"] = fields.get("judge_experts", "")
    data["publicity_period"] = fields.get("publicity_period", "")
    data["buyer_name"] = fields.get("buyer_name", "")
    data["buyer_address"] = fields.get("buyer_address", "")
    data["buyer_contact"] = fields.get("buyer_contact", "")
    data["agent_name"] = fields.get("agent_name", "")
    data["agent_address"] = fields.get("agent_address", "")
    data["agent_contact"] = fields.get("agent_contact", "")
    data["contact_name"] = fields.get("contact_name", "")
    data["contact_tel"] = fields.get("contact_tel", "")

    # ---- 金额（单位感知） ----
    amt_raw = fields.get("win_total_amount", "")
    if amt_raw:
        data["win_total_amount"] = parse_amount(amt_raw)

    fee_raw = fields.get("agent_service_fee", "")
    if fee_raw:
        data["agent_service_fee"] = parse_amount(fee_raw)

    # ---- 项目联系人电话兜底 ----
    if not data["contact_tel"] and data["agent_contact"]:
        # 只取数字部分
        m = re.search(r"([\d\-]{7,})", data["agent_contact"])
        if m:
            data["contact_tel"] = m.group(1)

    # ---- 主要标的信息表 ----
    details = extract_from_table(tree)
    data["win_detail_json"] = json.dumps(details, ensure_ascii=False) if details else ""

    # ---- 采购方式 ----
    m = re.search(r"采购方式[：:]\s*([^\s，。；\n]+)", content_text)
    if m:
        data["purchase_method"] = m.group(1).strip()
    else:
        for kw in ("竞争性磋商", "竞争性谈判", "单一来源", "询价", "公开招标",
                   "邀请招标", "框架协议", "竞价"):
            if kw in content_text:
                data["purchase_method"] = kw
                break

    # ---- 行业 ----
    data["industry"] = classify_industry(data["notice_title"], content_text)

    # ---- 行政区域 ----
    data["admin_region"] = extract_admin_region(data["source"], content_text)

    # ---- 预算 ----
    data["budget_source"] = "default"

    # ---- 附件 ----
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
    if attach:
        data["attach_file_names"] = ",".join(x["attach_name"] for x in attach)
        data["attach_json"] = json.dumps(attach, ensure_ascii=False)

    return data


# ========================= 单条测试 =========================
if __name__ == "__main__":
    test_urls = [
        # 旧版江西（萍乡）
        "https://www.ggzy.gov.cn/information/deal/html/b/360000/0202/20260911/0036df0f76b820ff42508532314870c996d3.html",
        # 新版抚州
        "https://www.ggzy.gov.cn/information/deal/html/b/360000/9002/20260912/0036926c190d8bb34f9eb298533e3ff360fd.html",
        # 四川（本次新加）
        "https://www.ggzy.gov.cn/information/deal/html/b/340000/9002/20260910/003420053b6cf1a34fefaa34e03b9923e6df.html",
    ]

    for url in test_urls:
        print(f"\n{'=' * 80}\nURL: {url}\n{'=' * 80}")
        tree = get_html_tree(url)
        if tree is None:
            print("抓取失败")
            continue
        data = parse_notice(tree, url)
        for k in FIELDS:
            v = data.get(k)
            if v in ("", None):
                continue
            if isinstance(v, str) and len(v) > 200:
                v = v[:200] + "..."
            print(f"{k}: {v}")