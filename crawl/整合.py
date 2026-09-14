# -*- coding: utf-8 -*-
from curl_cffi import requests
from lxml import etree
import re
import datetime
import json
import csv
import sys
import time
import random
from typing import Optional, Dict, Any, List

# ========================= 公共辅助函数 =========================

def normalize_url(url: str) -> str:
    """规范化 URL：去掉 /./ 相对路径片段"""
    return re.sub(r"/\./", "/", url)


SOURCE_DOMAIN_MAP = {
    "www.ccgp.gov.cn": "中国政府采购网",
    "ccgp.gov.cn": "中国政府采购网",
    "search.ccgp.gov.cn": "中国政府采购网",
}


def get_source_name(url: str) -> str:
    """根据 URL 域名返回数据来源平台名称"""
    m = re.search(r"https?://([^/]+)/", url + "/")
    if not m:
        return ""
    domain = m.group(1)
    return SOURCE_DOMAIN_MAP.get(domain, domain)


def get_html_tree(url: str, impersonate: str = "chrome120", timeout: int = 20,
                  retries: int = 2) -> Optional[etree._Element]:
    """请求页面并返回 lxml 解析树；网络异常自动重试，4xx/5xx 死链不重试"""
    url = normalize_url(url)
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, impersonate=impersonate, timeout=timeout)
            resp.raise_for_status()
            return etree.HTML(resp.text)
        except requests.exceptions.HTTPError:
            # 死链（404等）重试无意义，直接放弃
            return None
        except Exception as e:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return None
    return None


# ---------- 行业分类（标题优先，关键词按优先级从上到下匹配） ----------
INDUSTRY_RULES: List = [
    ("医疗卫生", ["医疗", "医院", "卫生", "药", "医学", "器械", "疾控", "保健", "康复", "护理", "门诊", "急救"]),
    ("教育培训", ["学校", "大学", "学院", "教育", "培训", "教学", "校园", "实训", "幼儿园", "图书馆"]),
    ("公共安全", ["消防", "公安", "警务", "安防", "应急", "救援", "司法", "监狱", "法庭", "执法", "武警", "戒"])
    ,
    ("信息技术", ["软件", "信息化", "数字化", "智慧", "信息系统", "系统集成", "网络安全", "信息安全",
              "计算机", "机房", "云计算", "大数据", "运维", "地理信息", "互联网", "网站建设", "数据库"]),
    ("工程建设", ["工程", "施工", "建筑", "装修", "改造", "修缮", "监理", "市政", "道路", "桥梁", "管网", "水利工程"]),
    ("市政交通", ["公路", "交通", "轨道", "公交", "客运", "停车", "路灯", "隧道", "航道", "港口"]),
    ("水利环保", ["水利", "水务", "供水", "排水", "污水", "环保", "生态", "垃圾", "固废", "环卫", "流域", "河道"]),
    ("能源电力", ["电力", "电网", "光伏", "风电", "能源", "锅炉", "供暖", "供热", "燃气", "配电"]),
    ("农林牧渔", ["农业", "农村", "林业", "畜牧", "渔业", "种植", "种苗", "农药", "化肥", "乡村振兴", "粮食"]),
    ("文化旅游", ["文化", "旅游", "体育", "图书", "博物馆", "景区", "广电", "演艺", "赛事", "档案", "文物"]),
    ("服务外包", ["物业", "保洁", "保安", "绿化养护", "食堂", "餐饮", "劳务", "外包", "维保", "租赁", "印刷",
              "保险", "审计", "咨询", "法律服务", "广告", "宣传"]),
    ("科研检测", ["实验室", "科研", "检测", "试验", "认证", "计量"]),
    ("设备购置", ["设备", "仪器", "车辆", "空调", "电梯", "家具", "购置", "服装", "办公用品"]),
]


def classify_industry(title: str, content: str) -> str:
    """行业归类：标题命中优先（最可靠），否则用正文前 600 字补充判断，未命中返回 其他"""
    for name, keywords in INDUSTRY_RULES:
        if any(kw in (title or "") for kw in keywords):
            return name
    sample = (content or "")[:600]
    for name, keywords in INDUSTRY_RULES:
        if any(kw in sample for kw in keywords):
            return name
    return "其他"


# ---------- 截止时间提取 ----------
DEADLINE_PATTERNS = [
    # 于2026年09月11日 09点30分（北京时间）前提交响应文件
    re.compile(r"于\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*[点时]\s*(\d{1,2})\s*分?"
               r"[^。；\n]{0,25}?前\s*(?:提交|递交|报价|响应|参加)"),
    # 递交/响应文件截止时间：2026年09月11日 09:30
    re.compile(r"(?:递交|提交|报价|响应)[^。；\n]{0,8}截止时间[：:]?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
               r"\s*(\d{1,2})\s*[点时:：]\s*(\d{1,2})?"),
    # 截止时间：2026年09月11日 09:30
    re.compile(r"截止时间[：:]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*[点时:：]\s*(\d{1,2})?"),
]


