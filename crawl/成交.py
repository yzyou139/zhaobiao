from curl_cffi import requests
from lxml import etree
import re
import datetime


def extract_deal_notice_data(tree, url):
    """
    成交公告 cjgg目录
    """
    data = {}
    # 基础公共字段
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    data["notice_title"] = title_list[0].strip() if title_list else ""
    data["notice_type"] = "成交公告"

    # 项目编号：兼容 项目编号 / 采购项目编号
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

    # ==========修复：把长破折号全部替换为英文短横线 - ==========
    region_list = tree.xpath('//td[text()="行政区域"]/following-sibling::td[1]/text()')
    data["admin_region"] = region_list[0].strip() if region_list else ""



    # =========成交公告特有字段=========
    # 总成交金额（公告概要表格）
    win_amount_list = tree.xpath('//td[contains(text(),"总成交金额")]/following-sibling::td[1]/text()')
    data["win_total_amount"] = None
    if win_amount_list:
        raw = "".join(win_amount_list).strip()
        m = re.search(r"([\d.]+)", raw)
        if m:
            data["win_total_amount"] = float(m.group(1))

    # 评审专家名单
    expert_list = tree.xpath('//td[contains(text(),"评审专家")]/following-sibling::td[1]/text()')
    data["judge_experts"] = expert_list[0].strip() if expert_list else ""

    # 代理服务费
    agent_fee_text = "".join(tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"本项目代理费总金额")]//text()'))
    data["agent_service_fee"] = None
    m_fee = re.search(r"([\d.]+)\s*万元", agent_fee_text)
    if m_fee:
        data["agent_service_fee"] = float(m_fee.group(1))

    # 成交供应商（正文 供应商名称：xxx）
    supplier_text = "".join(tree.xpath('//div[@class="vF_detail_content"]//p[contains(.,"供应商名称：")]//text()'))
    m_sup = re.search(r"供应商名称[:：](.*?)(?=供应商地址|$|\n)", supplier_text)
    data["win_supplier"] = m_sup.group(1).strip() if m_sup else ""
    # =================================

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
    test_url = "https://www.ccgp.gov.cn/cggg/zygg/cjgg/202609/t20260908_27291742.htm"
    tree = get_html_tree(test_url)
    if tree is not None:
        res = extract_deal_notice_data(tree, test_url)
        for k, v in res.items():
            print(f"{k}: {v}")
