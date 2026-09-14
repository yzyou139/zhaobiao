# -*- coding: utf-8 -*-
"""
ggzy.gov.cn 公告详情爬虫
输出字段与《字段说明.csv》一致
"""

import os
import re
import csv
import json
import time
import random
import logging
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ============== 配置 ==============
DETAIL_URL = "https://www.ggzy.gov.cn/information/deal/html/b/150000/0302/20260910/00158c5d2e5515bb4bdbb78f2dd7a19778fe.html"
OUT_CSV = "ggzy_notice.csv"
OUT_JSON = "ggzy_notice.json"

# 字段顺序（与 字段说明.csv 一致）
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

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ggzy")


# ============== 工具函数 ==============
def clean(s):
    """去除多余空白、&nbsp;"""
    if s is None:
        return None
    s = str(s).replace("\xa0", " ").replace("\u3000", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def one_line(s):
    """压成一行，用于标题/地址等"""
    if s is None:
        return None
    return re.sub(r"\s+", " ", str(s).replace("\xa0", " ")).strip()


def find1(pat, text, flags=re.S):
    """正则取第一组"""
    if not text:
        return None
    m = re.search(pat, text, flags)
    if not m:
        return None
    return one_line(m.group(1))


def extract_block(text, start_pat, end_pats):
    """从 start_pat 到 end_pats 之一之间的文本"""
    if not text:
        return ""
    end_alt = "|".join(end_pats) if end_pats else "$"
    m = re.search(start_pat + r"(.*?)(?:" + end_alt + r"|$)", text, re.S)
    return m.group(1) if m else ""


def to_wan(value_str):
    """把金额字符串转成万元 float"""
    if value_str is None:
        return None
    s = str(value_str).replace(",", "").replace("，", "").strip()
    m = re.search(r"([\d.]+)\s*(万元|元)?", s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    if m.group(2) == "元":
        v = v / 10000.0
    return round(v, 6)


def url_normalize(url):
    """去掉 /./ 片段"""
    if not url:
        return url
    return re.sub(r"/\./", "/", url)


# ============== 抓取 ==============
def fetch(url, referer=None, retries=3, timeout=20):
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    for i in range(retries):
        try:
            r = requests.get(url, headers=h, timeout=timeout)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as e:
            log.warning("请求失败 (%s/%s): %s", i + 1, retries, e)
            time.sleep(1.5 * (i + 1) + random.random())
    return None


# ============== 字段解析 ==============
def parse_notice_type(url, title):
    """先按 URL 目录判断，标题辅助"""
    m = re.search(r"/deal/html/[a-z]/\d+/(\d{4})/", url or "")
    code = m.group(1) if m else ""
    t = title or ""

    if code == "9001":
        if "更正" in t or "变更" in t:
            return "更正公告"
        if "终止" in t or "废标" in t or "流标" in t:
            return "终止公告"
        if "中标" in t:
            return "中标公告"
        if "成交" in t:
            return "成交公告"
        if "单一来源" in t:
            return "单一来源公示"
        if "公开招标" in t:
            return "公开招标公告"
        if "邀请招标" in t:
            return "邀请招标公告"
        if "竞争性磋商" in t:
            return "竞争性磋商公告"
        if "竞争性谈判" in t:
            return "竞争性谈判公告"
        if "询价" in t:
            return "询价公告"
        return "招标公告"
    if code == "9002":
        if "中标" in t:
            return "中标公告"
        if "成交" in t:
            return "成交公告"
        return "成交公示"

    # URL 无有效目录时，用标题兜底
    for kw in ("更正公告", "终止公告", "中标公告", "成交公告",
               "公开招标公告", "竞争性磋商公告", "竞争性谈判公告",
               "询价公告", "单一来源公示"):
        if kw in t:
            return kw
    return "其他公告"


INDUSTRY_RULES = [
    ("医疗卫生", ["医疗", "医院", "药品", "疫苗", "器械", "卫生", "药械", "中医", "疾控"]),
    ("信息技术", ["信息化", "软件", "系统", "数据", "网络", "云计算", "服务器",
                  "大数据", "人工智能", "平台建设", "信息", "IT", "电子"]),
    ("公共安全", ["公安", "消防", "应急", "安防", "监控", "治安", "警务"]),
    ("交通运输", ["交通", "公路", "铁路", "机场", "港口", "轨道", "地铁", "道路", "桥梁"]),
    ("水利水电", ["水利", "水电", "水务", "水库", "河道", "供水", "排水", "污水"]),
    ("能源电力", ["电力", "电网", "风电", "光伏", "燃气", "能源", "热力", "新能源"]),
    ("建筑工程", ["建筑", "施工", "工程", "监理", "勘察", "设计", "装修", "房建", "市政"]),
    ("环境保护", ["环保", "环境", "排放", "垃圾", "固废", "生态", "污染"]),
    ("教育科研", ["教育", "学校", "学院", "大学", "科研", "实验", "图书"]),
    ("农林牧渔", ["农业", "农村", "林业", "畜牧", "渔业", "农田", "种子", "肥料"]),
    ("社会保障", ["社保", "民政", "养老", "福利", "残疾人", "救助"]),
    ("文化体育", ["文化", "体育", "旅游", "文物", "艺术", "广电"]),
    ("其他", []),
]


def parse_industry(title, content_text):
    text = (title or "") + " " + (content_text or "")[:600]
    if not text.strip():
        return "其他"
    for name, kws in INDUSTRY_RULES:
        for kw in kws:
            if kw in text:
                return name
    return "其他"


REGION_PROVINCES = [
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
]


def parse_admin_region(title, buyer_address, agent_address, content_text):
    """优先从采购人地址推，其次标题，再兜底正文前部"""
    def find_region(text):
        if not text:
            return None
        for prov in REGION_PROVINCES:
            if prov in text:
                # 尝试抓省+市：省 市 xx
                m = re.search(prov + r"(?:省|市|自治区|特别行政区)?([\u4e00-\u9fa5]{2,8}?市)", text)
                if m:
                    return f"{prov}省/{m.group(1)}" if prov not in ("北京", "天津", "上海", "重庆") else prov
                return prov if prov in ("北京", "天津", "上海", "重庆") else prov + "省"
        return None

    for src in (buyer_address, agent_address, title, (content_text or "")[:200]):
        r = find_region(src)
        if r:
            return r
    return None


def parse_detail(html, url):
    result = {k: None for k in FIELDS}
    result["notice_url"] = url_normalize(url)
    result["crawl_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    result["budget_source"] = "default"

    soup = BeautifulSoup(html, "html.parser")
    detail = soup.select_one("div.detail") or soup

    # --- 标题 ---
    title_el = detail.select_one("h4.h4_o") or detail.select_one("h2.tc")
    title = one_line(title_el.get_text()) if title_el else None
    result["notice_title"] = title

    # --- 发布时间 ---
    p_o = detail.select_one("p.p_o")
    p_o_text = clean(p_o.get_text(" ", strip=True)) if p_o else ""
    publish_time = find1(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2}(\s+\d{2}:\d{2}(:\d{2})?)?)", p_o_text)
    if not publish_time:
        pub_el = detail.select_one("span#pubTime")
        publish_time = one_line(pub_el.get_text()) if pub_el else None
    result["publish_time"] = publish_time

    # --- 来源平台 ---
    src_el = detail.select_one("label#platformName")
    if not src_el:
        src_el = detail.select_one("span#platformName")
    result["source"] = one_line(src_el.get_text()) if src_el else None

    # --- 正文 ---
    content_el = (detail.select_one("#mycontent .detail_content")
                  or detail.select_one("#mycontent")
                  or detail.select_one(".detail_content"))
    if content_el:
        # 保留段落结构
        raw = content_el.get_text("\n", strip=True).replace("\xa0", " ")
    else:
        raw = detail.get_text("\n", strip=True).replace("\xa0", " ")
    content_text = clean(raw)
    result["content_text"] = content_text

    # --- 公告类型 ---
    result["notice_type"] = parse_notice_type(url, title)

    # --- 项目编号 ---
    result["project_code"] = find1(
        r"(?:项目编号|招标编号|采购编号|项目代码|采购计划编号|项目采购编号)"
        r"[：:]\s*([A-Za-z0-9\-_/（）()【】\u4e00-\u9fa5]{3,80})",
        content_text,
    )

    # --- 预算金额 ---
    budget_str = find1(
        r"(?:预算金额|预算总金额|采购预算|本项目预算|预算价)"
        r"[：:]\s*([\d,.]+\s*万?元)",
        content_text,
    )
    if not budget_str:
        budget_str = find1(
            r"(?:预算金额|预算总金额|采购预算|本项目预算)"
            r"[^\d]{0,10}([\d,.]+)\s*万元",
            content_text,
        )
    if budget_str:
        result["budget"] = to_wan(budget_str)
        if result["budget"] is not None:
            result["budget_source"] = "content"

    # --- 递交截止时间 ---
    result["bid_deadline"] = find1(
        r"(?:递交截止时间|投标截止时间|响应文件(?:递交|提交)?截止时间|"
        r"投标文件递交截止时间|响应文件提交截止时间|响应截止时间)"
        r"[：:]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*\d{1,2}[:：]\d{2}(?::\d{2})?)",
        content_text,
    )

    # --- 开标时间 ---
    result["open_bid_time"] = find1(
        r"开标时间[：:]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*\d{1,2}[:：]\d{2}(?::\d{2})?)",
        content_text,
    )

    # --- 采购方式 ---
    pm = find1(
        r"采购方式[：:]\s*(公开招标|邀请招标|竞争性磋商|竞争性谈判|"
        r"询价|单一来源采购|单一来源|框架协议|其他)",
        content_text,
    )
    if result["notice_type"] == "单一来源公示" and not pm:
        pm = "单一来源采购"
    result["purchase_method"] = pm

    # --- 采购人块 ---
    buyer_block = extract_block(
        content_text,
        r"(?:1[、.]\s*)?采购人(?:信息)?[：:]",
        [r"(?:2[、.]\s*)?(?:采购)?代理机构(?:信息)?[：:]", r"九、", r"八、"],
    )
    if not buyer_block:
        buyer_block = extract_block(
            content_text,
            r"采购人信息[：:]",
            [r"采购代理机构信息[：:]", r"代理机构信息[：:]"],
        )

    # --- 代理机构块 ---
    agent_block = extract_block(
        content_text,
        r"(?:2[、.]\s*)?(?:采购)?代理机构(?:信息)?[：:]",
        [r"九、", r"十、", r"其他说明", r"发布公告的媒介"],
    )

    # --- 采购人字段 ---
    result["buyer_name"] = (
        find1(r"名称[：:]\s*([^\n]+)", buyer_block)
        or find1(r"采购人(?:名称)?[：:]\s*([^\n]+)", content_text)
    )
    result["buyer_address"] = find1(r"地址[：:]\s*([^\n]+)", buyer_block)
    result["buyer_contact"] = (
        find1(r"联系电话[：:]\s*([0-9\-—\-、，,;；/ ]+)", buyer_block)
        or find1(r"电话[：:]\s*([0-9\-—\-、，,;；/ ]+)", buyer_block)
    )

    # --- 代理机构字段 ---
    result["agent_name"] = (
        find1(r"名称[：:]\s*([^\n]+)", agent_block)
        or find1(r"代理机构(?:名称)?[：:]\s*([^\n]+)", content_text)
    )
    result["agent_address"] = find1(r"地址[：:]\s*([^\n]+)", agent_block)
    result["agent_contact"] = (
        find1(r"联系电话[：:]\s*([0-9\-—\-、，,;；/ ]+)", agent_block)
        or find1(r"电话[：:]\s*([0-9\-—\-、，,;；/ ]+)", agent_block)
    )

    # --- 项目联系人 ---
    # 只取中文名，避免把电话混进来
    contact_name = find1(r"项目联系人[：:]\s*([\u4e00-\u9fa5]{2,8})", content_text)
    if not contact_name:
        agent_contact_name = find1(r"联系人[：:]\s*([\u4e00-\u9fa5]{2,8})", agent_block)
        contact_name = agent_contact_name
    result["contact_name"] = contact_name

    contact_tel = find1(
        r"项目联系电话[：:]\s*([0-9\-—\-、，,;；/ ]+)",
        content_text,
    )
    if not contact_tel:
        contact_tel = result["agent_contact"] or result["buyer_contact"]
    result["contact_tel"] = contact_tel

    # --- 行政区域 ---
    result["admin_region"] = parse_admin_region(
        title, result["buyer_address"], result["agent_address"], content_text
    )

    # --- 行业 ---
    result["industry"] = parse_industry(title, content_text)

    # --- 评审专家 ---
    result["judge_experts"] = find1(
        r"(?:评审专家|评标专家|磋商小组|谈判小组成员|评标委员会成员)"
        r"[：:]\s*([^\n]{2,200})",
        content_text,
    )

    # --- 代理服务费 ---
    fee_str = find1(
        r"(?:代理服务费|招标代理服务费|采购代理服务费|中标服务费)"
        r"[：: ]?\s*(?:金额)?[^\d]{0,10}([\d,.]+\s*万?元)",
        content_text,
    )
    if fee_str:
        result["agent_service_fee"] = to_wan(fee_str)

    # --- 更正事项 ---
    if result["notice_type"] == "更正公告":
        result["change_item"] = find1(
            r"(?:更正(?:内容|事项)|变更(?:内容|事项))[：:]\s*([^\n]{2,500})",
            content_text,
        ) or extract_block(content_text, r"(?:更正内容|变更内容)[：:]", [r"三、", r"四、", r"五、"])[:500] or None

    # --- 终止原因 ---
    if result["notice_type"] == "终止公告":
        result["terminate_reason"] = find1(
            r"(?:终止原因|废标原因|流标原因|终止/废标原因|项目终止的原因)"
            r"[：: ]?\s*([^\n]{2,300})",
            content_text,
        )

    # --- 单一来源相关 ---
    if result["notice_type"] == "单一来源公示" or "单一来源" in (title or ""):
        result["proposed_supplier"] = (
            find1(r"(?:拟定|拟确定)供应商(?:名称)?[：:]\s*([^\n]{2,200})", content_text)
            or find1(r"拟由\s*([\u4e00-\u9fa5A-Za-z0-9（）()]{3,80})\s*(?:提供|承担|实施)", content_text)
        )
        result["single_source_reason"] = find1(
            r"(?:采用单一来源(?:采购)?(?:方式)?的?理由|单一来源理由|采用单一来源采购方式的原因(?:及说明)?)"
            r"[：: ]?\s*([^\n]{5,500})",
            content_text,
        )

    # --- 公示期限 ---
    publicity = find1(
        r"(?:征求意见期限|公示期限|公示期)(?:从|自)?\s*"
        r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*(?:至|到|-)\s*"
        r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)",
        content_text,
    )
    if not publicity:
        publicity = find1(
            r"(?:公示期限|征求意见期限)[：: ]?\s*([^\n]{4,120})",
            content_text,
        )
    result["publicity_period"] = publicity

    # --- 中标/成交信息 ---
    if result["notice_type"] in ("中标公告", "成交公告", "成交公示"):
        parse_win_info(detail, content_text, result)

    # --- 附件 ---
    parse_attachments(detail, url, result)

    return result


def parse_win_info(detail, content_text, result):
    """中标/成交公告：供应商、金额、明细"""
    # 优先从表格里抓
    win_rows = []
    for table in detail.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_texts = [one_line(th.get_text()) for th in rows[0].find_all(["th", "td"])]
        header_join = " ".join(h or "" for h in header_texts)
        # 判断是不是中标明细表
        if not any(k in header_join for k in ("中标", "成交", "供应商", "金额", "包号")):
            continue
        for tr in rows[1:]:
            tds = tr.find_all(["td", "th"])
            if not tds:
                continue
            row = [one_line(td.get_text()) for td in tds]
            if not any(row):
                continue
            win_rows.append({
                "header": header_texts,
                "row": row,
            })

    win_suppliers = []
    win_amounts = []
    detail_json = []

    for item in win_rows:
        header = item["header"]
        row = item["row"]
        rec = {}
        for h, v in zip(header, row):
            if h:
                rec[h] = v
        detail_json.append(rec)

        for h, v in rec.items():
            if not v:
                continue
            if "供应商" in h or "中标人" in h or "成交人" in h or "中标单位" in h:
                win_suppliers.append(v)
            if "金额" in h or "价格" in h or "中标价" in h or "成交价" in h:
                amt = to_wan(v)
                if amt is not None:
                    win_amounts.append(amt)

    # 正则兜底
    if not win_suppliers:
        sup = find1(
            r"(?:中标|成交)(?:供应商|单位|人|公司)(?:名称)?[：:]\s*([^\n]{2,200})",
            content_text,
        )
        if sup:
            win_suppliers.append(sup)

    if not win_amounts:
        amt_str = find1(
            r"(?:中标|成交)(?:总)?金额[：: ]?\s*([\d,.]+\s*万?元)",
            content_text,
        )
        if amt_str:
            v = to_wan(amt_str)
            if v is not None:
                win_amounts.append(v)

    if win_suppliers:
        # 去重保序
        seen = set()
        uniq = []
        for s in win_suppliers:
            s2 = s.strip()
            if s2 and s2 not in seen:
                seen.add(s2)
                uniq.append(s2)
        result["win_supplier"] = "，".join(uniq)

    if win_amounts:
        result["win_total_amount"] = round(sum(win_amounts), 6)

    if detail_json:
        result["win_detail_json"] = json.dumps(detail_json, ensure_ascii=False)


def parse_attachments(detail, url, result):
    """附件：a.bizDownload + @id 构造 OSS 直链"""
    attach = []
    seen_urls = set()

    for a in detail.select("a.bizDownload"):
        name = one_line(a.get_text())
        href = a.get("href") or a.get("data-url") or a.get("url")
        full = urljoin(url, href) if href else None
        if not full:
            continue
        if full in seen_urls:
            continue
        seen_urls.add(full)
        attach.append({"attach_name": name, "attach_url": full})

    # 通过 @id 构造 OSS 直链（不同站点规则不同，这里给出通用尝试）
    for el in detail.select("[id]"):
        el_id = el.get("id") or ""
        if re.fullmatch(r"[0-9a-fA-F\-]{20,}", el_id):
            oss_url = f"https://download.ggzy.gov.cn/{el_id}"
            if oss_url not in seen_urls:
                seen_urls.add(oss_url)
                name = one_line(el.get_text()) or el_id
                attach.append({"attach_name": name, "attach_url": oss_url})

    if attach:
        names = [x["attach_name"] for x in attach if x["attach_name"]]
        result["attach_file_names"] = "，".join(names)
        result["attach_json"] = json.dumps(attach, ensure_ascii=False)


# ============== 输出 ==============
def save_csv(rows, path):
    if not rows:
        return
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def save_json(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


# ============== 主流程 ==============
def main():
    # 1. 抓详情页
    log.info("抓取详情页: %s", DETAIL_URL)
    html = fetch(DETAIL_URL, referer=DETAIL_URL)
    if not html:
        log.error("详情页抓取失败")
        return

    # 2. 解析
    data = parse_detail(html, DETAIL_URL)

    # 3. 打印
    print(json.dumps(data, ensure_ascii=False, indent=2))

    # 4. 保存
    save_csv([data], OUT_CSV)
    save_json([data], OUT_JSON)
    log.info("已写入 %s / %s", OUT_CSV, OUT_JSON)


if __name__ == "__main__":
    main()