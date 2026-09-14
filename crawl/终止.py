from curl_cffi import requests
from lxml import etree
import re
import datetime

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

# 写死终止公告测试链接
detail_url = "https://www.ccgp.gov.cn/cggg/zygg/fblbgg/202609/t20260909_27292423.htm"

resp_detail = requests.get(
    detail_url,
    cookies=cookies,
    headers=headers,
    impersonate="chrome131"
)
tree = etree.HTML(resp_detail.content)


def extract_terminate_notice_data(tree, url):
    """
    提取【终止/废标公告】字段，修复采购项目编号匹配问题
    :param tree: lxml解析树
    :param url: 公告完整url
    :return: dict
    """
    data = {}

    # 1.公告标题
    title_list = tree.xpath('//h2[@class="tc"]/text()')
    data["notice_title"] = title_list[0].strip() if title_list else ""

    # ==========【修复项目编号：兼容采购项目编号 / 项目编号】==========
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
    # ==============================================================

    # 3.公告类型
    data["notice_type"] = "终止公告"

    # 4.发布时间
    pub_list = tree.xpath('//span[@id="pubTime"]/text()')
    data["publish_time"] = pub_list[0].strip() if pub_list else ""

    # 5.行政区域
    region_list = tree.xpath('//td[text()="行政区域"]/following-sibling::td[1]/text()')
    data["admin_region"] = region_list[0].strip() if region_list else ""

    # ==========终止公告特有字段==========
    # 采购方式（正文：公开招标/竞争性磋商/询价等）
    data["purchase_method"] = ""
    method_list = tree.xpath('//div[@class="vF_detail_content"]//p[.//strong[contains(text(),"采购方式：")]]//text()')
    if not method_list:
        method_list = tree.xpath('//div[@class="vF_detail_content"]//p[contains(text(),"采购方式：")]/text()')
    if method_list:
        raw = "".join(method_list).strip()
        m = re.search(r"采购方式[:：](.*?)(?=[，。；\n]|$)", raw)
        if m:
            data["purchase_method"] = m.group(1).strip()

    # 预算金额（终止公告可能有原项目预算）
    data["budget"] = None
    budget_raw_list = tree.xpath('//td[text()="预算金额"]/following-sibling::td[1]/text()')
    if budget_raw_list:
        budget_raw = budget_raw_list[0].strip()
        match = re.search(r"([\d.]+)", budget_raw)
        if match:
            data["budget"] = float(match.group(1))
    if data["budget"] is None:
        # 正文里找预算金额
        budget_p_list = tree.xpath('//div[@class="vF_detail_content"]//p[contains(text(),"预算金额：")]//text()')
        if budget_p_list:
            raw = "".join(budget_p_list)
            match = re.search(r"([\d.]+)\s*万元", raw)
            if match:
                data["budget"] = float(match.group(1))

    # 终止原因（核心字段，从正文提取）
    data["terminate_reason"] = ""
    # 找包含"终止的原因"段落
    reason_p_list = tree.xpath(
        '//div[@class="vF_detail_content"]//p[contains(text(),"项目终止的原因") or contains(text(),"废标原因") or contains(text(),"终止原因")]'
    )
    if reason_p_list:
        reason_texts = reason_p_list[0].xpath('.//text()')
        data["terminate_reason"] = "".join(reason_texts).strip()
    # 备选匹配
    if not data["terminate_reason"]:
        all_p = tree.xpath('//div[@class="vF_detail_content"]//p//text()')
        full_text = "".join(all_p)
        m = re.search(r"项目终止的原因[：:]?\s*(.*?)(?=三、|其他补充|凡对本次公告|$)", full_text, re.DOTALL)
        if m:
            data["terminate_reason"] = m.group(1).strip()[:500]
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

    # 13.附件名称（取a标签文本，不是@title）
    attach_name_list = tree.xpath('//a[@class="bizDownload"]/text()')
    data["attach_file_names"] = ",".join([name.strip() for name in attach_name_list]) if attach_name_list else ""

    # 抓取时间
    data["crawl_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return data



# 执行提取
result = extract_terminate_notice_data(tree, detail_url)
for k, v in result.items():
    print(f"{k}: {v}")