def extract_bid_deadline(content_text: str) -> str:
    """从正文提取投标/响应文件递交截止时间，统一格式 YYYY-MM-DD HH:MM"""
    for pat in DEADLINE_PATTERNS:
        m = pat.search(content_text)
        if m:
            parts = [int(x) if x else 0 for x in m.groups()]
            y, mo, d, h, mi = parts
            try:
                return datetime.datetime(y, mo, d, h, mi).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                return f"{y:04d}-{mo:02d}-{d:02d}"
    return ""


def extract_content_text(tree: etree._Element) -> str:
    """提取正文全文（含表格文本），压缩多余空白"""
    nodes = tree.xpath('//div[@class="vF_detail_content"]//text()')
    text = "".join(nodes)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _clean_contact_name(raw: str) -> str:
    """裁掉名字尾部粘连的标签词（如 '联系代表电话' -> '联系代表'）"""
    return re.sub(r"(项?目?联?系?电\s*话|联\s*系\s*方\s*式|电\s*话).*$", "", raw).strip(" ：:、，")


def _main_phone_digits(s: str) -> str:
    """提取主号码数字（不含分机）。
    区号-本地号 格式：前 3~4 位为区号，后 7~8 位为本地号，之后 -或转 为分机；
    无区号格式：前 7~8 位为本地号，之后 -或转 为分机。"""
    s = s.strip()
    m = re.match(r'(\d{3,4})[-－—](\d{7,8})', s)
    if m:
        main = m.group(1) + m.group(2)
        rest = s[m.end():]
        if rest and rest[0] in '-－—转':
            return main
        return main
    m = re.match(r'\d{7,8}', s)
    if m:
        rest = s[m.end():]
        if rest and rest[0] in '-－—转':
            return m.group(0)
    return re.sub(r'\D', '', s)


def _should_strip_trailing(raw: str, k: int, following: str) -> bool:
    """判断是否应剥除 raw 尾部 k 位数字（段号）。
    following 以 . 或 ． 开头时，尾部 1~2 位数字很可能是段号。
    按号型区分：
    - 手机(1 开头)：剥除后主号恰好 11 位
    - 固话(0 开头)：有空格分隔符时剥至 10~12 位；无分隔符时仅当原主号超 12 位才剥
    - 本地号：有分隔符时剥至 7~8 位；无分隔符时仅当原主号超 8 位或有分机时才剥"""
    if len(raw) <= k or not raw[-k:].isdigit():
        return False
    if following[:1] not in (".", "．"):
        return False
    left = raw[:-k].rstrip()
    char_before = raw[-(k + 1)] if len(raw) > k else ""
    has_sep = not char_before.isdigit()
    orig_main = _main_phone_digits(raw)
    left_main = _main_phone_digits(left)
    if not left_main:
        return False
    has_ext = len(orig_main) < len(re.sub(r"\D", "", raw))
    if left_main[0] == "1":
        return len(left_main) == 11
    if left_main[0] == "0":
        if has_sep:
            return 10 <= len(left_main) <= 12
        return (len(orig_main) > 12 or has_ext) and 10 <= len(left_main) <= 12
    if has_sep:
        return 7 <= len(left_main) <= 8
    return (len(orig_main) > 8 or has_ext) and 7 <= len(left_main) <= 8


def _clean_contact_tel(raw: str, following: str = '') -> str:
    """清洗电话：
    - 截断段落编号粘连（含全角句点 ．）
    - 捕获串外紧跟 . 或 ．时，按号型校验剥除尾部段号
    - 移除所有内部空白（含换行）
    - 去掉尾部未闭合括号与游离标点"""
    raw = re.split(r"\d?\s*[.、．](?=[\u4e00-\u9fff])", raw)[0].strip()
    if following[:1] in (".", "．"):
        for k in (1, 2):
            if _should_strip_trailing(raw, k, following):
                raw = raw[:-k].rstrip()
                break
    raw = re.sub(r"\s+", "", raw)
    raw = re.sub(r"[（(]+$", "", raw).strip("、，,：:")
    return raw


def extract_contact_info(tree: Optional[etree._Element], content_text: str) -> Dict[str, str]:
    """提取项目联系人/联系电话。
    1) 结构化表格（div.table 的 项目联系人/项目联系电话 td）优先，天然配对正确；
    2) 正文正则回退：以"项目联系人："为锚点，电话只在该位置之后找，
       避免配到前面采购人/代理机构块里的"联系方式"；
    3) 名字裁掉粘连标签词，电话按段落编号截断。"""
    result = {"contact_name": "", "contact_tel": ""}

    if tree is not None:
        name_nodes = tree.xpath('//div[@class="table"]//td[text()="项目联系人"]/following-sibling::td[1]/text()')
        if name_nodes and name_nodes[0].strip():
            result["contact_name"] = name_nodes[0].strip()
        tel_nodes = tree.xpath('//div[@class="table"]//td[text()="项目联系电话"]/following-sibling::td[1]/text()')
        if tel_nodes and tel_nodes[0].strip():
            result["contact_tel"] = tel_nodes[0].strip()

    if not result["contact_name"]:
        m = (re.search(r"项目联系人\s*[：:]\s*([\u4e00-\u9fff·、]{2,15})", content_text)
             or re.search(r"联\s*系\s*人\s*[：:]\s*([\u4e00-\u9fff·、]{2,15})", content_text))
        if m:
            result["contact_name"] = _clean_contact_name(m.group(1))

    if not result["contact_tel"]:
        # 移除换行，避免电话号码跨 HTML 行被截断（如 '0574-88\n3535613'）
        ct = re.sub(r"[\n\r]+", "", content_text)
        tel_pat = re.compile(r"(?:项?目?联\s*系\s*电\s*话|联\s*系\s*电\s*话|电\s*话|联\s*系\s*方\s*式)\s*[：:]\s*([0-9\-—（）()、，,转 Extext]{7,40})")
        anchor = ct.find("项目联系人")
        m = tel_pat.search(ct, anchor if anchor != -1 else 0)
        if m is None and anchor != -1:
            m = tel_pat.search(ct)
        if m:
            following = ct[m.end():m.end() + 3]
            result["contact_tel"] = _clean_contact_tel(m.group(1), following)
    return result


