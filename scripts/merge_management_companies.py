#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
合并所有区的管理公司数据，生成统一的广告可否列表
"""

import os
import csv
import re
from pathlib import Path
from collections import defaultdict

# 目录
MANAGER_DIR = Path(__file__).parent.parent / "manager"
OUTPUT_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "management_companies.csv"

# 确保输出目录存在
OUTPUT_DIR.mkdir(exist_ok=True)


def normalize_company_name(name):
    """标准化公司名称（去除空格、全角半角转换等）"""
    if not name:
        return ""
    # 去除前后空格
    name = name.strip()
    # 全角转半角（部分字符）
    name = name.replace("　", " ").replace("（", "(").replace("）", ")")
    # 去除多余空格
    name = re.sub(r'\s+', ' ', name)
    return name


def parse_ad_status(status):
    """解析广告可否状态，返回标准化的状态"""
    if not status:
        return "未確認"

    status = status.strip()

    # 可以广告
    if status in ["全媒体　可能", "全媒体 可能", "全媒体可能"]:
        return "可"

    # 不可以
    if status == "不可":
        return "不可"

    # 部分可以
    if "以外可" in status or "以外　可" in status:
        return f"部分可（{status}）"

    # 看情况
    if status in ["物件による", "-"]:
        return "物件による"

    # 其他
    return status


def load_csv_files():
    """加载所有CSV文件"""
    all_companies = defaultdict(lambda: {"areas": [], "status": "未確認", "phone": "", "contact": ""})

    csv_files = list(MANAGER_DIR.glob("*.csv"))
    print(f"找到 {len(csv_files)} 个CSV文件")

    for csv_file in csv_files:
        # 从文件名提取区名
        area_match = re.search(r'(\S+区)', csv_file.stem)
        area = area_match.group(1) if area_match else "不明"

        print(f"  处理: {csv_file.name} ({area})")

        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) < 2:
                        continue

                    contact = row[0].strip() if len(row) > 0 else ""
                    company = normalize_company_name(row[1]) if len(row) > 1 else ""
                    phone = row[2].strip() if len(row) > 2 else ""
                    status_raw = row[3].strip() if len(row) > 3 else ""

                    if not company or company == "広告可否":
                        continue

                    status = parse_ad_status(status_raw)

                    # 更新公司信息
                    if area not in all_companies[company]["areas"]:
                        all_companies[company]["areas"].append(area)

                    # 优先保留有明确状态的记录
                    if status != "未確認":
                        if all_companies[company]["status"] == "未確認":
                            all_companies[company]["status"] = status
                        elif all_companies[company]["status"] != status:
                            # 如果状态冲突，标记为"要确认"
                            all_companies[company]["status"] = f"要確認（{all_companies[company]['status']} / {status}）"

                    if phone and not all_companies[company]["phone"]:
                        all_companies[company]["phone"] = phone

                    if contact and not all_companies[company]["contact"]:
                        all_companies[company]["contact"] = contact

        except Exception as e:
            print(f"    错误: {e}")

    return all_companies


def save_merged_csv(companies):
    """保存合并后的CSV"""
    # 按状态和公司名排序
    status_order = {"不可": 0, "可": 1, "部分可": 2, "物件による": 3, "未確認": 4}

    def sort_key(item):
        company, info = item
        status = info["status"]
        for key, order in status_order.items():
            if status.startswith(key):
                return (order, company)
        return (5, company)

    sorted_companies = sorted(companies.items(), key=sort_key)

    with open(OUTPUT_FILE, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["管理会社名", "広告可否", "対応区域", "連絡方法", "電話番号"])

        for company, info in sorted_companies:
            writer.writerow([
                company,
                info["status"],
                ", ".join(info["areas"]),
                info["contact"],
                info["phone"]
            ])

    print(f"\n保存到: {OUTPUT_FILE}")
    return len(sorted_companies)


def generate_blacklist(companies):
    """生成黑名单（不可广告的公司）"""
    blacklist = []
    whitelist = []

    for company, info in companies.items():
        status = info["status"]
        if status == "不可":
            blacklist.append(company)
        elif status == "可":
            whitelist.append(company)

    # 保存黑名单
    blacklist_file = OUTPUT_FILE.parent / "blacklist_companies.txt"
    with open(blacklist_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(sorted(blacklist)))
    print(f"黑名单保存到: {blacklist_file} ({len(blacklist)}家)")

    # 保存白名单
    whitelist_file = OUTPUT_FILE.parent / "whitelist_companies.txt"
    with open(whitelist_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(sorted(whitelist)))
    print(f"白名单保存到: {whitelist_file} ({len(whitelist)}家)")

    return blacklist, whitelist


def main():
    print("=" * 60)
    print("合并管理公司数据")
    print("=" * 60)

    # 加载所有CSV
    companies = load_csv_files()
    print(f"\n共找到 {len(companies)} 家管理公司")

    # 统计状态
    status_count = defaultdict(int)
    for info in companies.values():
        status = info["status"]
        if status.startswith("部分可"):
            status_count["部分可"] += 1
        elif status.startswith("要確認"):
            status_count["要確認"] += 1
        else:
            status_count[status] += 1

    print("\n状态统计:")
    for status, count in sorted(status_count.items()):
        print(f"  {status}: {count}家")

    # 保存合并后的CSV
    total = save_merged_csv(companies)

    # 生成黑名单和白名单
    blacklist, whitelist = generate_blacklist(companies)

    print("\n" + "=" * 60)
    print("完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
