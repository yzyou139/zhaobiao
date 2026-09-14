# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 交易结果公示（0104）解析器 v13
- 多段双层表头：表头判断放宽（全 th 或含常见列名）
- rowspan 前向填充
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


FIELDS = [
    "notice_title", "notice_type", "project_code", "publish_time",
    "notice_url", "source", "crawl_time",
    "industry", "admin_region",
    "section_code", "tender_method", "project_name", "construction_scale",
    "buyer_name", "buyer_contact", "buyer_tel",
    "tender_unit", "tender_unit_tel",
    "agent_name", "agent_contact", "agent_tel",
    "project_address", "construction_period",
    "bid_open_time", "bid_open_place",
    "evaluation_method", "bid_scope", "price_ceiling",
    "publicity_period", "objection_contact", "objection_tel", "supervision_tel",
    "qualification_review_json", "rejection_review_json",
    "score_summary_json", "bid_ranking_json",
    "candidate_ranking_json", "review_detail_json",
    "bidder_performance_json", "project_team_json", "bidder_honor_json",
    "other_bidders_json",
    "content_text", "attach_file_names", "attach_json",
]


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


CITY_MAP = {
    "青岛": ("山东省", "青岛市"), "济南": ("山东省", "济南市"),
    "烟台": ("山东省", "烟台市"), "成都": ("四川省", "成都市"),
    "阿坝": ("四川省", "阿坝州"), "金川": ("四川省", "阿坝州"),
    "武汉": ("湖北省", "武汉市"), "西安": ("陕西省", "西安市"),
    "昆明": ("云南省", "昆明市"), "重庆": ("重庆市", ""),
    "北京": ("北京市", ""), "上海": ("上海市", ""), "天津": ("天津市", ""),
}


def extract_admin_region(source: str, title: str) -> str:
    text = (source or "") + " " + (title or "")
    for key, (prov, city) in CITY_MAP.items():
        if key in text:
            return f"{prov}/{city}" if city else prov
    return ""


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


# ========================= 常列名 =========================
COLUMN_NAME_BLACKLIST = {
    "序号", "姓名", "岗位", "编号", "专业", "备注", "预审/投标",
    "投标人名称", "投标单位", "资质等级", "质量", "工期/天",
    "技术标得分", "商务标得分", "报价得分", "总得分",
    "评审结果", "未通过原因", "否决投标原因", "获奖名称",
    "颁奖机构", "获奖时间", "中标候选人及排序", "中标候选人名称",
    "经评审的投标价（元）", "综合评标得分", "综合评估得分或备注",
    "开工日期", "竣工（交工）日期",
    "职务", "证书名称", "证书编号", "职称专业", "级别",
    "项目业主", "项目名称", "建设规模", "合同价格（元）", "项目负责人",
    "技术负责人", "投标报价（元）", "综合评估得分", "评标专家",
    "商务部分得分", "技术部分得分", "报价部分得分", "中标候选人排序",
}


def _is_header_row(cell_infos: List[Dict]) -> bool:
    """判断一行是否是表头：全 th，或含常见列名"""
    if not cell_infos:
        return False
    if all(c["is_th"] for c in cell_infos):
        return True
    for c in cell_infos:
        t = (c.get("text") or "").strip()
        if t in COLUMN_NAME_BLACKLIST:
            return True
    return False


def _merge_two_level_header(h1: List[Dict], h2: List[Dict]) -> List[str]:
    parent_map = {}
    col_idx = 0
    parent_rowspan = {}
    for cell in h1:
        text, rs, cs = cell["text"], cell["rowspan"], cell["colspan"]
        for k in range(cs):
            parent_map[col_idx + k] = text
            if rs > 1:
                parent_rowspan[col_idx + k] = rs - 1
        col_idx += cs

    child_map = {}
    col_idx = 0
    pending = dict(parent_rowspan)
    for cell in h2:
        while col_idx in pending:
            child_map[col_idx] = parent_map.get(col_idx, "")
            pending[col_idx] -= 1
            if pending[col_idx] <= 0:
                del pending[col_idx]
            col_idx += 1
        text, cs = cell["text"], cell["colspan"]
        for k in range(cs):
            child_map[col_idx + k] = text
        col_idx += cs
    while col_idx in pending:
        child_map[col_idx] = parent_map.get(col_idx, "")
        pending[col_idx] -= 1
        if pending[col_idx] <= 0:
            del pending[col_idx]
        col_idx += 1

    max_col = max(max(parent_map.keys(), default=-1),
                  max(child_map.keys(), default=-1)) + 1
    merged = []
    for i in range(max_col):
        p = parent_map.get(i, "")
        c = child_map.get(i, "")
        if p and c and p != c:
            merged.append(f"{p}-{c}")
        else:
            merged.append(c or p)
    return merged


