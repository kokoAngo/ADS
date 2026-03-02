#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
更新TOP推荐物件到Notion数据库
从本次新评估的物件中选取TOP 3，保留最新6个，删除最早的记录
"""

import os
import requests
from datetime import datetime
from pathlib import Path

# Notion配置
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
SOURCE_DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"  # 原始物件数据库
TARGET_DATABASE_ID = "3171c1974dad80439367df13aa67f012"  # 新着物件おすすめ

MAX_RECOMMENDATIONS = 6  # 最多保留6个
NEW_PROPERTIES_CACHE = Path(__file__).parent.parent / "data" / "new_properties_cache.txt"

notion_headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json'
}


def load_new_properties_cache():
    """加载本次新评估的物件ID列表"""
    if not NEW_PROPERTIES_CACHE.exists():
        return set()

    with open(NEW_PROPERTIES_CACHE, 'r', encoding='utf-8') as f:
        ids = {line.strip() for line in f if line.strip()}

    return ids


def get_top_properties(limit=3, filter_ids=None):
    """获取推薦点数最高的物件

    Args:
        limit: 返回数量限制
        filter_ids: 如果提供，只返回这些ID中的物件
    """
    url = f"https://api.notion.com/v1/databases/{SOURCE_DATABASE_ID}/query"

    payload = {
        "filter": {
            "and": [
                {
                    "property": "推薦点数",
                    "number": {"is_not_empty": True}
                },
                {
                    "property": "推薦点数",
                    "number": {"greater_than": 0}
                }
            ]
        },
        "sorts": [
            {
                "property": "推薦点数",
                "direction": "descending"
            }
        ],
        "page_size": 100  # 获取更多以便过滤
    }

    response = requests.post(url, headers=notion_headers, json=payload, timeout=30)

    if response.status_code != 200:
        print(f"获取物件失败: {response.status_code}")
        return []

    results = response.json().get("results", [])
    properties_list = []

    for page in results:
        props = page["properties"]

        # 提取REINS_ID
        reins_id = ""
        if props.get("REINS_ID", {}).get("title"):
            reins_id = props["REINS_ID"]["title"][0]["plain_text"]

        # 如果提供了filter_ids，只保留在列表中的物件
        if filter_ids and reins_id not in filter_ids:
            continue

        # 提取推薦点数
        score = props.get("推薦点数", {}).get("number", 0) or 0

        # 提取物件名（从建物名或地址）
        building_name = ""
        if props.get("所在_建物名", {}).get("rich_text"):
            building_name = props["所在_建物名"]["rich_text"][0]["plain_text"]
        elif props.get("所在地", {}).get("rich_text"):
            building_name = props["所在地"]["rich_text"][0]["plain_text"]

        if reins_id:
            properties_list.append({
                "reins_id": reins_id,
                "score": round(score, 1),
                "building_name": building_name[:50] if building_name else ""
            })

        # 如果已经收集够了，提前退出
        if len(properties_list) >= limit:
            break

    return properties_list[:limit]


def get_existing_recommendations():
    """获取目标数据库中现有的推荐物件（按公開日排序，最早的在前）"""
    url = f"https://api.notion.com/v1/databases/{TARGET_DATABASE_ID}/query"

    payload = {
        "sorts": [
            {
                "property": "公開日",
                "direction": "ascending"  # 最早的在前面
            }
        ]
    }

    response = requests.post(url, headers=notion_headers, json=payload, timeout=30)

    if response.status_code != 200:
        print(f"获取现有推荐失败: {response.status_code}")
        return []

    results = response.json().get("results", [])
    existing = []

    for page in results:
        props = page["properties"]
        reins_id = ""
        if props.get("REINS_ID", {}).get("title"):
            reins_id = props["REINS_ID"]["title"][0]["plain_text"]

        existing.append({
            "page_id": page["id"],
            "reins_id": reins_id
        })

    return existing


def delete_page(page_id):
    """删除（归档）页面"""
    url = f"https://api.notion.com/v1/pages/{page_id}"

    data = {"archived": True}

    response = requests.patch(url, headers=notion_headers, json=data, timeout=30)
    return response.status_code == 200


def create_recommendation(prop):
    """创建新的推荐物件"""
    url = "https://api.notion.com/v1/pages"

    data = {
        "parent": {"database_id": TARGET_DATABASE_ID},
        "properties": {
            "REINS_ID": {
                "title": [{"text": {"content": prop["reins_id"]}}]
            },
            "推薦点数": {
                "number": prop["score"]
            },
            "物件名": {
                "rich_text": [{"text": {"content": prop["building_name"]}}] if prop["building_name"] else []
            },
            "公開日": {
                "date": {"start": datetime.now().strftime("%Y-%m-%d")}
            },
            "状態": {
                "status": {"name": "広告待ち"}
            }
        }
    }

    response = requests.post(url, headers=notion_headers, json=data, timeout=30)
    return response.status_code == 200


def main():
    print("=" * 60)
    print("更新TOP推荐物件（从新物件中选取）")
    print("=" * 60)

    # 0. 加载本次新物件缓存
    print("\n0. 加载新物件缓存...")
    new_property_ids = load_new_properties_cache()

    if not new_property_ids:
        print("   没有新物件缓存，跳过更新")
        print("   （请先运行 predict_and_update_notion_v2.py）")
        return

    print(f"   本次新物件: {len(new_property_ids)} 个")

    # 1. 从新物件中获取TOP 3（有推薦点数的）
    print("\n1. 从新物件中获取TOP 3...")
    top_properties = get_top_properties(3, filter_ids=new_property_ids)

    if not top_properties:
        print("   新物件中没有推薦点数>=6的物件，跳过更新")
        return

    for i, prop in enumerate(top_properties, 1):
        print(f"   [{i}] {prop['reins_id']}: 推薦点数={prop['score']}")

    # 2. 获取现有推荐
    print("\n2. 获取现有推荐...")
    existing = get_existing_recommendations()
    existing_ids = {e["reins_id"] for e in existing}
    print(f"   现有 {len(existing)} 个推荐")

    # 3. 过滤掉已存在的物件（去重）
    new_properties = [p for p in top_properties if p["reins_id"] not in existing_ids]
    skipped = len(top_properties) - len(new_properties)

    if skipped > 0:
        print(f"\n3. 去重检查...")
        print(f"   跳过 {skipped} 个已存在的物件")

    if not new_properties:
        print("   所有TOP物件已存在，无需更新")
        return

    print(f"   将添加 {len(new_properties)} 个新物件")

    # 4. 计算需要删除多少个（确保添加后不超过MAX_RECOMMENDATIONS）
    current_count = len(existing)
    new_count = len(new_properties)
    total_after_add = current_count + new_count

    to_delete = max(0, total_after_add - MAX_RECOMMENDATIONS)

    if to_delete > 0:
        print(f"\n4. 删除最早的 {to_delete} 个...")
        for i in range(to_delete):
            if i < len(existing):
                if delete_page(existing[i]["page_id"]):
                    print(f"   ✓ 删除 {existing[i]['reins_id']}")
                else:
                    print(f"   ✗ 删除失败")
    else:
        print(f"\n4. 无需删除（当前{current_count}个，添加{new_count}个后共{total_after_add}个）")

    # 5. 添加新物件
    print(f"\n5. 添加 {len(new_properties)} 个新推荐...")
    success = 0
    for prop in new_properties:
        if create_recommendation(prop):
            print(f"   ✓ {prop['reins_id']} (推薦点数={prop['score']}) 添加成功")
            success += 1
        else:
            print(f"   ✗ {prop['reins_id']} 添加失败")

    print("\n" + "=" * 60)
    print(f"完成! 成功添加 {success}/{len(new_properties)} 个推荐")
    print("=" * 60)


if __name__ == "__main__":
    main()