def _extract_common_fields(tree: etree._Element, url: str, notice_type: str) -> Dict[str, Any]:
    """提取所有公告类型共有的基础字段（含附件 JSON）"""
    url = normalize_url(url)
    data = {}

    # 标题
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    data["notice_title"] = title_list[0].strip() if title_list else ""

    # 公告类型（由调用者传入）
    data["notice_type"] = notice_type

    # 正文全文（先提取，后续字段解析都要用）
    content_text = extract_content_text(tree)
    data["content_text"] = content_text

    # 项目编号（兼容：编号值常被 span/strong 包裹，必须用 . 而非 text() 匹配）
    data["project_code"] = ""
    code_xpath = '''
    //div[@class="vF_detail_content"]//p[
        contains(.,"项目编号：") or contains(.,"采购项目编号：")
        or contains(.,"项目编号:") or contains(.,"采购项目编号:")
        or contains(.,"招标编号") or contains(.,"采购编号")
    ]
    '''
    code_list = tree.xpath(code_xpath)
    if code_list:
        raw_str = "".join(code_list[0].xpath(".//text()")).strip()
        match_code = re.search(r"(?:采购项目编号|项目编号|招标编号|采购编号)\s*[：:]\s*([A-Za-z0-9\-_／/\.]+)", raw_str)
        if match_code:
            data["project_code"] = match_code.group(1).strip().rstrip("。，,；")
    if not data["project_code"]:
        # 兜底：从正文全文正则提取
        m = re.search(r"(?:采购项目编号|项目编号|招标编号|采购编号|询价书编号|磋商文件编号|谈判文件编号)"
                      r"\s*[：:]\s*([A-Za-z0-9\-_／/\.]{4,40})", content_text)
        if m:
            data["project_code"] = m.group(1).strip().rstrip("。，,；")

    # 发布时间
    pub_list = tree.xpath('//span[@id="pubTime"]/text()')
    data["publish_time"] = pub_list[0].strip() if pub_list else ""

    # 行政区域
    region_list = tree.xpath('//td[text()="行政区域"]/following-sibling::td[1]/text()')
    data["admin_region"] = region_list[0].strip() if region_list else ""

    # 采购单位信息
    buyer_name_list = tree.xpath('//td[text()="采购单位"]/following-sibling::td[1]/text()')
    data["buyer_name"] = buyer_name_list[0].strip() if buyer_name_list else ""

    buyer_addr_list = tree.xpath('//td[text()="采购单位地址"]/following-sibling::td[1]/text()')
    data["buyer_address"] = buyer_addr_list[0].strip() if buyer_addr_list else ""

    buyer_contact_list = tree.xpath('//td[text()="采购单位联系方式"]/following-sibling::td[1]/text()')
    data["buyer_contact"] = buyer_contact_list[0].strip() if buyer_contact_list else ""

    # 代理机构信息
    agent_name_list = tree.xpath('//td[text()="代理机构名称"]/following-sibling::td[1]/text()')
    data["agent_name"] = agent_name_list[0].strip() if agent_name_list else ""

    agent_addr_list = tree.xpath('//td[text()="代理机构地址"]/following-sibling::td[1]/text()')
    data["agent_address"] = agent_addr_list[0].strip() if agent_addr_list else ""

    agent_contact_list = tree.xpath('//td[text()="代理机构联系方式"]/following-sibling::td[1]/text()')
    data["agent_contact"] = agent_contact_list[0].strip() if agent_contact_list else ""

    # 项目联系人（全类型通用，正文 p 标签中提取）
    data.update(extract_contact_info(tree, content_text))

    # ========== 附件提取（JSON 数组 + 文件名逗号分隔） ==========
    attach_nodes = tree.xpath('//a[@class="bizDownload"]')
    attach_list = []
    names = []
    for a in attach_nodes:
        name_raw = a.xpath("./text()")
        name = name_raw[0].strip() if name_raw else ""

        attach_id = a.xpath("./@id")
        if attach_id:
            uuid_str = attach_id[0].strip()
            full_href = f"https://download.ccgp.gov.cn/oss/download?uuid={uuid_str}"
        else:
            href = a.xpath("./@href")
            if href:
                href_str = href[0].strip()
                if href_str.startswith("http"):
                    full_href = href_str
                else:
                    full_href = "https://www.ccgp.gov.cn" + href_str if href_str.startswith("/") else href_str
            else:
                full_href = ""

        if name:
            attach_list.append({"attach_name": name, "attach_url": full_href})
            names.append(name)

    data["attach_file_names"] = ",".join(names) if names else ""
    data["attach_json"] = json.dumps(attach_list, ensure_ascii=False) if attach_list else ""

    # ========== 新增检索维度字段 ==========
    data["industry"] = classify_industry(data["notice_title"], content_text)
    data["bid_deadline"] = extract_bid_deadline(content_text)
    data["source"] = get_source_name(url)

    data["notice_url"] = url
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return data


