from curl_cffi import requests
from lxml import etree

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

detail_url = "https://www.ccgp.gov.cn/cggg/zygg/gkzb/202609/t20260907_27276199.htm"

resp_detail = requests.get(
    detail_url,
    cookies=cookies,
    headers=headers,
    impersonate="chrome131"
)

tree = etree.HTML(resp_detail.content)

def extract_notice_data(tree, url):
    """
    提取方案1全部字段，返回字典
    :param tree: lxml解析树
    :param url: 当前公告网页url
    :return: dict
    """
    data = {}

    # 1.公告标题
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    data["notice_title"] = title_list[0].strip() if title_list else ""

    # 2.项目编号，从正文p标签匹配
    data["project_code"] = ""
    code_xpath = '''
        //div[@class="vF_detail_content"]//p[
            .//strong[contains(text(),"项目编号：") or contains(text(),"采购项目编号：")]
            or contains(text(),"项目编号：") or contains(text(),"采购项目编号：")
        ]//text()
        '''
    code_list = tree.xpath(code_xpath)
    import re
    if code_list:
        raw_str = "".join(code_list).strip()
        match_code = re.search(r"(采购项目编号|项目编号)[:：](.*?)(?=\（|$|\n)", raw_str)
        if match_code:
            data["project_code"] = match_code.group(2).strip()

    # 3.公告类型
    data["notice_type"] = "公开招标公告"

    # 4.发布时间
    pub_list = tree.xpath('//span[@id="pubTime"]/text()')
    data["publish_time"] = pub_list[0].strip() if pub_list else ""

    # 5.开标时间
    open_bid_list = tree.xpath('//td[text()="开标时间"]/following-sibling::td[1]/text()')
    data["open_bid_time"] = open_bid_list[0].strip() if open_bid_list else ""

    # 6.行政区域
    region_list = tree.xpath('//td[text()="行政区域"]/following-sibling::td[1]/text()')
    data["admin_region"] = region_list[0].strip() if region_list else ""

    # 7.预算金额，清洗：￥64.530939万元 -> 提取数字
    budget_raw_list = tree.xpath('//td[text()="预算金额"]/following-sibling::td[1]/text()')
    budget_raw = budget_raw_list[0].strip() if budget_raw_list else ""
    if budget_raw:
        # 取出数字部分
        import re
        match = re.search(r"([\d.]+)", budget_raw)
        if match:
            data["budget"] = float(match.group(1))
        else:
            data["budget"] = None
    else:
        data["budget"] = None

    # 8.采购单位
    buyer_name_list = tree.xpath('//td[text()="采购单位"]/following-sibling::td[1]/text()')
    data["buyer_name"] = buyer_name_list[0].strip() if buyer_name_list else ""

    # 9.采购单位地址
    buyer_addr_list = tree.xpath('//td[text()="采购单位地址"]/following-sibling::td[1]/text()')
    data["buyer_address"] = buyer_addr_list[0].strip() if buyer_addr_list else ""

    # 10.采购单位联系方式
    buyer_contact_list = tree.xpath('//td[text()="采购单位联系方式"]/following-sibling::td[1]/text()')
    data["buyer_contact"] = buyer_contact_list[0].strip() if buyer_contact_list else ""

    # 11.代理机构名称
    agent_name_list = tree.xpath('//td[text()="代理机构名称"]/following-sibling::td[1]/text()')
    data["agent_name"] = agent_name_list[0].strip() if agent_name_list else ""

    # 12.代理机构地址
    agent_addr_list = tree.xpath('//td[text()="代理机构地址"]/following-sibling::td[1]/text()')
    data["agent_address"] = agent_addr_list[0].strip() if agent_addr_list else ""

    # 13.代理机构联系方式
    agent_contact_list = tree.xpath('//td[text()="代理机构联系方式"]/following-sibling::td[1]/text()')
    data["agent_contact"] = agent_contact_list[0].strip() if agent_contact_list else ""

    # 14.公告网页url
    data["notice_url"] = url



    import datetime
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return data


# 执行提取
result = extract_notice_data(tree, detail_url)
for k, v in result.items():
    print(f"{k}: {v}")
