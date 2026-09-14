from curl_cffi import requests
from lxml import etree
import re
import datetime


def extract_negotiation_notice_data(tree, url):
    """
    兼容：竞争性磋商公告(jzxcs)、竞争性谈判公告(jzxtpgg)，中央公告 + 地方公告
    增加预算容错校验，自动识别表格小数点错位脏数据
    """
    data = {}
    # 标题
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    data["notice_title"] = title_list[0].strip() if title_list else ""

    # 自动识别类型：竞争性谈判 / 竞争性磋商
    title_raw = data["notice_title"]
    if "竞争性谈判" in title_raw:
        data["notice_type"] = "竞争性谈判公告"
    elif "竞争性磋商" in title_raw:
        data["notice_type"] = "竞争性磋商公告"
    else:
        data["notice_type"] = "采购公告"

    # 项目编号：兼容项目编号 / 采购项目编号
    code_xpath = '''
    //div[@class="vF_detail_content"]//p[
        contains(.,"项目编号：") or contains(.,"采购项目编号：")
    ]//text()
    '''
    code_list = tree.xpath(code_xpath)
    data["project_code"] = ""
    if code_list:
        raw_str = "".join(code_list).strip()
        match_code = re.search(r"(采购项目编号|项目编号)[:：](.*?)(?=\（|$|\n)", raw_str)
        if match_code:
            data["project_code"] = match_code.group(2).strip()

    pub_list = tree.xpath('//span[@id="pubTime"]/text()')
    data["publish_time"] = pub_list[0].strip() if pub_list else ""

    region_list = tree.xpath('//td[text()="行政区域"]/following-sibling::td[1]/text()')
    data["admin_region"] = region_list[0].strip() if region_list else ""

    # 采购方式（正文提取）
    data["purchase_method"] = ""
    method_list = tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"采购方式：")]//text()')
    if method_list:
        raw = "".join(method_list).strip()
        m = re.search(r"采购方式[:：](.*?)(?=[，。；\n]|$)", raw)
        if m:
            data["purchase_method"] = m.group(1).strip()

    # =====================预算容错模块开始=====================
    data["budget"] = None
    data["budget_source"] = ""
    SUSPICIOUS_THRESHOLD = 0.1  # 小于0.1万判定为可疑，可修改

    # 来源A：正文（元 → 转万元）
    content_budget = None
    budget_p_list = tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"预算金额：")]//text()')
    if budget_p_list:
        raw = "".join(budget_p_list)
        m = re.search(r"预算金额[:：]\s*([\d.]+)\s*元", raw)
        if m:
            yuan_val = float(m.group(1))
            content_budget = yuan_val / 10000

    # 来源B：概要表格（单位万元）
    table_budget = None
    budget_raw_list = tree.xpath('//td[text()="预算金额"]/following-sibling::td[1]/text()')
    if budget_raw_list:
        budget_raw = budget_raw_list[0].strip()
        match = re.search(r"([\d.]+)", budget_raw)
        if match:
            table_budget = float(match.group(1))

    # 容错决策
    if table_budget is not None:
        if 0 < table_budget < SUSPICIOUS_THRESHOLD:
            # 金额过小，触发校验，优先正文
            if content_budget is not None:
                data["budget"] = content_budget
                data["budget_source"] = "content"
            else:
                data["budget"] = table_budget
                data["budget_source"] = "table_suspicious"
        else:
            # 表格金额正常，直接使用表格
            data["budget"] = table_budget
            data["budget_source"] = "table"
    else:
        # 表格无数据，取正文
        if content_budget is not None:
            data["budget"] = content_budget
            data["budget_source"] = "content"
        else:
            data["budget"] = None
            data["budget_source"] = "default"
    # =====================预算容错模块结束=====================



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

    data["notice_url"] = url
    attach_name_list = tree.xpath('//a[@class="bizDownload"]/text()')
    data["attach_file_names"] = ",".join([name.strip() for name in attach_name_list]) if attach_name_list else ""
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return data


def get_html_tree(url):
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=20)
        resp.raise_for_status()
        html = resp.text
        tree = etree.HTML(html)
        return tree
    except Exception as e:
        print(f"请求异常：{e}")
        return None


if __name__ == "__main__":
    test_url = "https://www.ccgp.gov.cn/cggg/dfgg/jzxtpgg/202609/t20260909_27292745.htm"
    tree = get_html_tree(test_url)
    if tree is not None:
        res = extract_negotiation_notice_data(tree, test_url)
        for k, v in res.items():
            print(f"{k}: {v}")
