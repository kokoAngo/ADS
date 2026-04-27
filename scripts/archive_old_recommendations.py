#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Archive Old Recommendations - 独立服务

把两个 TOP DB 里 Status 是终态(取下済 / 広告済)且 Created time 距今超过
ARCHIVE_AFTER_DAYS 天的 row, 通过 Notion archive (软归档, archived=true)
移到回收站, 保持 DB 体积可控。

软归档说明: Notion 的 archived 是可恢复的, row 仍在 DB 里, 只是不在默认视图。
若需要 A/B 复盘, 可在 Notion UI 把 view filter 改成 "Show archived" 看回历史。

调度: launchd jp.ango.archiverecommendations 每周日 02:00 JST 跑一次。

环境变量:
  DRY_RUN=1  (默认): 只打印待归档清单, 不真做
  DRY_RUN=0       : 真执行 archive
  ARCHIVE_AFTER_DAYS=30 (默认): 终态超过 N 天才归档
"""
import os
import sys
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(line_buffering=True)


# ============================================================
# 配置
# ============================================================
NOTION_API_KEY = os.getenv("NOTION_API_KEY")

# (显示名, DB ID, 终态 Status 列表)
TARGET_DATABASES = [
    ("新着物件おすすめ", "3171c1974dad80439367df13aa67f012", ["取下済"]),
    ("確認待ち物件",      "3181c1974dad80279cb7dfdeb92b946f", ["広告済"]),
]

ARCHIVE_AFTER_DAYS = int(os.getenv("ARCHIVE_AFTER_DAYS", "30"))
DRY_RUN = os.getenv("DRY_RUN", "1") == "1"

LOG_FILE = Path("logs") / "archive_old_recommendations.log"
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
def notion_query(db_id, filter_obj=None, page_size=100):
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    all_results = []
    cursor = None
    while True:
        payload = {"page_size": page_size}
        if filter_obj:
            payload["filter"] = filter_obj
        if cursor:
            payload["start_cursor"] = cursor
        try:
            r = requests.post(url, headers=notion_headers, json=payload, timeout=60)
            data = r.json()
        except Exception as e:
            log(f"  Notion query 错误: {e}")
            break
        if "results" not in data:
            log(f"  Notion query error: {data}")
            break
        all_results.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return all_results


def notion_archive(page_id):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    try:
        r = requests.patch(url, headers=notion_headers, json={"archived": True}, timeout=30)
        return r.status_code == 200
    except Exception as e:
        log(f"  Notion archive 错误: {e}")
        return False


# ============================================================
# Main
# ============================================================
def archive_db(db_label, db_id, terminal_statuses, cutoff):
    """对一个 TOP DB 找出终态 + 超过 cutoff 的 row, 归档"""
    log(f"\n=== {db_label} ===")
    # filter: Status ∈ terminal_statuses
    if len(terminal_statuses) > 1:
        filter_obj = {"or": [{"property": "Status", "status": {"equals": s}} for s in terminal_statuses]}
    else:
        filter_obj = {"property": "Status", "status": {"equals": terminal_statuses[0]}}

    rows = notion_query(db_id, filter_obj=filter_obj)
    log(f"  终态 ({'/'.join(terminal_statuses)}) row: {len(rows)}")

    candidates = []
    for r in rows:
        created = r.get("created_time", "")
        if not created:
            continue
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created_dt < cutoff:
            reins_id = ""
            if r["properties"].get("REINS_ID", {}).get("title"):
                reins_id = r["properties"]["REINS_ID"]["title"][0]["plain_text"]
            candidates.append({
                "page_id": r["id"],
                "reins_id": reins_id,
                "created": created,
            })

    log(f"  超过 {ARCHIVE_AFTER_DAYS} 天的: {len(candidates)} 件")

    archived = 0
    failed = 0
    for c in candidates:
        if DRY_RUN:
            log(f"    [DRY_RUN] would archive: {c['reins_id']} (created {c['created'][:10]})")
        else:
            if notion_archive(c["page_id"]):
                log(f"    ✓ archive: {c['reins_id']} (created {c['created'][:10]})")
                archived += 1
            else:
                log(f"    ✗ archive 失败: {c['reins_id']}")
                failed += 1

    return len(candidates), archived, failed


def main():
    log("=" * 60)
    log(f"Archive Old Recommendations — DRY_RUN={DRY_RUN} ARCHIVE_AFTER_DAYS={ARCHIVE_AFTER_DAYS}")
    log("=" * 60)

    cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_AFTER_DAYS)
    log(f"截止时刻 (UTC): {cutoff.isoformat()}")

    total_candidates = 0
    total_archived = 0
    total_failed = 0
    for label, db_id, terminal in TARGET_DATABASES:
        c, a, f = archive_db(label, db_id, terminal, cutoff)
        total_candidates += c
        total_archived += a
        total_failed += f

    log("\n" + "=" * 60)
    log("完成!")
    log(f"  候选(终态 + 超 {ARCHIVE_AFTER_DAYS} 天): {total_candidates}")
    if DRY_RUN:
        log(f"  [DRY_RUN] 未执行任何 archive。设 DRY_RUN=0 真做")
    else:
        log(f"  archive 成功: {total_archived}")
        if total_failed:
            log(f"  archive 失败: {total_failed}")
    log("=" * 60)


if __name__ == "__main__":
    main()