def _parse_merged_title_table(table) -> List[Dict]:
    """多段标题 + 表头 + 数据 + rowspan 前向填充"""
    trs = table.xpath('./tbody/tr | ./tr')
    if not trs:
        return []

    tr_infos = []
    for tr in trs:
        cells = tr.xpath('./td|./th')
        if not cells:
            continue
        cell_infos = []
        for c in cells:
            cell_infos.append({
                "text": extract_text(c),
                "rowspan": int(c.get("rowspan") or 1),
                "colspan": int(c.get("colspan") or 1),
                "is_th": c.tag == "th",
            })
        tr_infos.append({
            "cells": cell_infos,
            "n_cells": len(cells),
            "is_all_th": all(c.tag == "th" for c in cells),
            "is_header": _is_header_row(cell_infos),
        })

    sections = []
    cur = None
    i = 0
    while i < len(tr_infos):
        info = tr_infos[i]

        if info["n_cells"] == 1:
            if cur is not None and cur["header"] and cur["data"]:
                sections.append(cur)
            cur = {"title": info["cells"][0]["text"].strip(),
                   "header": None, "data": []}
            i += 1
            continue

        if cur is None:
            cur = {"title": "", "header": None, "data": []}

        if cur["header"] is None and info["is_header"]:
            if i + 1 < len(tr_infos) and tr_infos[i + 1]["is_header"]:
                cur["header"] = _merge_two_level_header(
                    info["cells"], tr_infos[i + 1]["cells"]
                )
                i += 2
                continue
            else:
                cur["header"] = [c["text"] for c in info["cells"]]
                i += 1
                continue

        cur["data"].append(info)
        i += 1

    if cur is not None and cur["header"] and cur["data"]:
        sections.append(cur)

    result = []
    for sec in sections:
        header = sec["header"]
        n_col = len(header)
        if n_col == 0:
            continue
        data_rows = []
        pending = {}
        for info in sec["data"]:
            row = []
            c_idx = 0
            while c_idx in pending:
                rem, val = pending[c_idx]
                row.append(val)
                rem -= 1
                if rem > 0:
                    pending[c_idx] = [rem, val]
                else:
                    del pending[c_idx]
                c_idx += 1
            for cell in info["cells"]:
                text, rs, cs = cell["text"], cell["rowspan"], cell["colspan"]
                for k in range(cs):
                    row.append(text)
                    if rs > 1:
                        pending[c_idx + k] = [rs - 1, text]
                c_idx += cs
            while c_idx in pending:
                rem, val = pending[c_idx]
                row.append(val)
                rem -= 1
                if rem > 0:
                    pending[c_idx] = [rem, val]
                else:
                    del pending[c_idx]
                c_idx += 1
            if len(row) < n_col:
                row = row + [""] * (n_col - len(row))
            elif len(row) > n_col:
                row = row[:n_col]
            d = {}
            for k, v in zip(header, row):
                if v and k:
                    d[k] = v
            if d:
                data_rows.append(d)

        if data_rows:
            result.append({
                "title": sec["title"],
                "header": header,
                "rows": data_rows,
            })
    return result


# ========================= 结构识别 =========================
def _looks_like_field_name(cell: str) -> bool:
    s = (cell or "").strip("：: ").strip()
    if not s or not (2 <= len(s) <= 25):
        return False
    if not re.search(r"[\u4e00-\u9fa5]", s):
        return False
    if re.fullmatch(r"\d+", s):
        return False
    return s not in COLUMN_NAME_BLACKLIST


