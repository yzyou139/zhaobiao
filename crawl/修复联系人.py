# -*- coding: utf-8 -*-
"""联系人字段本地修复：利用已入库的 content_text 重新提取 contact_name/contact_tel，
不重新请求网络。修复两类问题：
  1) 名字粘连标签词（'联系代表电话'）
  2) 电话粘连段落编号 / 配到采购人·代理机构的电话（'010-662721112'）
"""
import csv
import os
import re
import shutil
import importlib.util

BASE = os.path.dirname(os.path.abspath(__file__))
FILES = ["zbgg", "gkzb", "gzgg", "cjgg", "jzxcs", "jzxtpgg", "xjgg", "qtgg", "dylygg"]

# 加载整合.py 中的新提取函数
spec = importlib.util.spec_from_file_location("integration", os.path.join(BASE, "整合.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def looks_wrong(name: str, tel: str) -> bool:
    """判断旧值是否疑似有问题（用于统计）"""
    if re.search(r"(电\s*话|联系方式)$", name):
        return True
    if re.search(r"\d[.、]", tel):
        return True
    return False


def main():
    total_fixed = 0
    total_rows = 0
    for cat in FILES:
        fn = os.path.join(BASE, f"ccgp_{cat}_data_v2.csv")
        if not os.path.exists(fn):
            continue
        with open(fn, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)

        changed = 0
        suspicious = 0
        for r in rows:
            total_rows += 1
            old_name, old_tel = r["contact_name"], r["contact_tel"]
            if looks_wrong(old_name, old_tel):
                suspicious += 1
            new = m.extract_contact_info(None, r.get("content_text", ""))
            # 表格值已丢失（正文回退结果），仅在提取到值时覆盖
            if new["contact_name"]:
                r["contact_name"] = new["contact_name"]
            if new["contact_tel"]:
                r["contact_tel"] = new["contact_tel"]
            if (r["contact_name"], r["contact_tel"]) != (old_name, old_tel):
                changed += 1

        if changed:
            if not os.path.exists(fn + ".bak"):
                shutil.copy(fn, fn + ".bak")  # 只保留最初原始备份
            with open(fn, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        total_fixed += changed
        print(f"{cat:10s} 行数:{len(rows):4d} 旧值疑似异常:{suspicious:3d} 本次修正:{changed:3d}")

    print(f"\n合计修正 {total_fixed}/{total_rows} 行（原文件已备份为 .bak）")


if __name__ == "__main__":
    main()
