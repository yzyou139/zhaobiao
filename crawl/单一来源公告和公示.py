from curl_cffi import requests
from lxml import etree
import re
import datetime


def extract_single_source_publicity_data(tree, url):
    """
    单一来源公示 dylygg 目录
    采购前公示，未成交；区分于【单一来源成交公告】
    """
    data = {}
    # 标题
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    data["notice_title"] = title_list[0].strip() if title_list else ""
    data["notice_type"] = "单一来源公示"

    # 项目编号
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

    data["purchase_method"] = "单一来源采购"

    # =====================预算容错模块（复制复用）=====================
    data["budget"] = None
    data["budget_source"] = ""
    SUSPICIOUS_THRESHOLD = 0.1

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
            if content_budget is not None:
                data["budget"] = content_budget
                data["budget_source"] = "content"
            else:
                data["budget"] = table_budget
                data["budget_source"] = "table_suspicious"
        else:
            data["budget"] = table_budget
            data["budget_source"] = "table"
    else:
        if content_budget is not None:
            data["budget"] = content_budget
            data["budget_source"] = "content"
        else:
            data["budget"] = None
            data["budget_source"] = "default"
    # =================================================================

    # 【单一来源公示特有字段】
    # 1.拟定供应商名称
    proposed_supp_text = "".join(tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"名称：") and contains(.,"拟定供应商")]//text()'))
    m_sup = re.search(r"名称[:：](.*?)(?=地址|$|\n)", proposed_supp_text)
    data["proposed_supplier"] = m_sup.group(1).strip() if m_sup else ""

    # 2.单一来源理由
    reason_text = "".join(tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"采用单一来源采购方式的原因及说明")]//text()'))
    m_reason = re.search(r"采用单一来源采购方式的原因及说明[:：](.*?)(?=二、|三、|$)", reason_text, re.DOTALL)
    data["single_source_reason"] = m_reason.group(1).strip()[:600] if m_reason else ""

    # 3.公示期限
    publicity_text = "".join(tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"公示期限")]//text()'))
    m_period = re.search(r"公示期限[:：](.*?)(?=四、|$)", publicity_text, re.DOTALL)
    data["publicity_period"] = m_period.group(1).strip() if m_period else ""

    # 通用空字段（继承统一数据表结构）
    data["terminate_reason"] = ""
    data["change_item"] = ""
    data["win_total_amount"] = None
    data["judge_experts"] = ""
    data["agent_service_fee"] = None
    data["win_supplier"] = ""

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
    test_url = "https://www.ccgp.gov.cn/cggg/dfgg/dylygg/202609/t20260909_27292715.htm"
    tree = get_html_tree(test_url)
    if tree is not None:
        res = extract_single_source_publicity_data(tree, test_url)
        for k, v in res.items():
            print(f"{k}: {v}")
