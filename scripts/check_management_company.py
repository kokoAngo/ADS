"""
检查6分以上物件的管理会社
- 黑名单管理会社 → 標記为不可（仲介）
- 未知管理会社 → 標記为確認待ち
"""
import os
import sys
import csv
import requests
from dotenv import load_dotenv

os.chdir(r"D:\Fango Ads")
load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}

# 加载黑名单
BLACKLIST_COMPANIES = []
try:
    with open("funt IDpass - 千代田区　管理会社.csv", "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if len(row) >= 4 and row[3] == "不可":
                company_name = row[1].strip()
                if company_name:
                    BLACKLIST_COMPANIES.append(company_name)
    print(f"已加载 {len(BLACKLIST_COMPANIES)} 个黑名单管理会社")
except Exception as e:
    print(f"加载黑名单失败: {e}")


def get_high_score_properties(min_score=6.0):
    """获取6分以上且有管理会社但広告可为空的物件"""
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
                    {"property": "管理会社", "rich_text": {"is_not_empty": True}},
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


def update_ad_status(page_id, status, company_name):
    """更新広告可状态"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    data = {
        "properties": {
            "広告可": {"select": {"name": status}}
        }
    }
    response = requests.patch(url, headers=headers, json=data, timeout=60)
    return response.status_code == 200


def main():
    print("=" * 60)
    print("检查6分以上物件的管理会社")
    print("=" * 60)

    # 获取物件
    print("\n获取6分以上且広告可为空的物件...")
    pages = get_high_score_properties()
    print(f"找到 {len(pages)} 个物件需要检查")

    if not pages:
        print("没有需要检查的物件")
        return

    # 检查每个物件
    blacklist_count = 0
    unknown_count = 0

    for i, page in enumerate(pages):
        props = page["properties"]
        page_id = page["id"]

        # 获取REINS_ID
        reins_id = "Unknown"
        if "REINS_ID" in props and props["REINS_ID"]["title"]:
            reins_id = props["REINS_ID"]["title"][0]["plain_text"]

        # 获取管理会社
        company = ""
        if "管理会社" in props and props["管理会社"]["rich_text"]:
            company = props["管理会社"]["rich_text"][0]["plain_text"]

        # 获取得分
        score = props.get("予測_view数", {}).get("number", 0)

        print(f"\n[{i+1}/{len(pages)}] {reins_id} (得分: {score})")
        print(f"  管理会社: {company}")

        # 检查是否在黑名单
        is_blacklisted = False
        for blacklisted in BLACKLIST_COMPANIES:
            if blacklisted in company or company in blacklisted:
                is_blacklisted = True
                break

        if is_blacklisted:
            status = "不可（仲介）"
            if update_ad_status(page_id, status, company):
                print(f"  → 标记为 {status}")
                blacklist_count += 1
            else:
                print(f"  ✗ 更新失败")
        else:
            status = "確認待ち"
            if update_ad_status(page_id, status, company):
                print(f"  → 标记为 {status}")
                unknown_count += 1
            else:
                print(f"  ✗ 更新失败")

    print(f"\n{'='*60}")
    print(f"完成!")
    print(f"黑名单管理会社: {blacklist_count} 个 → 不可（仲介）")
    print(f"未知管理会社: {unknown_count} 个 → 確認待ち")
    print("=" * 60)


if __name__ == "__main__":
    main()