def _ensure_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """补全所有可能缺失的字段，确保返回字典包含统一键"""
    defaults = {
        "notice_title": "",
        "notice_type": "",
        "project_code": "",
        "publish_time": "",
        "admin_region": "",
        "buyer_name": "",
        "buyer_address": "",
        "buyer_contact": "",
        "agent_name": "",
        "agent_address": "",
        "agent_contact": "",
        "contact_name": "",
        "contact_tel": "",
        "attach_file_names": "",
        "attach_json": "",
        "notice_url": "",
        "crawl_time": "",
        "content_text": "",
        "industry": "",
        "bid_deadline": "",
        "source": "",

        "purchase_method": "",
        "budget": None,
        "budget_source": "",
        "terminate_reason": "",
        "change_item": "",
        "win_total_amount": None,
        "win_detail_json": "",
        "judge_experts": "",
        "agent_service_fee": None,
        "win_supplier": "",
        "proposed_supplier": "",
        "single_source_reason": "",
        "publicity_period": "",
        "open_bid_time": "",
    }
    for key, default in defaults.items():
        if key not in data:
            data[key] = default
    return data


# ========================= 预算/金额通用提取 =========================

def _parse_budget_wan(raw: str) -> Optional[float]:
    """从字符串提取金额（万元）"""
    if not raw:
        return None
    m = re.search(r"([\d,]+\.?\d*)", raw)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _extract_budget_with_fallback(tree: etree._Element, content_text: str) -> Dict[str, Any]:
    """
    预算金额提取（三级容错）：
    1) 页面表格 预算金额 td（单位万元）
    2) 正文结构化 预算金额：XXX万元
    3) 正文 预算金额（元）：XXX → 换算万元 / 散文体 预算金额 XXX万元
    表格值 < 0.1 视为可疑（元未换算），优先用正文值
    """
    SUSPICIOUS_THRESHOLD = 0.1
    result = {"budget": None, "budget_source": ""}

    table_budget = None
    budget_raw_list = tree.xpath('//td[text()="预算金额"]/following-sibling::td[1]/text()')
    if budget_raw_list:
        table_budget = _parse_budget_wan(budget_raw_list[0].strip())

    content_budget = None
    m = re.search(r"预算金额\s*[：:]\s*([\d,]+\.?\d*)\s*万?元", content_text)
    if m:
        content_budget = float(m.group(1).replace(",", ""))
    if content_budget is None:
        m = re.search(r"预算金额（元）\s*[：:]\s*([\d,]+\.?\d*)", content_text)
        if m:
            content_budget = float(m.group(1).replace(",", "")) / 10000
    if content_budget is None:
        # 散文体：项目预算金额 380万元（人民币）
        m = re.search(r"预算金额\s*([\d,]+\.?\d*)\s*万元", content_text)
        if m:
            content_budget = float(m.group(1).replace(",", ""))

    if table_budget is not None:
        if 0 < table_budget < SUSPICIOUS_THRESHOLD:
            if content_budget is not None:
                result["budget"] = content_budget
                result["budget_source"] = "content"
            else:
                result["budget"] = table_budget
                result["budget_source"] = "table_suspicious"
        else:
            result["budget"] = table_budget
            result["budget_source"] = "table"
    elif content_budget is not None:
        result["budget"] = content_budget
        result["budget_source"] = "content"
    else:
        result["budget_source"] = "default"
    return result


# ========================= 中标供应商明细提取 =========================

def extract_win_detail(tree: etree._Element) -> List[Dict[str, str]]:
    """
    从正文表格提取中标/成交供应商明细。
    ccgp 中标/成交公告的供应商表格表头固定含「供应商名称」列，
    每行对应一个包/一个供应商，列名按表头原样保留（服务/货物/工程名称各异）。
    """
    details = []
    for table in tree.xpath('//div[@class="vF_detail_content"]//table'):
        trs = table.xpath('.//tr')
        if len(trs) < 2:
            continue
        header_cells = ["".join(t.xpath(".//text()")).strip() for t in trs[0].xpath('./td|./th')]
        if not any("供应商" in h for h in header_cells):
            continue
        for tr in trs[1:]:
            cells = ["".join(t.xpath(".//text()")).strip() for t in tr.xpath('./td|./th')]
            if not any(cells):
                continue
            row = {}
            for i, cell in enumerate(cells):
                key = header_cells[i] if i < len(header_cells) else f"col_{i}"
                if cell:
                    row[key] = cell
            if row:
                details.append(row)
    return details


