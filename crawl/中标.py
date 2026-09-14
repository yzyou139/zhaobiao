from curl_cffi import requests
from lxml import etree
import re
import datetime
import json

cookies = {
    'Hm_lvt_9f8bda7a6bb3d1d7a9c7196bfed609b5': '1788746299',
    'HMACCOUNT': '119E8986621B12EA',
    'Hm_lpvt_9f8bda7a6bb3d1d7a9c7196bfed609b5': '1788746316',
}

headers = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'Pragma': 'no-cache',
    'Referer': 'https://www.ccgp.gov.cn/cggg/zygg/',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
    'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
}

# 写死中标公告测试链接
detail_url = "https://www.ccgp.gov.cn/cggg/zygg/zbgg/./202609/t20260909_27294248.htm"

resp_detail = requests.get(
    detail_url,
    cookies=cookies,
    headers=headers,
    impersonate="chrome131"
)
tree = etree.HTML(resp_detail.content)


def extract_win_notice_data(tree, url):
    """
    提取【中标公告】字段，返回字典
    :param tree: lxml解析树
    :param url: 公告完整url
    :return: dict
    """
    data = {}
    # 1.公告标题
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    data["notice_title"] = title_list[0].strip() if title_list else ""

    # 2.项目编号 正文p标签
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

    # 3.公告类型
    data["notice_type"] = "中标公告"

    # 4.发布时间
    pub_list = tree.xpath('//span[@id="pubTime"]/text()')
    data["publish_time"] = pub_list[0].strip() if pub_list else ""

    # 5.行政区域
    region_list = tree.xpath('//td[text()="行政区域"]/following-sibling::td[1]/text()')
    data["admin_region"] = region_list[0].strip() if region_list else ""

    # ==========中标公告特有字段==========
    # 总中标金额（万元）
    win_total_raw_list = tree.xpath('//td[text()="总中标金额"]/following-sibling::td[1]/text()')
    win_total_raw = win_total_raw_list[0].strip() if win_total_raw_list else ""
    if win_total_raw:
        match = re.search(r"([\d.]+)", win_total_raw)
        data["win_total_amount"] = float(match.group(1)) if match else None
    else:
        data["win_total_amount"] = None

    # 评审专家名单
    expert_list = tree.xpath('//td[text()="评审专家名单"]/following-sibling::td[1]/text()')
    data["judge_experts"] = expert_list[0].strip() if expert_list else ""

    # 代理服务费总金额，正文匹配
    agent_fee_list = tree.xpath('//div[@class="vF_detail_content"]//p[contains(text(),"本项目代理费总金额：")]/text()')
    agent_fee_raw = agent_fee_list[0].strip() if agent_fee_list else ""
    if agent_fee_raw:
        m = re.search(r"([\d.]+)", agent_fee_raw)
        data["agent_service_fee"] = float(m.group(1)) if m else None
    else:
        data["agent_service_fee"] = None
    # ==================================

    # 6.采购单位
    buyer_name_list = tree.xpath('//td[text()="采购单位"]/following-sibling::td[1]/text()')
    data["buyer_name"] = buyer_name_list[0].strip() if buyer_name_list else ""

    # 7.采购单位地址
    buyer_addr_list = tree.xpath('//td[text()="采购单位地址"]/following-sibling::td[1]/text()')
    data["buyer_address"] = buyer_addr_list[0].strip() if buyer_addr_list else ""

    # 8.采购单位联系方式
    buyer_contact_list = tree.xpath('//td[text()="采购单位联系方式"]/following-sibling::td[1]/text()')
    data["buyer_contact"] = buyer_contact_list[0].strip() if buyer_contact_list else ""

    # 9.代理机构名称
    agent_name_list = tree.xpath('//td[text()="代理机构名称"]/following-sibling::td[1]/text()')
    data["agent_name"] = agent_name_list[0].strip() if agent_name_list else ""

    # 10.代理机构地址
    agent_addr_list = tree.xpath('//td[text()="代理机构地址"]/following-sibling::td[1]/text()')
    data["agent_address"] = agent_addr_list[0].strip() if agent_addr_list else ""

    # 11.代理机构联系方式
    agent_contact_list = tree.xpath('//td[text()="代理机构联系方式"]/following-sibling::td[1]/text()')
    data["agent_contact"] = agent_contact_list[0].strip() if agent_contact_list else ""

    # 12.公告网页url
    data["notice_url"] = url

    # ==========改造：输出json格式附件数组 ==========
    a_tags = tree.xpath('//a[@class="bizDownload"]')
    attach_list = []
    for a in a_tags:
        name_raw = a.xpath("./text()")
        name = name_raw[0].strip() if name_raw else ""
        attach_id = a.xpath("./@id")
        full_href = ""
        if attach_id:
            uuid_str = attach_id[0].strip()
            full_href = f"https://download.ccgp.gov.cn/oss/download?uuid={uuid_str}"
        if name:
            attach_list.append({
                "attach_name": name,
                "attach_url": full_href
            })
    # json字符串存入字段，ensure_ascii=False支持中文
    data["attach_json"] = json.dumps(attach_list, ensure_ascii=False)
    # ====================================================

    # 抓取时间
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return data




# 执行提取
result = extract_win_notice_data(tree, detail_url)
for k, v in result.items():
    print(f"{k}: {v}")