def _detect_structure(rows: List[List[str]]) -> Tuple[str, Optional[List[str]]]:
    if not rows:
        return "empty", None
    first = rows[0]
    non_empty = [c for c in first if c]
    if len(first) == 1 and non_empty and 6 <= len(non_empty[0]) <= 60:
        return "merged_title", None
    even_indices = list(range(0, len(first) - 1, 2))
    first_even_ok = all(_looks_like_field_name(first[i]) for i in even_indices) if even_indices else False
    second_first_is_num = len(rows) > 1 and bool(re.fullmatch(r"\d+", (rows[1][0] or "").strip()))
    if first_even_ok and not second_first_is_num:
        return "kv", None
    has_single = any(len(r) == 1 for r in rows)
    if len(first) >= 2 and has_single:
        return "multi_section", first
    return "standard", first


def _parse_kv_rows(rows: List[List[str]]) -> Dict[str, str]:
    result = {}
    for cells in rows:
        for i in range(0, len(cells) - 1, 2):
            k = cells[i].strip("：: ").strip()
            v = cells[i + 1].strip()
            if k and v and k not in result:
                result[k] = v
    return result


def _get_raw_rows(table) -> List[List[str]]:
    rows = []
    for tr in table.xpath('./tbody/tr | ./tr'):
        cells = [extract_text(td) for td in tr.xpath('./td|./th')]
        if any(cells):
            rows.append(cells)
    return rows


def _extract_filled_rows(table, start_idx: int = 0) -> List[List[str]]:
    trs = table.xpath('./tbody/tr | ./tr')
    result = []
    pending = {}
    for r_idx, tr in enumerate(trs):
        cells = tr.xpath('./td|./th')
        if not cells:
            continue
        if r_idx < start_idx:
            c_idx = 0
            for cell in cells:
                rs = int(cell.get("rowspan") or 1)
                cs = int(cell.get("colspan") or 1)
                text = extract_text(cell)
                for k in range(cs):
                    if rs > 1:
                        pending[c_idx + k] = [rs - 1, text]
                c_idx += cs
            continue
        row = []
        c_idx = 0
        for cell in cells:
            while c_idx in pending:
                rem, val = pending[c_idx]
                row.append(val)
                rem -= 1
                if rem > 0:
                    pending[c_idx] = [rem, val]
                else:
                    del pending[c_idx]
                c_idx += 1
            text = extract_text(cell)
            rs = int(cell.get("rowspan") or 1)
            cs = int(cell.get("colspan") or 1)
            for k in range(cs):
                row.append(text)
                if rs > 1:
                    pending[c_idx + k] = [rs - 1, text]
            c_idx += cs
        while c_idx in pending:
            rem, val = pending[c_idx]
            row.append(val)
            rem -= 1
            if rem > 0:
                pending[c_idx] = [rem, val]
            else:
                del pending[c_idx]
            c_idx += 1
        result.append(row)
    return result


def get_all_tables(tree) -> List[Dict[str, Any]]:
    tables = []
    for root_sel in [
        '//div[contains(@class, "detail_content")]',
        '//div[@id="mycontent"]',
    ]:
        roots = tree.xpath(root_sel)
        if not roots:
            continue
        root = roots[0]
        for table in root.xpath('.//table'):
            trs = table.xpath('./tbody/tr | ./tr')
            if len(trs) < 2:
                continue
            all_rows = _get_raw_rows(table)
            if len(all_rows) < 2:
                continue
            structure, main_header = _detect_structure(all_rows)

            if structure == "kv":
                kv = _parse_kv_rows(all_rows)
                tables.append({
                    "title": "", "header": ["key", "value"],
                    "rows": [kv], "structure": "kv", "kv": kv, "raw_rows": all_rows,
                })
                continue

            if structure == "merged_title":
                for sec in _parse_merged_title_table(table):
                    if sec["rows"]:
                        tables.append({
                            "title": sec["title"], "header": sec["header"],
                            "rows": sec["rows"], "structure": "merged_title",
                        })
                continue

            if structure == "multi_section":
                sections = []
                cur = {"title": "", "rows": []}
                for cells in all_rows[1:]:
                    if len(cells) == 1:
                        if cur["rows"]:
                            sections.append(cur)
                        cur = {"title": cells[0], "rows": []}
                    else:
                        row = {}
                        for i, c in enumerate(cells):
                            key = main_header[i] if i < len(main_header) else f"col_{i}"
                            if c:
                                row[key] = c
                        if row:
                            cur["rows"].append(row)
                if cur["rows"]:
                    sections.append(cur)
                for sec in sections:
                    tables.append({
                        "title": sec["title"], "header": main_header,
                        "rows": sec["rows"], "structure": "multi_section",
                        "raw_rows": all_rows,
                    })
                continue

            header = main_header or all_rows[0]
            data_rows_raw = _extract_filled_rows(table, start_idx=1)
            n_col = len(header)
            data_rows = []
            for cells in data_rows_raw:
                if not any(cells):
                    continue
                if len(cells) < n_col:
                    cells = cells + [""] * (n_col - len(cells))
                elif len(cells) > n_col:
                    cells = cells[:n_col]
                row = {}
                for i, c in enumerate(cells):
                    if c:
                        row[header[i]] = c
                if row:
                    data_rows.append(row)
            if data_rows:
                tables.append({
                    "title": "", "header": header, "rows": data_rows,
                    "structure": "standard", "raw_rows": all_rows,
                })
        break
    return tables