def _sum_amount_from_detail(win_detail_json: str) -> Optional[float]:
    """分包明细 JSON 中含"金额"的列求和（万元）"""
    if not win_detail_json:
        return None
    try:
        details = json.loads(win_detail_json)
    except (ValueError, TypeError):
        return None
    total = 0.0
    found = False
    for row in details:
        for k, v in row.items():
            if "金额" in k and isinstance(v, str):
                m = re.search(r"([\d,]+\.?\d*)", v)
                if m:
                    total += float(m.group(1).replace(",", ""))
                    found = True
    return round(total, 6) if found else None


def _extract_win_supplier(tree: etree._Element, content_text: str) -> Dict[str, Any]:
    """中标供应商：优先表格明细，正文正则兜底"""
    result = {"win_supplier": "", "win_detail_json": ""}
    details = extract_win_detail(tree)
    if details:
        result["win_detail_json"] = json.dumps(details, ensure_ascii=False)
        suppliers = []
        for row in details:
            for k, v in row.items():
                if "供应商" in k and v and v not in suppliers:
                    suppliers.append(v)
        result["win_supplier"] = "、".join(suppliers)
        return result
    # 兜底：正文 供应商名称：XXX
    m = re.search(r"(?:中标|成交)?供应商名称\s*[：:]\s*([^\n，,。;；]{2,60})", content_text)
    if m:
        result["win_supplier"] = m.group(1).strip()
    return result


# ========================= 各类型解析函数 =========================

def parse_tender_notice(tree: etree._Element, url: str) -> Dict[str, Any]:
    """公开招标公告（gkzb）"""
    data = _extract_common_fields(tree, url, "公开招标公告")
    open_bid_list = tree.xpath('//td[text()="开标时间"]/following-sibling::td[1]/text()')
    data["open_bid_time"] = open_bid_list[0].strip() if open_bid_list else ""
    if not data["open_bid_time"]:
        m = re.search(r"开标时间\s*[：:]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*[\d:：点时分]+)", data["content_text"])
        if m:
            data["open_bid_time"] = m.group(1).strip()
    data.update(_extract_budget_with_fallback(tree, data["content_text"]))
    return _ensure_fields(data)


def parse_win_notice(tree: etree._Element, url: str) -> Dict[str, Any]:
    """中标公告（zbgg）"""
    data = _extract_common_fields(tree, url, "中标公告")
    win_total_raw_list = tree.xpath('//td[text()="总中标金额"]/following-sibling::td[1]/text()')
    win_total_raw = win_total_raw_list[0].strip() if win_total_raw_list else ""
    data["win_total_amount"] = _parse_budget_wan(win_total_raw)
    if data["win_total_amount"] is None:
        m = re.search(r"总中标金额\s*[：:]\s*([\d,]+\.?\d*)\s*万?元", data["content_text"])
        if m:
            data["win_total_amount"] = float(m.group(1).replace(",", ""))

    expert_list = tree.xpath('//td[text()="评审专家名单"]/following-sibling::td[1]/text()')
    data["judge_experts"] = expert_list[0].strip() if expert_list else ""

    agent_fee_text = "".join(tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"本项目代理费总金额：")]//text()'))
    data["agent_service_fee"] = None
    m = re.search(r"([\d.]+)", agent_fee_text)
    if m:
        data["agent_service_fee"] = float(m.group(1))

    # 中标供应商 + 分包明细
    data.update(_extract_win_supplier(tree, data["content_text"]))
    if data["win_total_amount"] is None:
        # 兜底：从分包明细金额列求和
        data["win_total_amount"] = _sum_amount_from_detail(data["win_detail_json"])
    return _ensure_fields(data)


def parse_deal_notice(tree: etree._Element, url: str) -> Dict[str, Any]:
    """成交公告（cjgg）"""
    data = _extract_common_fields(tree, url, "成交公告")
    win_amount_list = tree.xpath('//td[contains(text(),"总成交金额")]/following-sibling::td[1]/text()')
    data["win_total_amount"] = None
    if win_amount_list:
        raw = "".join(win_amount_list).strip()
        data["win_total_amount"] = _parse_budget_wan(raw)
    if data["win_total_amount"] is None:
        m = re.search(r"总成交金额\s*[：:]\s*([\d,]+\.?\d*)\s*万?元", data["content_text"])
        if m:
            data["win_total_amount"] = float(m.group(1).replace(",", ""))

    expert_list = tree.xpath('//td[contains(text(),"评审专家")]/following-sibling::td[1]/text()')
    data["judge_experts"] = expert_list[0].strip() if expert_list else ""

    agent_fee_text = "".join(tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"本项目代理费总金额")]//text()'))
    data["agent_service_fee"] = None
    m_fee = re.search(r"([\d.]+)\s*万元", agent_fee_text)
    if m_fee:
        data["agent_service_fee"] = float(m_fee.group(1))

    # 成交供应商 + 分包明细
    data.update(_extract_win_supplier(tree, data["content_text"]))
    if data["win_total_amount"] is None:
        # 兜底：从分包明细金额列求和
        data["win_total_amount"] = _sum_amount_from_detail(data["win_detail_json"])
    return _ensure_fields(data)


