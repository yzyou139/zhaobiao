import pandas as pd

# 手动列出要合并的csv文件路径
files = [
    r"D:\bishe\crawl\table1.csv",
    r"D:\bishe\crawl\table2.csv",
    r"D:\bishe\crawl\table3.csv",
    r"D:\bishe\crawl\table4.csv",
    r"D:\bishe\crawl\table5.csv",
]

output = r"./字段说明.csv"      # 合并结果

dfs = [pd.read_csv(f) for f in files]
result = pd.concat(dfs, ignore_index=True)
result.to_csv(output, index=False, encoding="utf-8-sig")
print(f"合并完成，共 {len(result)} 行 → {output}")