def identify_table_type(tbl: Dict[str, Any]) -> Optional[str]:
    header = tbl.get("header") or []
    structure = tbl.get("structure", "standard")
    h = " ".join(header)
    title = tbl.get("title") or ""
    h_all = h + " " + title
    if structure == "kv":
        return None
    if "其他投标人" in h_all or "除中标候选人之外" in h_all:
        return "other_bidders"
    if "中标候选人的评审情况" in h_all:
        return "review_detail"
    if "中标候选人及排序" in h or ("中标候选人名称" in h and "投标报价" in h):
        return "candidate_ranking"
    if "类似业绩" in h_all:
        return "bidder_performance"
    if "项目管理机构" in h_all:
        return "project_team"
    if "评标专家" in h and ("商务部分得分" in h or "技术部分得分" in h):
        return "review_detail"
    if "投标人名称" in h and "评审结果" in h:
        return "qualification_review"
    if "否决投标原因" in h:
        return "rejection_review"
    if "总得分" in h and "技术标" in h:
        return "score_summary"
    if "投标报价" in h and "资质等级" in h:
        return "bid_ranking"
    if "工程规模" in h and "工程造价" in h:
        return "bidder_performance"
    if "获奖名称" in h:
        return "bidder_honor"
    if "姓名" in h and "岗位" in h:
        return "team_member"
    return None


