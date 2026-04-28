#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一次性迁移脚本: 旧「確認待ち物件 DB」→ 「おすすめ DB」

业务背景: 2026-04-28 决定合并两张 TOP DB 为一张, 旧 確認待ち 整体迁过来。

流程:
1. 拉 確認待ち DB 全部 non-archived 行
2. Status 按映射表转换
3. 复制字段到 おすすめ DB
4. 去重: 如果 REINS_ID 已在 おすすめ → 跳过
5. 原行 archived=true 软删

环境变量:
  DRY_RUN=1 (默认): 只打印待迁移清单, 不真做
  DRY_RUN=0       : 真执行迁移

用法:
  DRY_RUN=1 ./venv/bin/python scripts/migrate_pending_to_osusume.py    # dry run
  DRY_RUN=0 ./venv/bin/python scripts/migrate_pending_to_osusume.py    # 真做

注: 迁移脚本是 idempotent 的 (REINS_ID 已存在跳过), 重复跑安全。
跑完一次成功后, 这个文件可以删除/归档。
"""
import os
import sys
import requests
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(line_buffering=True)


# ============================================================
# 配置
# ============================================================
NOTION_API_KEY = os.getenv("NOTION_API_KEY")

OSUSUME_DB    = "3171c1974dad80439367df13aa67f012"  # 新着物件おすすめ (目标)
PENDING_DB    = "3181c1974dad80279cb7dfdeb92b946f"  # 確認待ち物件 (源)

# 旧 → 新 Status 映射
STATUS_MAP = {
    "広告待ち":   "確認待ち",      # 商号未确认, 让 staff 走判定流程
    "広告済":     "掲載指示済み",   # 投放中
    "取下待ち":   "要取り下げ",     # 等撤下
    "取下済":     "取下済み",       # 已撤下 (加 み)
}

DRY_RUN = os.getenv("DRY_RUN", "1") == "1"

LOG_FILE = Path("logs") / "migrate_pending_to_osusume.log"
LOG_FILE.parent.mkdir(exist_ok=True)

notion_headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def log(msg):
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


# ============================================================
# Notion API
# ============================================================
def notion_query_all(db_id):
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    all_results = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        r = requests.post(url, headers=notion_headers, json=payload, timeout=60)
        d = r.json()
        all_results.extend(d.get("results", []))
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    return all_results


def notion_create(db_id, properties):
    url = "https://api.notion.com/v1/pages"
    r = requests.post(url, headers=notion_headers,
                      json={"parent": {"database_id": db_id}, "properties": properties},
                      timeout=60)
    return r.status_code == 200, r


def notion_archive(page_id):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    r = requests.patch(url, headers=notion_headers,
                       json={"archived": True}, timeout=30)
    return r.status_code == 200


def notion_query_by_reins(db_id, reins_id):
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    r = requests.post(url, headers=notion_headers,
                      json={"filter": {"property": "REINS_ID",
                                       "title": {"equals": reins_id}}},
                      timeout=30)
    return r.json().get("results", [])


# ============================================================
# 字段提取助手
# ============================================================
def get_title(props, field):
    arr = props.get(field, {}).get("title", [])
    return arr[0]["plain_text"] if arr else ""


def get_richtext(props, field):
    arr = props.get(field, {}).get("rich_text", [])
    return arr[0]["plain_text"] if arr else ""


def get_select(props, field):
    sel = props.get(field, {}).get("select")
    return sel.get("name") if sel else None


def get_status(props, field):
    st = props.get(field, {}).get("status")
    return st.get("name") if st else None


def get_number(props, field):
    return props.get(field, {}).get("number")


def get_date(props, field):
    d = props.get(field, {}).get("date")
    return d.get("start") if d else None


# ============================================================
# Main
# ============================================================
def build_target_properties(src_props, new_status):
    """从源 row 的 properties 构造目标 row 的 properties"""
    reins_id = get_title(src_props, "REINS_ID")
    bukken_name = get_richtext(src_props, "物件名")
    company = get_richtext(src_props, "管理会社")
    score = get_number(src_props, "推薦点数")
    listing_count = get_number(src_props, "登録店舗数")
    koukai = get_date(src_props, "公開日時")
    kaisha_kahi = get_select(src_props, "会社広告可否")

    target = {
        "REINS_ID": {"title": [{"text": {"content": reins_id}}]},
        "Status": {"status": {"name": new_status}},
    }
    if bukken_name:
        target["物件名"] = {"rich_text": [{"text": {"content": bukken_name}}]}
    if company:
        target["管理会社"] = {"rich_text": [{"text": {"content": company}}]}
    if score is not None:
        target["推薦点数"] = {"number": score}
    if listing_count is not None:
        target["登録店舗数"] = {"number": listing_count}
    if koukai:
        target["公開日時"] = {"date": {"start": koukai}}
    if kaisha_kahi:
        target["会社広告可否"] = {"select": {"name": kaisha_kahi}}

    return target


def main():
    log("=" * 60)
    log(f"Migrate 確認待ち → おすすめ — DRY_RUN={DRY_RUN}")
    log("=" * 60)

    log(f"\n拉取 確認待ち DB ({PENDING_DB})...")
    src_rows = notion_query_all(PENDING_DB)
    log(f"  → {len(src_rows)} 行")

    stats = {"migrated": 0, "skip_dup": 0, "skip_unmapped": 0, "skip_no_reins": 0, "error": 0}

    for i, row in enumerate(src_rows):
        props = row["properties"]
        reins_id = get_title(props, "REINS_ID")
        old_status = get_status(props, "Status") or ""
        bukken_name = get_richtext(props, "物件名")[:25]

        if not reins_id:
            log(f"  [{i+1}/{len(src_rows)}] ✗ 缺 REINS_ID, 跳过")
            stats["skip_no_reins"] += 1
            continue

        new_status = STATUS_MAP.get(old_status)
        if not new_status:
            log(f"  [{i+1}/{len(src_rows)}] ✗ {reins_id} {bukken_name} Status={old_status!r} 不在映射表, 跳过")
            stats["skip_unmapped"] += 1
            continue

        # 去重检查: 看 おすすめ 是否已有同 REINS_ID
        existing = notion_query_by_reins(OSUSUME_DB, reins_id)
        if existing:
            log(f"  [{i+1}/{len(src_rows)}] - {reins_id} {bukken_name} 已在 おすすめ, 跳过 (软删源行)")
            if not DRY_RUN:
                notion_archive(row["id"])
            stats["skip_dup"] += 1
            continue

        target_props = build_target_properties(props, new_status)

        if DRY_RUN:
            log(f"  [{i+1}/{len(src_rows)}] [DRY] {reins_id} {bukken_name} Status: {old_status} → {new_status}")
        else:
            ok, resp = notion_create(OSUSUME_DB, target_props)
            if ok:
                # 软删源行
                notion_archive(row["id"])
                log(f"  [{i+1}/{len(src_rows)}] ✓ {reins_id} {bukken_name} {old_status} → {new_status}")
                stats["migrated"] += 1
            else:
                log(f"  [{i+1}/{len(src_rows)}] ✗ {reins_id} 创建失败: {resp.text[:150]}")
                stats["error"] += 1

    log("\n" + "=" * 60)
    log("完成!")
    for k, v in stats.items():
        if v > 0:
            log(f"  {k}: {v}")
    if DRY_RUN:
        log("\n  [DRY_RUN] 未实际迁移。设 DRY_RUN=0 真做")
    log("=" * 60)


if __name__ == "__main__":
    main()
