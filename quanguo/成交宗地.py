# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 土地成交（成交宗地）通用解析器 v6
修复：
1. project_code 前缀切分：用 rfind 动作词而非 lookahead
2. 表格识别：用第 2 行偶数位 key 占比判断 key-value 交替 vs 表头+数据行
3. admin_region：买家字段找市/县/区 + 全字段找省份 + source 兜底
"""
import re
import json
import datetime
from typing import Optional, Dict, Any, List, Tuple

from curl_cffi import requests
from lxml import etree

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

FIELD_ALIASES: Dict[str, List[str]] = {
    "project_code":     ["项目编号", "公告编号", "宗地代码"],
    "land_no":          ["宗地编号", "地块编号", "宗地号"],
    "land_location":    ["地块位置", "土地位置", "宗地位置"],
    "land_use":         ["土地用途", "宗地用途"],
    "land_area":        ["土地面积", "宗地面积"],
    "land_years":       ["出让年限", "使用年限"],
    "win_total_amount": ["成交总价", "成交价", "成交金额"],
    "budget":           ["出让起始价", "起始价", "起拍价", "挂牌起始价"],
    "buyer_name":       ["出让人", "出让单位", "出让方"],
    "win_supplier":     ["竞得人", "竞得单位", "受让单位", "受让人"],
    "buyer_address":    ["地址"],
    "contact_name":     ["联系人", "联 系 人"],
    "contact_tel":      ["联系电话", "联系方式"],
    "publicity_period": ["公示期", "公示期限"],
}

AMOUNT_FIELDS = {"win_total_amount", "budget", "agent_service_fee"}
NUMERIC_FIELDS = {"land_area", "land_years"}

INDUSTRY_RULES = [
    ("土地出让", ["建设用地", "土地", "地块", "宗地", "出让", "挂牌"]),
    ("房地产",   ["商住", "住宅", "商业", "房地产", "置业"]),
    ("工程建设", ["工程", "施工", "建筑", "装修", "改造", "监理"]),
]

PROVINCES = [
    "北京", "天津", "上海", "重庆",
    "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东",
    "河南", "湖北", "湖南", "广东", "海南",
    "四川", "贵州", "云南", "陕西", "甘肃", "青海",
    "内蒙古", "广西", "西藏", "宁夏", "新疆",
]

CHAPTER_FRAGMENTS = {
    "基本情况", "基", "地块", "地基本情况", "本情况",
    "地块的基本情况", "人基本情况", "出让",
}


# ========================= 基础工具 =========================
def classify_industry(title: str, content: str) -> str:
    for name, kws in INDUSTRY_RULES:
        if any(kw in (title or "") for kw in kws):
            return name
    for name, kws in INDUSTRY_RULES:
        if any(kw in (content or "")[:600] for kw in kws):
            return name
    return "其他"


def get_html_tree(url: str, timeout: int = 20):
    try:
        r = requests.get(url, impersonate="chrome120", timeout=timeout)
        r.raise_for_status()
        r.encoding = "utf-8"
        return etree.HTML(r.text)
    except Exception:
        return None


def extract_text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.xpath(".//text()"))).strip()


def to_amount(raw: str, unit: str) -> Optional[float]:
    if not raw:
        return None
    m = re.search(r"([\d,]+\.?\d*)", raw)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if unit and "元" in unit and "万" not in unit:
        v /= 10000.0
    return round(v, 6)


# ========================= 值边界 =========================
STOP_PATTERN = re.compile(r"[。；;\n]|[一二三四五六七八九十]+[、.]")


def trim_value(raw: str, max_len: int = 200) -> str:
    if not raw:
        return ""
    raw = re.sub(r"^[\s：:（(【\[「『、，,\t]+", "", raw)
    m = STOP_PATTERN.search(raw)
    if m:
        raw = raw[:m.start()]
    raw = re.sub(r"[\s，,、；;。]+$", "", raw).strip()
    return raw[:max_len]


# ========================= 文本字段 =========================
def extract_from_text(text: str) -> Dict[str, Tuple[str, str]]:
    matches = []
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            for m in re.finditer(re.escape(alias), text):
                matches.append({
                    "field": field, "alias": alias,
                    "start": m.start(), "end": m.end(),
                    "alias_len": len(alias),
                })

    matches.sort(key=lambda x: (x["start"], -x["alias_len"]))
    used_field = set()
    filtered = []
    for m in matches:
        if m["field"] in used_field:
            continue
        overlap = False
        for r in filtered:
            if not (m["end"] <= r["start"] or m["start"] >= r["end"]):
                overlap = True
                break
        if overlap:
            continue
        filtered.append(m)
        used_field.add(m["field"])

    filtered.sort(key=lambda x: x["start"])

    result: Dict[str, Tuple[str, str]] = {}
    for i, m in enumerate(filtered):
        field = m["field"]
        end = m["end"]
        tail = text[end:end + 15]
        unit = ""
        unit_m = re.match(r"^[（(]([^）)]{1,10})[）)]", tail)
        if unit_m:
            unit = unit_m.group(1)
            end += unit_m.end()

        next_start = filtered[i + 1]["start"] if i + 1 < len(filtered) else len(text)
        raw = text[end:next_start]

        m_chapter = re.search(r"[一二三四五六七八九十]+[、.]", raw)
        if m_chapter:
            raw = raw[:m_chapter.start()]

        value = trim_value(raw, max_len=200)
        if field in AMOUNT_FIELDS:
            value = value[:30]
        elif field in NUMERIC_FIELDS:
            value = value[:20]

        if value:
            result[field] = (value, unit)

    return result


# ========================= 表格提取 =========================
def match_header_to_field(hname: str) -> Optional[str]:
    if not hname:
        return None
    # 先剥离单位括号
    base = re.sub(r"[（(][^）)]*[）)]\s*$", "", hname).strip()
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias == base:
                return field
    candidates = []
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in base:
                candidates.append((len(alias), field))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return None


def parse_header_cell(td) -> Tuple[str, str]:
    raw = extract_text(td)
    m = re.match(r"^(.*?)[（(]([^）)]*)[）)]\s*$", raw)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return raw.strip(), ""


def extract_from_tables(tree) -> Dict[str, Tuple[str, str]]:
    """
    智能识别表格结构：
    - key-value 交替：每行偶数位是 key、奇数位是 value
    - 表头+数据行：第一行是 key，后续行是 value
    判据：看第 2 行（若有）偶数位是 key 的比例
    """
    result: Dict[str, Tuple[str, str]] = {}

    for table in tree.xpath('//div[@id="mycontent"]//table'):
        rows = table.xpath('.//tr')
        if not rows:
            continue

        row_cells = []
        for tr in rows:
            cells = [extract_text(td) for td in tr.xpath('./td|./th')]
            if any(cells):
                row_cells.append(cells)
        if not row_cells:
            continue

        # 判断：第 2 行偶数位是 key 的比例
        is_kv = False
        if len(row_cells) >= 2:
            second = row_cells[1]
            even_total = 0
            even_hit = 0
            for i in range(0, len(second), 2):
                even_total += 1
                if match_header_to_field(second[i]):
                    even_hit += 1
            if even_total and even_hit / even_total >= 0.6:
                is_kv = True
        elif len(row_cells) == 1:
            # 单行：也判一下
            cells = row_cells[0]
            even_total = 0
            even_hit = 0
            for i in range(0, len(cells), 2):
                even_total += 1
                if match_header_to_field(cells[i]):
                    even_hit += 1
            if even_total >= 2 and even_hit / even_total >= 0.6:
                is_kv = True

        # === 情形 A：key-value 交替 ===
        if is_kv:
            for cells in row_cells:
                for i in range(0, len(cells) - 1, 2):
                    key = cells[i].strip()
                    val = cells[i + 1].strip()
                    field = match_header_to_field(key)
                    if field and field not in result and val:
                        unit_m = re.match(r"^(.*?)[（(]([^）)]*)[）)]", key)
                        unit = unit_m.group(2) if unit_m else ""
                        result[field] = (trim_value(val, 200), unit)
            continue

        # === 情形 B：表头 + 数据行 ===
        if len(row_cells) < 2:
            continue
        header_cells = rows[0].xpath('./td|./th')
        headers = [parse_header_cell(td) for td in header_cells]
        for cells in row_cells[1:]:
            if not any(cells):
                continue
            for i, (hname, hunit) in enumerate(headers):
                if i >= len(cells):
                    break
                value = cells[i].strip()
                if not value:
                    continue
                matched = match_header_to_field(hname)
                if matched and matched not in result:
                    result[matched] = (value, hunit)

    return result


# ========================= 专项提取 =========================
CODE_RE = re.compile(r"[\u4e00-\u9fa5]{2,15}字[〔\[]\d{4}[〕\]]\d+号")
ACTION_WORDS = ["公示", "结果", "使用权", "出让", "挂牌", "拍卖", "成交", "竞得"]


def extract_project_code(tree, text: str, title: str) -> str:
    v = tree.xpath('//span[@id="noticeNo"]/text()')
    if v:
        return v[0].strip()

    candidates = set()
    for src in (title or "", text or ""):
        for m in CODE_RE.finditer(src):
            code = m.group(0)
            prefix, suffix = code.split("字", 1)
            # 切掉前缀里含动作词的部分
            for w in ACTION_WORDS:
                idx = prefix.rfind(w)
                if idx >= 0:
                    prefix = prefix[idx + len(w):]
                    break
            if len(prefix) >= 2:
                candidates.add(prefix + "字" + suffix)
    if not candidates:
        return ""
    return sorted(candidates, key=len)[0]


def extract_buyer_name(text: str) -> str:
    # 1. 结构化"出让人：XXX"
    m = re.search(
        r"出让人\s*[：:]\s*"
        r"([^\s，。；\n]+?)"
        r"(?=\s*地\s*址|\s*联\s*系|\s*电\s*话|"
        r"\s*[一二三四五六七八九十]+[、.]|\s*$)",
        text
    )
    if m:
        v = m.group(1).strip()
        if v not in CHAPTER_FRAGMENTS and len(v) >= 3:
            return v
    # 2. "经XX委托"
    m = re.search(
        r"经\s*([\u4e00-\u9fa5]{3,30}?"
        r"(?:自然资源局|储备交易中心|交易中心|自然资源和规划局))"
        r"\s*(?:委托|的委托)",
        text
    )
    if m:
        return m.group(1)
    # 3. "受XX的委托"
    m = re.search(
        r"受\s*([\u4e00-\u9fa5]{3,30}?"
        r"(?:自然资源局|储备交易中心|交易中心|自然资源和规划局))"
        r"\s*的委托",
        text
    )
    if m:
        return m.group(1)
    # 4. 正文开头机构名（如"镇雄县自然资源局国有土地使用权拍卖..."）
    m = re.match(
        r"^([\u4e00-\u9fa5]{2,30}?"
        r"(?:自然资源局|储备交易中心|交易中心|自然资源和规划局))",
        text
    )
    if m:
        return m.group(1)
    return ""


def extract_admin_region(buyer_name: str, buyer_address: str,
                         content_head: str = "", source: str = "") -> str:
    buyer_text = f"{buyer_name or ''} {buyer_address or ''}"

    # 找市/县/区
    city = ""
    for src in (buyer_text, content_head):
        if not src:
            continue
        m = re.search(r"([\u4e00-\u9fa5]{2,6}?)(市|县|区|自治州|地区)", src)
        if m:
            city = m.group(1) + m.group(2)
            break

    # 找省份（先用买家字段，其次正文头，最后 source）
    prov = None
    for src in (buyer_text, content_head, source):
        if not src:
            continue
        for p in PROVINCES:
            if p in src:
                prov = p
                break
        if prov:
            break

    if not prov and not city:
        return ""
    if not prov:
        return city
    if not city:
        return prov
    if prov in ("北京", "天津", "上海", "重庆"):
        return prov
    return f"{prov}/{city}"


def extract_publicity_period(tree, text: str) -> str:
    dt = tree.xpath('//span[@id="dealTime"]/text()')
    if dt:
        return dt[0].strip()
    m = re.search(
        r"公示期[为:：\s]*"
        r"(\d{4}年\d{1,2}月\d{1,2}日)\s*[至到\-]\s*"
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        text
    )
    if m:
        return f"{m.group(1)}至{m.group(2)}"
    return ""


# ========================= 基础字段 =========================
def _base_fields(tree, url: str) -> Dict[str, Any]:
    data = {k: "" for k in FIELDS}
    data["notice_url"] = re.sub(r"/\./", "/", url or "")
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["notice_type"] = "成交宗地"

    h4 = tree.xpath('//h4[@class="h4_o"]/text()')
    data["notice_title"] = h4[0].strip() if h4 else ""

    pub_span = tree.xpath('//p[@class="p_o"]/span[1]/text()')
    if pub_span:
        m = re.search(r"发布时间[：:]\s*(.+)", pub_span[0])
        if m:
            data["publish_time"] = m.group(1).strip()

    src = tree.xpath('//label[@id="platformName"]/text()')
    data["source"] = src[0].strip() if src else ""

    content_nodes = tree.xpath('//div[@id="mycontent"]//text()')
    content_text = "".join(content_nodes)
    content_text = re.sub(r"[ \t\u3000]+", " ", content_text)
    content_text = re.sub(r"\n\s*\n+", "\n", content_text).strip()
    data["content_text"] = content_text

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


# ========================= 主解析 =========================
def parse_land_result(tree, url: str) -> Dict[str, Any]:
    data = _base_fields(tree, url)
    text = data["content_text"]

    table_fields = extract_from_tables(tree)
    text_fields = extract_from_text(text)

    merged: Dict[str, Tuple[str, str]] = dict(text_fields)
    for f, tv in table_fields.items():
        merged[f] = tv

    for field, (value, unit) in merged.items():
        if field == "land_no":
            continue
        if field in AMOUNT_FIELDS:
            data[field] = to_amount(value, unit)
            if field == "budget" and data[field] is not None:
                data["budget_source"] = "table" if field in table_fields else "content"
        elif field in NUMERIC_FIELDS:
            data[field] = value
        else:
            data[field] = value.strip()

    # ============ 专项兜底 ============
    code = extract_project_code(tree, text, data["notice_title"])
    if code:
        data["project_code"] = code

    buyer = extract_buyer_name(text)
    if buyer:
        data["buyer_name"] = buyer

    if data["contact_tel"]:
        v = re.split(r"[一二三四五六七八九十]+[、.]", data["contact_tel"])[0]
        v = re.split(r"联系单位|单位地址", v)[0]
        data["contact_tel"] = re.sub(r"[\s，,、]+$", "", v).strip()

    if data["win_supplier"]:
        v = re.split(r"备注\s*[：:]", data["win_supplier"])[0]
        data["win_supplier"] = re.sub(r"[\s，,、]+$", "", v).strip()

    pub = extract_publicity_period(tree, text)
    if pub:
        data["publicity_period"] = pub

    region = extract_admin_region(
        data["buyer_name"], data["buyer_address"],
        content_head=text[:200], source=data["source"],
    )
    if region:
        data["admin_region"] = region

    data["industry"] = classify_industry(data["notice_title"], text)

    # win_detail_json 组装
    detail = {}
    if data.get("project_code"):
        detail["公告编号"] = data["project_code"]
    if merged.get("land_no"):
        detail["宗地编号"] = merged["land_no"][0]
    if merged.get("land_location"):
        detail["地块位置"] = merged["land_location"][0]
    if merged.get("land_area"):
        detail["土地面积"] = merged["land_area"][0]
    if merged.get("land_use"):
        detail["土地用途"] = merged["land_use"][0]
    if merged.get("land_years"):
        detail["出让年限"] = merged["land_years"][0]
    if data["win_total_amount"] is not None:
        detail["成交价（万元）"] = data["win_total_amount"]
    if data["win_supplier"]:
        detail["竞得人"] = data["win_supplier"]
    if detail:
        data["win_detail_json"] = json.dumps([detail], ensure_ascii=False)

    return data


# ========================= 测试 =========================
if __name__ == "__main__":
    test_urls = [
        "https://www.ggzy.gov.cn/information/deal/html/b/530000/0302/20260911/0053577729b6c366442eabd49ad7c1dca249.html",
        "https://www.ggzy.gov.cn/information/deal/html/b/b640000/0302/20260911/00647bef4a589c824d60838bbd96f431c338.html",
        "https://www.ggzy.gov.cn/information/deal/html/b/530000/0302/20260911/0053134dd880f4d64ff5be1912f669517941.html",
    ]

    for url in test_urls:
        print(f"\n{'=' * 80}\nURL: {url}\n{'=' * 80}")
        tree = get_html_tree(url)
        if tree is None:
            print("抓取失败")
            continue
        data = parse_land_result(tree, url)
        for k in FIELDS:
            v = data.get(k)
            if v in ("", None):
                continue
            if isinstance(v, str) and len(v) > 200:
                v = v[:200] + "..."
            print(f"{k}: {v}")