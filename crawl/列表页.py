import random
import requests
from lxml import etree
import time
import csv

# ===================== 公告类型提取函数 =====================
def extract_raw_notice_type(title: str) -> str:
    title = title or ""
    title = title.strip()
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
        "中标公告",
        "中标结果公示",
        "中标结果公告",
    ]
    for word in long_candidates:
        if title.endswith(word):
            return word
    idx = title.rfind("公告")
    if idx == -1:
        return ""
    prefix_text = title[:idx]
    collect_chars = []
    for char in reversed(prefix_text):
        if "\u4e00" <= char <= "\u9fff":
            collect_chars.append(char)
            if len(collect_chars) >= 4:
                break
        else:
            break
    collect_chars.reverse()
    word = "".join(collect_chars)
    if len(word) < 2:
        return ""
    raw_type = word + "公告"
    return raw_type


def normalize_notice_type(raw_type: str) -> str:
    if not raw_type:
        return "未知公告"
    if "竞价" in raw_type:
        return "竞价公告"
    elif "成交" in raw_type:
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
    elif "中标公告" in raw_type:
        return "中标公告"
    elif "中标结果公示" in raw_type:
        return "中标结果公示"
    elif "中标结果公告" in raw_type:
        return "中标结果公告"
    return raw_type

# ===================== 爬虫主体 =====================
# ✅ 你要改的文件名，只改这里！
CSV_FILENAME = "ccgp_gzgg.csv"

BASE_URL = "https://www.ccgp.gov.cn/cggg/zygg/gzgg/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def parse_list_page(page_num: int):
    """
    page_num: 页码，从 1 开始
    返回当前页所有公告列表
    """
    if page_num == 1:
        url = BASE_URL + "index.htm"
    else:
        url = BASE_URL + f"index_{page_num - 1}.htm"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        html = etree.HTML(resp.text)
    except Exception as e:
        print(f"第{page_num}页请求失败：{e}")
        return []

    items = []
    li_list = html.xpath('//ul[@class="c_list_bid"]/li')
    for li in li_list:
        a_nodes = li.xpath("./a")
        if not a_nodes:
            continue
        a_node = a_nodes[0]
        title = a_node.xpath("@title")[0].strip()
        href_rel = a_node.xpath("@href")[0].strip()
        detail_url = BASE_URL + href_rel

        ems = li.xpath(".//em/text()")
        if len(ems) >=3:
            pub_time = ems[0].strip()
            area = ems[1].strip()
            purchaser = ems[2].strip()
        else:
            pub_time = area = purchaser = ""

        raw_notice_type = extract_raw_notice_type(title)
        notice_type = normalize_notice_type(raw_notice_type)

        row = {
            "notice_title": title,
            "detail_url": detail_url,
            "pub_time": pub_time,
            "area": area,
            "purchaser": purchaser,
            "raw_notice_type": raw_notice_type,
            "notice_type": notice_type
        }
        items.append(row)
    return items


def append_csv(page_data, is_first=False):
    """追加写入一页数据到csv，is_first=True时写入表头"""
    if not page_data:
        return
    fieldnames = list(page_data[0].keys())
    with open(CSV_FILENAME, mode="a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if is_first:
            writer.writeheader()
        writer.writerows(page_data)


if __name__ == "__main__":
    seen_urls = set()
    total_count = 0
    start_page = 1
    end_page = 25

    for p in range(start_page, end_page + 1):
        print(f"\n========== 正在抓取第{p}页 ==========")
        page_raw_items = parse_list_page(p)
        current_page_items = []

        # 本页去重
        for item in page_raw_items:
            if item["detail_url"] not in seen_urls:
                seen_urls.add(item["detail_url"])
                current_page_items.append(item)
            else:
                print(f"【跳过重复】{item['detail_url']}")

        # ✅ 一页抓取完，立刻打印当前页数据
        print(f"\n---------- 第{p}页结果（{len(current_page_items)}条）----------")
        for item in current_page_items:
            print("-" * 60)
            for k, v in item.items():
                print(f"{k}: {v}")

        # ✅ 一页抓取完，立刻追加写入CSV，只有第一页写表头
        is_first_page = True if p == start_page else False
        append_csv(current_page_items, is_first=is_first_page)

        total_count += len(current_page_items)
        print(f"\n第{p}页保存完毕，累计已抓取：{total_count} 条")
        time.sleep(random.uniform(1.0, 3.0))

    print(f"\n==== 全部{end_page}页抓取结束，总共有效数据：{total_count}条 ====")
