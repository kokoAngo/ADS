#!/usr/bin/env python
# -*- coding: utf-8 -*-
# DEPRECATED 2026-04-28: 已被 process_pipeline.py 替代。
# 仍引用废弃的 PENDING_DATABASE_ID (3181...) 和旧的 双 TOP DB 模型, 请勿手动跑。
"""
更新TOP推荐物件到Notion数据库
- 広告可=可 的物件 → 新着物件おすすめ (直接使用)
- 広告可=確認待ち 的物件 → 確認待ち物件 (需确认)
"""

import os
import requests
from datetime import datetime
from pathlib import Path

# Notion配置
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
SOURCE_DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"  # 原始物件数据库
RECOMMEND_DATABASE_ID = "3171c1974dad80439367df13aa67f012"  # 新着物件おすすめ (広告可=可)
PENDING_DATABASE_ID = "3181c1974dad80279cb7dfdeb92b946f"  # 確認待ち物件

MAX_RECOMMENDATIONS = 20  # 每个表最多保留20个
MIN_SCORE_THRESHOLD = 5.5  # 確認待ち物件的阈值
MIN_SCORE_RECOMMEND = 5.5  # 新着物件おすすめ的阈值
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


def get_top_properties_by_status(filter_ids=None, limit=None, today_only=False, min_score=None):
    """获取推薦点数最高的物件，按広告可状态分组

    Args:
        filter_ids: 只获取这些ID的物件
        limit: 每组最多获取几个（None表示不限制）
        today_only: 是否只获取今天新增的物件
        min_score: 最低分数阈值（None表示不限制）

    Returns:
        dict: {"可": [...], "確認待ち": [...]}
    """
    url = f"https://api.notion.com/v1/databases/{SOURCE_DATABASE_ID}/query"

    filters = [
        {"property": "推薦点数", "number": {"is_not_empty": True}},
        {"property": "推薦点数", "number": {"greater_than": 0}}
    ]

    # 分数阈值筛选
    if min_score is not None:
        filters.append({"property": "推薦点数", "number": {"greater_than_or_equal_to": min_score}})

    # 如果只获取今天的物件
    if today_only:
        today = datetime.now().strftime("%Y-%m-%d")
        filters.append({"timestamp": "created_time", "created_time": {"on_or_after": today}})

    payload = {
        "filter": {"and": filters},
        "sorts": [
            {"property": "推薦点数", "direction": "descending"}
        ],
        "page_size": 100
    }

    response = requests.post(url, headers=notion_headers, json=payload, timeout=30)

    if response.status_code != 200:
        print(f"获取物件失败: {response.status_code}")
        return {"可": [], "確認待ち": []}

    results = response.json().get("results", [])

    # 按広告可状态分组
    approved = []  # 広告可=可
    pending = []   # 広告可=確認待ち

    for page in results:
        props = page["properties"]

        # 提取REINS_ID
        reins_id = ""
        if props.get("REINS_ID", {}).get("title"):
            reins_id = props["REINS_ID"]["title"][0]["plain_text"]

        # 如果提供了filter_ids，只保留在列表中的物件
        if filter_ids and reins_id not in filter_ids:
            continue

        # 提取広告可状态
        ad_status = ""
        if props.get("広告可", {}).get("select"):
            ad_status = props["広告可"]["select"].get("name", "")

        # 提取推薦点数
        score = props.get("推薦点数", {}).get("number", 0) or 0

        # 提取物件名
        building_name = ""
        if props.get("所在_建物名", {}).get("rich_text"):
            building_name = props["所在_建物名"]["rich_text"][0]["plain_text"]
        elif props.get("所在地", {}).get("rich_text"):
            building_name = props["所在地"]["rich_text"][0]["plain_text"]

        # 提取管理会社
        management_company = ""
        if props.get("管理会社", {}).get("rich_text"):
            management_company = props["管理会社"]["rich_text"][0]["plain_text"]

        if reins_id:
            prop_data = {
                "reins_id": reins_id,
                "score": round(score, 1),
                "building_name": building_name[:50] if building_name else "",
                "management_company": management_company[:50] if management_company else "",
                "ad_status": ad_status
            }

            # 如果设置了limit则限制数量，否则不限制
            if ad_status == "可":
                if limit is None or len(approved) < limit:
                    approved.append(prop_data)
            elif ad_status == "確認待ち":
                if limit is None or len(pending) < limit:
                    pending.append(prop_data)

    return {"可": approved, "確認待ち": pending}


def get_existing_items(database_id):
    """获取目标数据库中现有的物件（按公開日排序，最早的在前）"""
    url = f"https://api.notion.com/v1/databases/{database_id}/query"

    payload = {
        "sorts": [
            {"property": "公開日時", "direction": "ascending"}
        ]
    }

    response = requests.post(url, headers=notion_headers, json=payload, timeout=30)

    if response.status_code != 200:
        print(f"获取现有数据失败: {response.status_code}")
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