def parse_change_notice(tree: etree._Element, url: str) -> Dict[str, Any]:
    """变更公告（gzgg）"""
    data = _extract_common_fields(tree, url, "变更公告")
    change_item_list = tree.xpath('//td[text()="更正事项"]/following-sibling::td[1]/text()')
    data["change_item"] = change_item_list[0].strip() if change_item_list else ""
    data.update(_extract_budget_with_fallback(tree, data["content_text"]))
    return _ensure_fields(data)


def parse_flow_bid_notice(tree: etree._Element, url: str) -> Dict[str, Any]:
    """流标/废标/终止公告（qtgg），按标题细分类型"""
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    title = title_list[0].strip() if title_list else ""
    if "终止" in title:
        notice_type = "终止公告"
    elif "废标" in title or "流标" in title or "无效" in title:
        notice_type = "流标公告"
    else:
        notice_type = "其他公告"
    data = _extract_common_fields(tree, url, notice_type)

    method_list = tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"采购方式：")]//text()')
    if method_list:
        raw = "".join(method_list).strip()
        m = re.search(r"采购方式[:：](.*?)(?=[，。；\n]|$)", raw)
        if m:
            data["purchase_method"] = m.group(1).strip()

    data.update(_extract_budget_with_fallback(tree, data["content_text"]))

    data["terminate_reason"] = ""
    reason_xpath = '''
    //div[@class="vF_detail_content"]//p[
        contains(.,"项目废标/流标的原因")
        or contains(.,"废标原因")
        or contains(.,"流标原因")
        or contains(.,"项目终止的原因")
        or contains(.,"终止原因")
    ]
    '''
    reason_p_list = tree.xpath(reason_xpath)
    if reason_p_list:
        reason_texts = reason_p_list[0].xpath('./following-sibling::p[1]//text()')
        data["terminate_reason"] = "".join(reason_texts).strip()
        if not data["terminate_reason"]:
            data["terminate_reason"] = "".join(reason_p_list[0].xpath('.//text()')).strip()
    if not data["terminate_reason"]:
        m = re.search(
            r"(?:项目废标/流标的原因|废标原因|流标原因|项目终止的原因|终止原因)[：:]?\s*(.+?)"
            r"(?=三、|四、|其他补充|凡对本次公告|$)",
            data["content_text"], re.DOTALL)
        if m:
            data["terminate_reason"] = m.group(1).strip()[:600]
    return _ensure_fields(data)


def parse_terminate_notice(tree: etree._Element, url: str) -> Dict[str, Any]:
    """终止公告（fblbgg）"""
    data = _extract_common_fields(tree, url, "终止公告")
    method_list = tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"采购方式：")]//text()')
    if method_list:
        raw = "".join(method_list).strip()
        m = re.search(r"采购方式[:：](.*?)(?=[，。；\n]|$)", raw)
        if m:
            data["purchase_method"] = m.group(1).strip()

    data.update(_extract_budget_with_fallback(tree, data["content_text"]))

    data["terminate_reason"] = ""
    reason_p_list = tree.xpath(
        '//div[@class="vF_detail_content"]//p[contains(.,"项目终止的原因") or contains(.,"废标原因") or contains(.,"终止原因")]'
    )
    if reason_p_list:
        reason_texts = reason_p_list[0].xpath('.//text()')
        data["terminate_reason"] = "".join(reason_texts).strip()
    if not data["terminate_reason"]:
        m = re.search(r"项目终止的原因[：:]?\s*(.+?)(?=三、|其他补充|凡对本次公告|$)", data["content_text"], re.DOTALL)
        if m:
            data["terminate_reason"] = m.group(1).strip()[:500]
    return _ensure_fields(data)


def parse_competitive_notice(tree: etree._Element, url: str) -> Dict[str, Any]:
    """竞争性磋商/谈判公告（jzxcs / jzxtpgg），自动从标题识别类型"""
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    title = title_list[0].strip() if title_list else ""
    if "竞争性谈判" in title:
        notice_type = "竞争性谈判公告"
    elif "竞争性磋商" in title:
        notice_type = "竞争性磋商公告"
    else:
        notice_type = "采购公告"

    data = _extract_common_fields(tree, url, notice_type)

    method_list = tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"采购方式：")]//text()')
    if method_list:
        raw = "".join(method_list).strip()
        m = re.search(r"采购方式[:：](.*?)(?=[，。；\n]|$)", raw)
        if m:
            data["purchase_method"] = m.group(1).strip()

    data.update(_extract_budget_with_fallback(tree, data["content_text"]))
    return _ensure_fields(data)


