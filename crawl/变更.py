from curl_cffi import requests
from lxml import etree
import re
import datetime


def extract_change_notice_data(tree, url):
    data = {}
    # 基础公共字段（和之前保持一致）
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    data["notice_title"] = title_list[0].strip() if title_list else ""
    data["notice_type"] = "变更公告"

    data["project_code"] = ""
    # 修复：末尾加 /text() 直接取文本，不再拿到element对象
    code_xpath = '''
    //div[@class="vF_detail_content"]//p[
        contains(text(),"原公告的采购项目编号：") or contains(text(),"项目编号：")
    ]//text()
    '''
    code_list = tree.xpath(code_xpath)
    if code_list:
        raw_str = "".join(code_list).strip()
        match_code = re.search(r"(原公告的采购项目编号|项目编号)[:：](.*?)(?=\（|$|\n)", raw_str)
        if match_code:
            data["project_code"] = match_code.group(2).strip()

    pub_list = tree.xpath('//span[@id="pubTime"]/text()')
    data["publish_time"] = pub_list[0].strip() if pub_list else ""

    region_list = tree.xpath('//td[text()="行政区域"]/following-sibling::td[1]/text()')
    data["admin_region"] = region_list[0].strip() if region_list else ""



    buyer_name_list = tree.xpath('//td[text()="采购单位"]/following-sibling::td[1]/text()')
    data["buyer_name"] = buyer_name_list[0].strip() if buyer_name_list else ""

    buyer_addr_list = tree.xpath('//td[text()="采购单位地址"]/following-sibling::td[1]/text()')
    data["buyer_address"] = buyer_addr_list[0].strip() if buyer_addr_list else ""

    buyer_contact_list = tree.xpath('//td[text()="采购单位联系方式"]/following-sibling::td[1]/text()')
    data["buyer_contact"] = buyer_contact_list[0].strip() if buyer_contact_list else ""

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

    # ========== 新增：变更相关字段 ==========
    # 更正事项（公告概要表格）
    change_item_list = tree.xpath('//td[text()="更正事项"]/following-sibling::td[1]/text()')
    data["change_item"] = change_item_list[0].strip() if change_item_list else ""


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
    test_url = "https://www.ccgp.gov.cn/cggg/zygg/gzgg/202609/t20260908_27291831.htm"
    tree = get_html_tree(test_url)
    if tree is not None:
        res = extract_change_notice_data(tree, test_url)
        for k, v in res.items():
            print(f"{k}: {v}")
