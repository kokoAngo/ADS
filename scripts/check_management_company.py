"""
检查4分以上物件的管理会社
- 黑名单管理会社 → 標記为不可（仲介）
- 白名单管理会社 → 標記为可
- 未知管理会社 → 標記为確認待ち
"""
import os
import sys
import csv
import requests
from pathlib import Path
from dotenv import load_dotenv

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}

# 数据文件路径
DATA_DIR = Path(__file__).parent.parent / "data"
BLACKLIST_FILE = DATA_DIR / "blacklist_companies.txt"
WHITELIST_FILE = DATA_DIR / "whitelist_companies.txt"
FULL_DATA_FILE = DATA_DIR / "management_companies.csv"


def load_company_lists():
    """加载黑白名单和完整数据"""
    blacklist = set()
    whitelist = set()
    case_by_case = set()  # 物件による
    all_companies = {}  # 完整数据

    # 加载黑名单
    if BLACKLIST_FILE.exists():
        with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
            blacklist = {line.strip() for line in f if line.strip()}
        print(f"黑名单: {len(blacklist)} 家")

    # 加载白名单
    if WHITELIST_FILE.exists():
        with open(WHITELIST_FILE, 'r', encoding='utf-8') as f:
            whitelist = {line.strip() for line in f if line.strip()}
        print(f"白名单: {len(whitelist)} 家")

    # 加载完整数据（用于匹配"物件による"等状态）
    if FULL_DATA_FILE.exists():
        with open(FULL_DATA_FILE, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                company = row.get("管理会社名", "").strip()
                status = row.get("広告可否", "").strip()
                if company:
                    all_companies[company] = status
                    if status == "物件による":
                        case_by_case.add(company)
        print(f"物件による: {len(case_by_case)} 家")
        print(f"总计: {len(all_companies)} 家管理公司")

    return blacklist, whitelist, case_by_case, all_companies


def match_company(company_name, company_set):
    """模糊匹配公司名称"""
    if not company_name:
        return None

    # 精确匹配
    if company_name in company_set:
        return company_name

    # 包含匹配
    for known in company_set:
        # 去除常见后缀进行比较
        name1 = company_name.replace("（株）", "").replace("(株)", "").replace("株式会社", "").strip()
        name2 = known.replace("（株）", "").replace("(株)", "").replace("株式会社", "").strip()

        if name1 in name2 or name2 in name1:
            return known

    return None


def get_high_score_properties(min_score=6.0):
    """获取阈值以上且有商号但広告可为空的物件"""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {
            "page_size": 100,
            "filter": {
                "and": [
                    {"property": "予測_view数", "number": {"greater_than_or_equal_to": min_score}},
                    {"property": "商号", "rich_text": {"is_not_empty": True}},
                    {"property": "広告可", "select": {"is_empty": True}}
                ]
            }
        }
        if start_cursor:
            payload["start_cursor"] = start_cursor

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        data = response.json()

        if "results" in data:
            all_results.extend(data["results"])
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        else:
            print(f"Error: {data}")
            break

    return all_results


def update_ad_status(page_id, status):
    """更新広告可状态"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    data = {
        "properties": {
            "広告可": {"select": {"name": status}}
        }
    }
    response = requests.patch(url, headers=headers, json=data, timeout=60)
    return response.status_code == 200


def get_below_threshold_properties(threshold=6.0):
    """获取阈值以下、view数已评但広告可为空的物件（用于标记跳过）"""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {
            "page_size": 100,
            "filter": {
                "and": [
                    {"property": "予測_view数", "number": {"less_than": threshold}},
                    {"property": "予測_view数", "number": {"is_not_empty": True}},
                    {"property": "広告可", "select": {"is_empty": True}}
                ]
            }
        }
        if start_cursor:
            payload["start_cursor"] = start_cursor

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        data = response.json()

        if "results" in data:
            all_results.extend(data["results"])
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        else:
            print(f"Error: {data}")
            break

    return all_results


def mark_skipped(page_id):
    """将低分物件标记为対象外（用--表示跳过）"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    data = {
        "properties": {
            "広告可": {"select": {"name": "--"}}
        }
    }
    response = requests.patch(url, headers=headers, json=data, timeout=60)
    return response.status_code == 200


def main():
    print("=" * 60)
    print("检查6分以上物件的管理会社")
    print("=" * 60)

    # 加载公司列表
    print("\n加载管理公司数据...")
    blacklist, whitelist, case_by_case, all_companies = load_company_lists()

    # 获取物件
    print("\n获取6分以上且広告可为空的物件...")
    pages = get_high_score_properties()
    print(f"找到 {len(pages)} 个物件需要检查")

    if not pages:
        print("没有需要检查的物件")
        return

    # 统计
    stats = {
        "可": 0,
        "不可（仲介）": 0,
        "物件による": 0,
        "確認待ち": 0
    }

    for i, page in enumerate(pages):
        props = page["properties"]
        page_id = page["id"]

        # 获取REINS_ID
        reins_id = "Unknown"
        if "REINS_ID" in props and props["REINS_ID"]["title"]:
            reins_id = props["REINS_ID"]["title"][0]["plain_text"]

        # 获取管理会社（从商号字段读取）
        company = ""
        if "商号" in props and props["商号"]["rich_text"]:
            company = props["商号"]["rich_text"][0]["plain_text"]

        # 获取得分
        score = props.get("予測_view数", {}).get("number", 0)

        print(f"\n[{i+1}/{len(pages)}] {reins_id} (得分: {score})")
        print(f"  管理会社: {company}")

        # 判断状态
        status = None

        # 检查黑名单
        if match_company(company, blacklist):
            status = "不可（仲介）"

        # 检查白名单
        elif match_company(company, whitelist):
            status = "可"

        # 检查物件による
        elif match_company(company, case_by_case):
            status = "物件による"

        # 未知
        else:
            status = "確認待ち"

        # 更新状态
        if update_ad_status(page_id, status):
            print(f"  → {status}")
            stats[status] += 1
        else:
            print(f"  ✗ 更新失败")

    print(f"\n{'='*60}")
    print("完成!")
    print("-" * 30)
    for status, count in stats.items():
        if count > 0:
            print(f"  {status}: {count} 个")
    print("=" * 60)

    # 标记低分物件为跳过
    print("\n标记6分以下物件为跳过...")
    low_pages = get_below_threshold_properties(threshold=6.0)
    print(f"找到 {len(low_pages)} 个低分物件")
    skipped_count = 0
    for page in low_pages:
        if mark_skipped(page["id"]):
            skipped_count += 1
    print(f"已标记跳过: {skipped_count}/{len(low_pages)} 个")


if __name__ == "__main__":
    main()