def parse_single_source_notice(tree: etree._Element, url: str) -> Dict[str, Any]:
    """
    单一来源频道（dylygg），按标题区分两种形态：
    - 公示（征求意见）：含 拟定供应商/采用原因/公示期限
    - 采购公告：结构与普通采购公告类似（开标时间、预算等）
    """
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    title = title_list[0].strip() if title_list else ""
    is_publicity = ("公示" in title) or ("征求意见" in title)
    notice_type = "单一来源公示" if is_publicity else "单一来源采购公告"

    data = _extract_common_fields(tree, url, notice_type)
    data["purchase_method"] = "单一来源采购"
    content = data["content_text"]

    data.update(_extract_budget_with_fallback(tree, content))

    if is_publicity:
        # 拟定供应商：结构化「拟定供应商名称：」优先，散文体「拟由XXX，」兜底
        m = re.search(r"拟[定]?供应商名称\s*[：:]\s*([^\n，,。;；]{2,60})", content)
        if not m:
            m = re.search(r"拟由\s*(.{2,50}?)[，,（(]", content)
        data["proposed_supplier"] = m.group(1).strip() if m else ""

        # 单一来源原因说明
        m = re.search(
            r"采用单一来源采购方式的原因(?:及说明)?\s*[：:]?\s*(.+?)"
            r"(?=\n\s*[一二三四五六七八九十]、|公示期限|拟定供应商|附件|$)",
            content, re.DOTALL)
        data["single_source_reason"] = m.group(1).strip()[:600] if m else ""

        # 公示期限：结构化 或 起止日期
        m = re.search(r"公示期限\s*[：:]\s*([^\n]{2,60})", content)
        if not m:
            # 部级征求意见格式：征求意见期限从至 2025年8月7日至2025年8月13日止
            m = re.search(r"征求意见期限[^。；\n]{0,10}?((?:\d{4})[-/年]\d{1,2}[-/月]\d{1,2}日?)"
                          r"\s*[至到]\s*((?:\d{4})[-/年]\d{1,2}[-/月]\d{1,2}日?)", content)
            if m:
                data["publicity_period"] = f"{m.group(1).strip()} 至 {m.group(2).strip()}"
            else:
                m = re.search(r"((?:\d{4})[-/年]\d{1,2}[-/月]\d{1,2}日?)\s*[至到]\s*"
                              r"((?:\d{4})[-/年]\d{1,2}[-/月]\d{1,2}日?)[^。；\n]{0,15}公示", content)
        if not data.get("publicity_period"):
            data["publicity_period"] = m.group(0).strip()[:120] if m else ""
    else:
        # 采购公告形态：提取开标时间
        m = re.search(r"开标时间\s*[：:]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*[\d:：点时分]+)", content)
        data["open_bid_time"] = m.group(1).strip() if m else ""
    return _ensure_fields(data)


def parse_inquiry_notice(tree: etree._Element, url: str) -> Dict[str, Any]:
    """询价公告（xjgg），动态识别类型"""
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    title = title_list[0].strip() if title_list else ""
    raw_type = _extract_raw_notice_type(title)
    notice_type = _normalize_notice_type(raw_type)

    data = _extract_common_fields(tree, url, notice_type)

    data["budget"] = None
    data["budget_source"] = ""
    budget_text_nodes = tree.xpath('//div[@class="table"]//td[text()="预算金额"]/following-sibling::td[1]/text()')
    if budget_text_nodes:
        budget_raw = budget_text_nodes[0].strip()
        data["budget"] = _parse_budget_wan(budget_raw)
        if data["budget"] is not None:
            data["budget_source"] = "table"
    if data["budget"] is None:
        m = re.search(r"预算金额\s*[：:]\s*([\d,]+\.?\d*)\s*万?元", data["content_text"])
        if m:
            data["budget"] = float(m.group(1).replace(",", ""))
            data["budget_source"] = "content"

    method_match = re.search(r"采购方式[:：](.*?)(?=\n|$)", data["content_text"], re.S)
    if method_match:
        data["purchase_method"] = method_match.group(1).strip()
    if "竞价" in raw_type:
        data["purchase_method"] = "框架协议二次竞价"
    return _ensure_fields(data)


# ---------- 类型识别辅助函数 ----------
def _extract_raw_notice_type(title: str) -> str:
    """从标题中提取原始公告类型字符串"""
    title = title or ""
    title = title.strip()
    long_candidates = [
        "框架协议二次竞价公告",
        "中标（成交）结果公告",
        "竞争性磋商公告",
        "竞争性谈判公告",
        "公开招标公告",
        "资格预审公告",
        "单一来源成交公告",
        "单一来源公示",
        "询价公告",
        "招标公告",
        "成交公告",
        "更正公告",
        "变更公告",
        "流标公告",
        "终止公告",
        "采购意向公告",
    ]
    for word in long_candidates:
        if title.endswith(word):
            return word
    idx = title.rfind("公告")
    if idx == -1:
        return ""
    prefix_text = title[:idx]
    collect_chars = []
    for char in reversed(prefix_text):
        if "\u4e00" <= char <= "\u9fff":
            collect_chars.append(char)
            if len(collect_chars) >= 4:
                break
        else:
            break
    collect_chars.reverse()
    word = "".join(collect_chars)
    if len(word) < 2:
        return ""
    return word + "公告"