def create_item(database_id, prop, status_name="広告待ち"):
    """创建新的推荐物件"""
    url = "https://api.notion.com/v1/pages"

    data = {
        "parent": {"database_id": database_id},
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
            "管理会社": {
                "rich_text": [{"text": {"content": prop["management_company"]}}] if prop["management_company"] else []
            },
            "公開日時": {
                "date": {"start": datetime.now().strftime("%Y-%m-%d")}
            },
            "Status": {
                "status": {"name": status_name}
            }
        }
    }

    response = requests.post(url, headers=notion_headers, json=data, timeout=30)
    return response.status_code == 200


def update_database(database_id, db_name, properties):
    """更新指定数据库"""
    if not properties:
        print(f"\n【{db_name}】没有新物件")
        return 0

    print(f"\n{'='*50}")
    print(f"【{db_name}】")
    print(f"{'='*50}")

    # 获取现有数据
    existing = get_existing_items(database_id)
    existing_ids = {e["reins_id"] for e in existing}
    print(f"现有: {len(existing)} 个")

    # 过滤已存在的
    new_items = [p for p in properties if p["reins_id"] not in existing_ids]
    if not new_items:
        print("所有物件已存在，无需更新")
        return 0

    print(f"待添加: {len(new_items)} 个")
    for p in new_items:
        print(f"  - {p['reins_id']}: {p['score']}分, {p['management_company']}")

    # 计算需要删除多少
    total_after = len(existing) + len(new_items)
    to_delete = max(0, total_after - MAX_RECOMMENDATIONS)

    if to_delete > 0:
        print(f"\n删除最早的 {to_delete} 个...")
        for i in range(to_delete):
            if i < len(existing):
                if delete_page(existing[i]["page_id"]):
                    print(f"  ✓ 删除 {existing[i]['reins_id']}")

    # 添加新物件
    print(f"\n添加 {len(new_items)} 个...")
    success = 0
    status_name = "広告待ち"  # 两个数据库都用同样的状态

    for prop in new_items:
        if create_item(database_id, prop, status_name):
            print(f"  ✓ {prop['reins_id']} ({prop['score']}分)")
            success += 1
        else:
            print(f"  ✗ {prop['reins_id']} 添加失败")

    return success


def main():
    import sys

    # 检查参数
    force_all = "--all" in sys.argv  # 获取全部TOP（不限日期）
    today_mode = "--today" in sys.argv or (not force_all)  # 默认只获取今天的

    today_str = datetime.now().strftime("%Y-%m-%d")

    print("=" * 60)
    print("更新TOP推荐物件")
    print(f"  - 広告可=可 & >= {MIN_SCORE_RECOMMEND}分 → 新着物件おすすめ")
    print(f"  - 広告可=確認待ち & >= {MIN_SCORE_THRESHOLD}分 → 確認待ち物件")
    if force_all:
        print("  [模式: 全部物件]")
    else:
        print(f"  [模式: 今日新增 ({today_str})]")
    print("=" * 60)

    # 1. 获取高分物件（按広告可状态分组，使用不同阈值）
    # 新着物件おすすめ: >= 6.5分 且 広告可=可
    if force_all:
        print(f"\n1a. 获取全部>={MIN_SCORE_RECOMMEND}分且広告可=可的物件...")
        grouped_recommend = get_top_properties_by_status(min_score=MIN_SCORE_RECOMMEND, today_only=False)
        print(f"\n1b. 获取全部>={MIN_SCORE_THRESHOLD}分且広告可=確認待ち的物件...")
        grouped_pending = get_top_properties_by_status(min_score=MIN_SCORE_THRESHOLD, today_only=False)
    else:
        print(f"\n1a. 获取今日({today_str})>={MIN_SCORE_RECOMMEND}分且広告可=可的物件...")
        grouped_recommend = get_top_properties_by_status(min_score=MIN_SCORE_RECOMMEND, today_only=True)
        print(f"\n1b. 获取今日({today_str})>={MIN_SCORE_THRESHOLD}分且広告可=確認待ち的物件...")
        grouped_pending = get_top_properties_by_status(min_score=MIN_SCORE_THRESHOLD, today_only=True)

    print(f"   広告可=可 (>={MIN_SCORE_RECOMMEND}分): {len(grouped_recommend['可'])} 个")
    print(f"   広告可=確認待ち (>={MIN_SCORE_THRESHOLD}分): {len(grouped_pending['確認待ち'])} 个")

    # 2. 更新两个数据库
    success_approved = update_database(RECOMMEND_DATABASE_ID, "新着物件おすすめ", grouped_recommend["可"])
    success_pending = update_database(PENDING_DATABASE_ID, "確認待ち物件", grouped_pending["確認待ち"])

    print("\n" + "=" * 60)
    print(f"完成!")
    print(f"  新着物件おすすめ: +{success_approved} 个")
    print(f"  確認待ち物件: +{success_pending} 个")
    print("=" * 60)


if __name__ == "__main__":
    main()