def parse_notice(tree, url: str) -> Dict[str, Any]:
    data = {k: "" for k in FIELDS}
    data["notice_url"] = normalize_url(url)
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["notice_type"] = "交易结果公示"

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
        r"(?:招标项目编号|项目编号|招标编号|采购编号|标段编号)[\s:：]*([A-Za-z0-9\-_]+)",
        po_text
    )
    if m:
        data["project_code"] = m.group(1).strip()

    content = extract_content_text(tree)
    data["content_text"] = content

    tables = get_all_tables(tree)
    log.info(f"[表格] 共提取到 {len(tables)} 个分段表")

    base_info: Dict[str, str] = {}
    for tbl in tables:
        ttype = identify_table_type(tbl)
        title_str = (tbl.get("title") or "")[:25] if tbl.get("title") else "(无标题)"
        hd = " | ".join((tbl.get("header") or [])[:4])
        log.info(f"[表格] {ttype or '?'} | 结构={tbl.get('structure')} | 标题={title_str} | 表头={hd} | {len(tbl.get('rows', []))} 行")

        if tbl.get("structure") == "kv" and tbl.get("kv"):
            for k, v in tbl["kv"].items():
                if k not in base_info:
                    base_info[k] = v
            continue

        json_map = {
            "qualification_review": "qualification_review_json",
            "rejection_review": "rejection_review_json",
            "score_summary": "score_summary_json",
            "bid_ranking": "bid_ranking_json",
            "candidate_ranking": "candidate_ranking_json",
            "review_detail": "review_detail_json",
            "bidder_performance": "bidder_performance_json",
            "project_team": "project_team_json",
            "team_member": "project_team_json",
            "bidder_honor": "bidder_honor_json",
            "other_bidders": "other_bidders_json",
        }
        key_json = json_map.get(ttype)
        if key_json:
            rows_with_title = []
            for r in tbl["rows"]:
                r2 = dict(r)
                if tbl.get("title"):
                    r2["_section"] = tbl["title"]
                rows_with_title.append(r2)
            if data[key_json]:
                old = json.loads(data[key_json])
                data[key_json] = json.dumps(old + rows_with_title, ensure_ascii=False)
            else:
                data[key_json] = json.dumps(rows_with_title, ensure_ascii=False)

    log.info(f"[base_info] 字段: {list(base_info.keys())}")

    if base_info:
        for k in ("标段编号", "标段(包)编号", "标段（包）编号"):
            if base_info.get(k):
                data["section_code"] = base_info[k]
                break
        data["project_name"] = base_info.get("项目名称", "") or base_info.get("项目及标段名称", "")
        data["tender_method"] = base_info.get("招标方式", "")
        data["construction_scale"] = base_info.get("建设规模", "")
        data["buyer_name"] = (
            base_info.get("建设单位", "") or base_info.get("项目业主", "")
            or base_info.get("招标人", "") or base_info.get("招标单位", "")
        )
        data["buyer_contact"] = base_info.get("联系人", "")
        data["buyer_tel"] = (
            base_info.get("联系电话", "") or base_info.get("项目业主联系电话", "")
            or base_info.get("招标人联系电话", "")
        )
        data["tender_unit"] = base_info.get("招标单位", "") or base_info.get("招标人", "")
        data["tender_unit_tel"] = base_info.get("招标人联系电话", "")
        data["agent_name"] = base_info.get("招标代理单位", "") or base_info.get("招标代理机构", "")
        data["agent_contact"] = base_info.get("代理机构联系人", "")
        data["agent_tel"] = (
            base_info.get("招标代理机构联系电话", "") or base_info.get("代理机构联系电话", "")
        )
        data["project_address"] = base_info.get("工程地址", "")
        data["construction_period"] = base_info.get("工期", "")
        data["bid_open_place"] = base_info.get("开标地点", "")
        data["evaluation_method"] = base_info.get("评标办法", "")
        data["bid_scope"] = base_info.get("许可范围", "")
        for k in ("投标最高限价（元）", "投标最高限价(元)", "最高限价"):
            if base_info.get(k):
                data["price_ceiling"] = base_info[k]
                break
        if base_info.get("开标时间"):
            data["bid_open_time"] = parse_datetime(base_info["开标时间"])

    if not data.get("publicity_period") and base_info.get("公示期"):
        data["publicity_period"] = base_info["公示期"]
    if not data.get("publicity_period"):
        m = re.search(r"公示期[：:]?\s*(\d{4}[-年]\d{1,2}[-月]\d{1,2}[日]?\s*\d{1,2}[:：]\d{2}(?::\d{2})?\s*至\s*\d{4}[-年]\d{1,2}[-月]\d{1,2}[日]?\s*\d{1,2}[:：]\d{2}(?::\d{2})?)", content)
        if m:
            data["publicity_period"] = m.group(1).strip()
    if not data.get("publicity_period"):
        m = re.search(r"公示(?:期|时间)[：:]?\s*(\d{4}年\d{1,2}月\d{1,2}日[-至到]\d{4}年\d{1,2}月\d{1,2}日)", content)
        if m:
            data["publicity_period"] = m.group(1)

    m = re.search(r"异议受理[：:]?\s*联系人[：:]\s*([^，,。\n]+)", content)
    if m:
        data["objection_contact"] = m.group(1).strip()
    m = re.search(r"异议受理.*?联系电话[：:]\s*([\d\-/]+)", content)
    if m:
        data["objection_tel"] = m.group(1).strip()
    m = re.search(r"行政监督投诉电话\s*[：:]?\s*([\d\-]+)", content)
    if m:
        data["supervision_tel"] = m.group(1).strip()
    if not data.get("supervision_tel"):
        m = re.search(r"(?:监督电话|联系电话)[：:]\s*(\d{7,12})", content)
        if m:
            data["supervision_tel"] = m.group(1).strip()

    if not data["project_code"] and data.get("section_code"):
        data["project_code"] = data["section_code"]

    data["industry"] = classify_industry(data["notice_title"], content)
    data["admin_region"] = extract_admin_region(data["source"], data["notice_title"])

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


def parse_batch(urls, out_csv: str = "jieguo.csv", sleep_range=(1.0, 2.0)):
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
        "https://www.ggzy.gov.cn/information/deal/html/b/370000/0104/20260910/00374526703bb58b4ed091fbf35f62beb637.html",
        "https://www.ggzy.gov.cn/information/deal/html/a/510000/0104/20260910/00515af18d7b60dd496a8ab59a39dc832286.html",
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