def _normalize_notice_type(raw_type: str) -> str:
    """归一化为标准公告类型名称"""
    if not raw_type:
        return "未知公告"
    if "竞价" in raw_type:
        return "竞价公告"
    elif "中标" in raw_type or "成交" in raw_type:
        return "成交公告"
    elif "询价" in raw_type:
        return "询价公告"
    elif "竞争性磋商" in raw_type:
        return "竞争性磋商公告"
    elif "竞争性谈判" in raw_type:
        return "竞争性谈判公告"
    elif "公开招标" in raw_type:
        return "公开招标公告"
    elif "招标" in raw_type:
        return "招标公告"
    elif "单一来源公示" in raw_type:
        return "单一来源公示"
    elif "资格预审" in raw_type:
        return "资格预审公告"
    elif "更正" in raw_type:
        return "更正公告"
    elif "变更" in raw_type:
        return "变更公告"
    elif "流标" in raw_type:
        return "流标公告"
    elif "终止" in raw_type:
        return "终止公告"
    elif "采购意向" in raw_type:
        return "采购意向公告"
    return raw_type


# ========================= 统一入口 =========================

PARSER_MAP = {
    "gkzb": parse_tender_notice,
    "zbgg": parse_win_notice,
    "cjgg": parse_deal_notice,
    "gzgg": parse_change_notice,
    "qtgg": parse_flow_bid_notice,
    "jzxcs": parse_competitive_notice,
    "jzxtpgg": parse_competitive_notice,
    "dylygg": parse_single_source_notice,
    "xjgg": parse_inquiry_notice,
    "fblbgg": parse_terminate_notice,
}


def parse_notice(url: str) -> Dict[str, Any]:
    """
    根据 URL 自动识别公告类型并提取所有字段
    :param url: 公告页面 URL
    :return: 包含所有字段的字典
    """
    url = normalize_url(url)
    tree = get_html_tree(url)
    if tree is None:
        return {}

    path_parts = url.split("/")
    try:
        idx_cggg = path_parts.index("cggg")
        if idx_cggg + 2 < len(path_parts):
            dir_name = path_parts[idx_cggg + 2]
        else:
            dir_name = ""
    except ValueError:
        dir_name = ""

    parser = PARSER_MAP.get(dir_name)
    if parser:
        return parser(tree, url)
    else:
        # 降级方案：根据标题动态识别
        title_list = tree.xpath('//h2[@class="tc"]/text()')
        title = title_list[0].strip() if title_list else ""
        raw_type = _extract_raw_notice_type(title)
        notice_type = _normalize_notice_type(raw_type)
        data = _extract_common_fields(tree, url, notice_type)
        data.update(_extract_budget_with_fallback(tree, data["content_text"]))
        return _ensure_fields(data)


# ========================= 批量处理函数 =========================

def batch_parse_from_csv(input_path: str, output_path: str, url_column: str = 'detail_url',
                         sleep_range=(0.3, 0.8)):
    """
    从 CSV 读取 URL 列，逐条解析并实时写入结果。
    支持断点续爬：输出文件已存在的 notice_url 会被跳过。
    """
    sample = _ensure_fields({})
    fieldnames = list(sample.keys())

    # 断点续爬：读取已解析的 URL
    done_urls = set()
    try:
        with open(output_path, 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                u = row.get('notice_url')
                if u:
                    done_urls.add(normalize_url(u))
    except FileNotFoundError:
        pass

    # 统计待处理总数
    with open(input_path, 'r', encoding='utf-8-sig') as f:
        all_urls = [normalize_url(row.get(url_column, "")) for row in csv.DictReader(f)]
    todo_urls = [u for u in all_urls if u and u not in done_urls]
    total_rows = len(todo_urls)
    print(f"待解析 {total_rows} 条（已跳过 {len(done_urls)} 条）")

    processed = 0
    is_new_file = len(done_urls) == 0
    with open(output_path, 'a', encoding='utf-8-sig', newline='') as outf:
        writer = csv.DictWriter(outf, fieldnames=fieldnames)
        if is_new_file:
            writer.writeheader()
            outf.flush()

        for url in todo_urls:
            try:
                data = parse_notice(url)
                if data:
                    data = _ensure_fields(data)
                    writer.writerow(data)
                    outf.flush()
                    processed += 1
                    title_preview = data.get('notice_title', '')[:30]
                    print(f"[{processed}/{total_rows}] ✓ {title_preview}")
                else:
                    print(f"[{processed + 1}/{total_rows}] ✗ {url} 解析返回空数据")
            except Exception as e:
                print(f"[{processed + 1}/{total_rows}] ✗ {url} 解析失败: {e}")
            time.sleep(random.uniform(*sleep_range))

    print(f"✅ 解析完成，共处理 {processed} 条记录，结果已保存至 {output_path}")


# ========================= 命令行入口 =========================

if __name__ == "__main__":
    # 支持命令行参数：python 整合.py input.csv output.csv
    if len(sys.argv) >= 3:
        input_csv = sys.argv[1]
        output_csv = sys.argv[2]
    else:
        input_csv = r"D:\bishe\crawl\ccgp_gzgg.csv"
        output_csv = r"D:\bishe\crawl\ccgp_gzgg_data_v2.csv"
        print(f"未指定文件，使用默认: {input_csv} -> {output_csv}")

    batch_parse_from_csv(input_csv, output_csv)
