from curl_cffi import requests
from lxml import etree
import re
import datetime


def extract_flow_bid_notice_data(tree, url):
    """
    流标公告解析函数（qtgg目录）
    字段：notice_title,notice_type,project_code,publish_time,admin_region,purchase_method,budget,terminate_reason,buyer_name,buyer_address,buyer_contact,agent_name,agent_address,agent_contact,notice_url,attach_file_names,crawl_time
    """
    data = {}
    # 公告标题
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    data["notice_title"] = title_list[0].strip() if title_list else ""
    data["notice_type"] = "流标公告"

    # 项目编号：兼容 项目编号 / 采购项目编号
    data["project_code"] = ""
    code_xpath = '''
    //div[@class="vF_detail_content"]//p[
        .//strong[contains(text(),"项目编号：") or contains(text(),"采购项目编号：")]
        or contains(text(),"项目编号：") or contains(text(),"采购项目编号：")
    ]//text()
    '''
    code_list = tree.xpath(code_xpath)
    if code_list:
        raw_str = "".join(code_list).strip()
        match_code = re.search(r"(采购项目编号|项目编号)[:：](.*?)(?=\（|$|\n)", raw_str)
        if match_code:
            data["project_code"] = match_code.group(2).strip()

    # 发布时间
    pub_list = tree.xpath('//span[@id="pubTime"]/text()')
    data["publish_time"] = pub_list[0].strip() if pub_list else ""

    # 行政区域
    region_list = tree.xpath('//td[text()="行政区域"]/following-sibling::td[1]/text()')
    data["admin_region"] = region_list[0].strip() if region_list else ""

    # 采购方式
    data["purchase_method"] = ""
    method_list = tree.xpath('//div[@class="vF_detail_content"]//p[.//strong[contains(text(),"采购方式：")]]//text()')
    if not method_list:
        method_list = tree.xpath('//div[@class="vF_detail_content"]//p[contains(text(),"采购方式：")]/text()')
    if method_list:
        raw = "".join(method_list).strip()
        m = re.search(r"采购方式[:：](.*?)(?=[，。；\n]|$)", raw)
        if m:
            data["purchase_method"] = m.group(1).strip()

    # 预算金额
    data["budget"] = None
    budget_raw_list = tree.xpath('//td[text()="预算金额"]/following-sibling::td[1]/text()')
    if budget_raw_list:
        budget_raw = budget_raw_list[0].strip()
        match = re.search(r"([\d.]+)", budget_raw)
        if match:
            data["budget"] = float(match.group(1))
    if data["budget"] is None:
        budget_p_list = tree.xpath('//div[@class="vF_detail_content"]//p[contains(text(),"预算金额：")]//text()')
        if budget_p_list:
            raw = "".join(budget_p_list)
            match = re.search(r"([\d.]+)\s*万元", raw)
            if match:
                data["budget"] = float(match.group(1))

    # 流标原因
    data["terminate_reason"] = ""
    reason_xpath = '''
    //div[@class="vF_detail_content"]//p[
        contains(text(),"项目废标/流标的原因")
        or contains(text(),"废标原因")
        or contains(text(),"流标原因")
    ]
    '''
    reason_p_list = tree.xpath(reason_xpath)
    if reason_p_list:
        # 取标题段落的下一个p标签作为原因正文
        reason_texts = reason_p_list[0].xpath('./following-sibling::p[1]//text()')
        data["terminate_reason"] = "".join(reason_texts).strip()
    # 兜底正则全文匹配
    if not data["terminate_reason"]:
        all_p = tree.xpath('//div[@class="vF_detail_content"]//p//text()')
        full_text = "".join(all_p)
        m = re.search(r"(项目废标/流标的原因|废标原因|流标原因)[：:]?\s*(.*?)(?=三、|其他补充|凡对本次公告|$)", full_text, re.DOTALL)
        if m:
            data["terminate_reason"] = m.group(2).strip()[:600]

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

    # 附件名称
    attach_name_list = tree.xpath('//a[@class="bizDownload"]/text()')
    data["attach_file_names"] = ",".join([name.strip() for name in attach_name_list]) if attach_name_list else ""

    # 抓取时间
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return data


def get_flow_bid_html(url):
    """请求页面，返回lxml解析树"""
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
    test_url = "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202609/t20260909_27292398.htm"
    tree = get_flow_bid_html(test_url)
    if tree is not None:
        res = extract_flow_bid_notice_data(tree, test_url)
        for k, v in res.items():
            print(f"{k}: {v}")
