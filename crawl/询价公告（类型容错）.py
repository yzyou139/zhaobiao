from lxml import etree
import re
import requests

def extract_raw_notice_type(title: str) -> str:
    title = title or ""
    title = title.strip()
    # 长词候选列表，长词放前面优先匹配
    long_candidates = [
        "框架协议二次竞价公告",
        "中标（成交）结果公告",
        "竞争性磋商公告",
        "竞争性谈判公告",
        "公开招标公告",
        "资格预审公告",
        "单一来源成交公告",
        "单一来源公示",
        "询价公告",
        "招标公告",
        "成交公告",
        "更正公告",
        "变更公告",
        "流标公告",
        "终止公告",
        "采购意向公告",
    ]
    # 优先匹配标题结尾完整长类型
    for word in long_candidates:
        if title.endswith(word):
            return word

    # 降级策略：找到公告，向前提取连续中文字符，跳过括号数字
    idx = title.rfind("公告")
    if idx == -1:
        return ""

    prefix_text = title[:idx]
    collect_chars = []
    # 从后往前遍历prefix_text
    for char in reversed(prefix_text):
        # 判断是否中文汉字
        if "\u4e00" <= char <= "\u9fff":
            collect_chars.append(char)
            # 最多收集4个汉字就停止
            if len(collect_chars) >= 4:
                break
        else:
            # 碰到非中文（括号、数字、横线等），直接终止
            break
    # 反转回来，恢复顺序
    collect_chars.reverse()
    word = "".join(collect_chars)
    if len(word) < 2:
        return ""
    raw_type = word + "公告"
    return raw_type

def normalize_notice_type(raw_type: str) -> str:
    """
    归一化：关键词规则匹配，**不写死全部key**
    """
    if not raw_type:
        return "未知公告"
    # 关键词规则，按优先级从上到下
    if "竞价" in raw_type:
        return "竞价公告"
    elif "中标" in raw_type or "成交" in raw_type:
        return "成交公告"
    elif "询价" in raw_type:
        return "询价公告"
    elif "竞争性磋商" in raw_type:
        return "竞争性磋商公告"
    elif "竞争性谈判" in raw_type:
        return "竞争性谈判公告"
    elif "公开招标" in raw_type:
        return "公开招标公告"
    elif "招标" in raw_type:
        return "招标公告"
    elif "单一来源公示" in raw_type:
        return "单一来源公示"
    elif "资格预审" in raw_type:
        return "资格预审公告"
    elif "更正" in raw_type:
        return "更正公告"
    elif "变更" in raw_type:
        return "变更公告"
    elif "流标" in raw_type:
        return "流标公告"
    elif "终止" in raw_type:
        return "终止公告"
    elif "采购意向" in raw_type:
        return "采购意向公告"
    # 未命中规则，原样返回，兼容未来新命名
    return raw_type


def get_html_tree(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        tree = etree.HTML(resp.text)
        return tree
    except Exception as e:
        print("请求失败：", e)
        return None


def extract_inquiry_notice_data(tree: etree._Element, url: str):
    """
    政府采购网 公告解析（适配xjgg栏目下多种类型，动态提取公告类型）
    """
    data = {}
    # 1.公告标题、动态提取公告类型
    title_nodes = tree.xpath('//h2[@class="tc"]/text()')
    data["notice_title"] = title_nodes[0].strip() if title_nodes else ""

    raw_type = extract_raw_notice_type(data["notice_title"])
    data["raw_notice_type"] = raw_type
    data["notice_type"] = normalize_notice_type(raw_type)

    # 2.发布时间
    pub_time_nodes = tree.xpath('//span[@id="pubTime"]/text()')
    data["publish_time"] = pub_time_nodes[0].strip() if pub_time_nodes else ""

    # ========== 【重点：从隐藏的公告概要表格xpath提取】 ==========
    # 行政区域
    region_nodes = tree.xpath('//div[@class="table"]//td[text()="行政区域"]/following-sibling::td[1]/text()')
    data["admin_region"] = region_nodes[0].strip() if region_nodes else ""

    # 采购单位（采购人）
    purchaser_nodes = tree.xpath('//div[@class="table"]//td[text()="采购单位"]/following-sibling::td[1]/text()')
    data["purchaser"] = purchaser_nodes[0].strip() if purchaser_nodes else ""

    # 代理机构名称
    agency_nodes = tree.xpath('//div[@class="table"]//td[text()="代理机构名称"]/following-sibling::td[1]/text()')
    data["agency"] = agency_nodes[0].strip() if agency_nodes else ""

    # 项目联系人
    contact_name_nodes = tree.xpath('//div[@class="table"]//td[text()="项目联系人"]/following-sibling::td[1]/text()')
    data["contact_name"] = contact_name_nodes[0].strip() if contact_name_nodes else ""

    # 项目联系电话
    contact_tel_nodes = tree.xpath('//div[@class="table"]//td[text()="项目联系电话"]/following-sibling::td[1]/text()')
    data["contact_tel"] = contact_tel_nodes[0].strip() if contact_tel_nodes else ""

    # 预算金额
    budget_text_nodes = tree.xpath('//div[@class="table"]//td[text()="预算金额"]/following-sibling::td[1]/text()')
    data["budget"] = None
    data["budget_source"] = ""
    if budget_text_nodes:
        budget_raw = budget_text_nodes[0].strip()
        tb_match = re.search(r"([\d,.]+)", budget_raw)
        if tb_match:
            val_str = tb_match.group(1).replace(",", "")
            data["budget"] = float(val_str)
            data["budget_source"] = "table"

    # 正文全部文本，用来拿项目编号、采购方式
    content_text = "".join(tree.xpath('//div[@id="noticeArea"]//text()'))
    # 项目编号
    code_match = re.search(r"项目编号[:：]([A-Z0-9\-_]+)", content_text)
    data["project_code"] = code_match.group(1).strip() if code_match else ""
    # 采购方式
    method_match = re.search(r"采购方式[:：](.*?)(?=\n|$)", content_text, re.S)
    data["purchase_method"] = method_match.group(1).strip() if method_match else ""

    # 联动采购方式：只要raw_type里面带竞价关键词，赋值采购方式
    if "竞价" in raw_type:
        data["purchase_method"] = "框架协议二次竞价"

    # 采购预告类无成交金额、无中标供应商
    data["deal_amount"] = None
    data["win_supplier"] = ""

    # 预留空字段，保证和其他解析器字段对齐，入库无需修改表
    data["judge_experts"] = ""
    data["agent_service_fee"] = None
    data["terminate_reason"] = ""
    data["change_content"] = ""
    data["publicity_period"] = ""

    return data


if __name__ == "__main__":
    # 测试：璧山框架二次竞价
    # test_url = "https://www.ccgp.gov.cn/cggg/dfgg/xjgg/202609/t20260909_27292669.htm"
    test_url = "https://www.ccgp.gov.cn/cggg/dfgg/zgysgg/202411/t20241113_23606889.htm"
    tree = get_html_tree(test_url)
    if tree is not None:
        res = extract_inquiry_notice_data(tree, test_url)
        for k, v in res.items():
            print(f"{k}: {v}")
