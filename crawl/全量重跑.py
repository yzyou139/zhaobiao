# -*- coding: utf-8 -*-
"""全量重跑驱动：顺序解析 9 个类别的列表 CSV -> *_data_v2.csv（断点续爬，可中断重跑）"""
import subprocess
import sys
import os

BASE = os.path.dirname(os.path.abspath(__file__))
CATEGORIES = ["dylygg", "zbgg", "cjgg", "gkzb", "gzgg", "jzxcs", "jzxtpgg", "xjgg", "qtgg"]

if __name__ == "__main__":
    only = sys.argv[1:] or CATEGORIES
    for cat in only:
        inp = os.path.join(BASE, f"ccgp_{cat}.csv")
        outp = os.path.join(BASE, f"ccgp_{cat}_data_v2.csv")
        print(f"\n{'#' * 60}\n# 类别: {cat}  ->  {os.path.basename(outp)}\n{'#' * 60}", flush=True)
        r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(BASE, "整合.py"), inp, outp])
        if r.returncode != 0:
            print(f"!! {cat} 退出码 {r.returncode}，继续下一类别", flush=True)
    print("\n全部类别处理完成", flush=True)
