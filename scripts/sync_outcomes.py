#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Sync Outcomes - 独立服务

把广告管理 DB(db_defb = 広告管理/ファンテイズ-forrent)里每个物件的 反響数(rollup),
按 REINS_ID 聚合后写回 おすすめ DB 的「実反響数」字段, 同时累积到
data/outcomes_history.csv (供 LR 训练 / score 校准 / 月次重训用)。

这是 self-learning-loop Phase 1 Step 1 (zero dip 风险), 仅建数据通路, 不影响
任何评分 / 写 TOP / 撤退判定逻辑。

输入: 広告管理 DB(defb9f3...) 的全部投放记录 (n ~ 656)
   貴社物件コード(title) = REINS_ID
   反響数(rollup)        = 该投放的反响数
   start/end             = 投放时间窗
   score                 = 投放时分(快照)

输出:
  - おすすめ DB.実反響数 = sum(反響数) by REINS_ID
  - data/outcomes_history.csv: 每行一个投放记录, 含 features

调度: launchd jp.ango.syncoutcomes 每天 03:00 JST.

环境变量:
  DRY_RUN=1 (默认): 不真做, 只打印
  DRY_RUN=0       : 真同步
"""
import os
import re
import sys
import csv
import requests
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(line_buffering=True)


# ============================================================
# 配置
# ============================================================
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
ADS_DB_ID = "defb9f3bccc34ae487b441ef7a1c0754"        # 広告管理/ファンテイズ-forrent
OSUSUME_DB_ID = "3171c1974dad80439367df13aa67f012"   # 新着物件おすすめ

OUTCOMES_CSV = Path("data") / "outcomes_history.csv"

DRY_RUN = os.getenv("DRY_RUN", "1") == "1"

LOG_FILE = Path("logs") / "sync_outcomes.log"
LOG_FILE.parent.mkdir(exist_ok=True)
OUTCOMES_CSV.parent.mkdir(exist_ok=True)

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


def notion_update(page_id, properties):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    try:
        r = requests.patch(url, headers=notion_headers,
                           json={"properties": properties}, timeout=60)
        return r.status_code == 200
    except Exception as e:
        log(f"  Notion update 错误: {e}")
        return False


# ============================================================
# 字段提取
# ============================================================
def get_title(props, field):
    arr = props.get(field, {}).get("title", [])
    return arr[0]["plain_text"] if arr else ""


def get_rich_text(props, field):
    arr = props.get(field, {}).get("rich_text", [])
    return arr[0]["plain_text"] if arr else ""


def get_number(props, field):
    return props.get(field, {}).get("number")


def get_rollup_number(props, field):
    """rollup 类型字段, 内部 number"""
    rollup = props.get(field, {}).get("rollup", {})
    if rollup.get("type") == "number":
        return rollup.get("number")
    return None


def get_date_start(props, field):
    d = props.get(field, {}).get("date") or {}
    return d.get("start")


def get_formula_date(props, field):
    """formula 字段返回 date 类型"""
    f = props.get(field, {}).get("formula", {})
    if f.get("type") == "date":
        return (f.get("date") or {}).get("start")
    if f.get("type") == "string":
        return f.get("string")
    return None


# 広告管理 DB の 貴社物件コード は ad-system 内部 ID:
#   AI100138178064 / fng100138625774 → 前置 "AI"/"fng" を剥がせば REINS_ID
#   og844 / og50    → ad-system 独自 ID (REINS 無し物件), join 対象外
_REINS_PREFIX = re.compile(r"^(AI|fng)(\d{12})$")


def extract_reins_id(ad_code):
    """貴社物件コード から REINS_ID (12 桁) を取り出す。一致しなければ None。"""
    if not ad_code:
        return None
    m = _REINS_PREFIX.match(ad_code.strip())
    if m:
        return m.group(2)
    return None


# ============================================================
# Main
# ============================================================
def main():
    log("=" * 60)
    log(f"Sync Outcomes — DRY_RUN={DRY_RUN}")
    log("=" * 60)

    # 1. 拉广告管理 DB 全部投放
    log(f"\n拉取広告管理 DB ({ADS_DB_ID})...")
    ads_rows = notion_query_all(ADS_DB_ID)
    log(f"  → {len(ads_rows)} 投放记录")

    # 2. 按 REINS_ID 聚合反响数
    by_reins = defaultdict(int)
    csv_rows = []
    skipped_no_reins = 0
    skipped_non_reins = 0  # og… のような ad-system 独自 ID
    for row in ads_rows:
        props = row["properties"]
        ad_code = get_title(props, "貴社物件コード")
        if not ad_code:
            skipped_no_reins += 1
            continue
        reins_id = extract_reins_id(ad_code)
        if not reins_id:
            skipped_non_reins += 1
            continue
        reverbs = get_rollup_number(props, "反響数") or 0
        by_reins[reins_id] += int(reverbs)

        # 记录 csv (每个投放一行)
        csv_rows.append({
            "reins_id": reins_id,
            "ad_code": ad_code,
            "ad_page_id": row["id"],
            "ad_created_time": row.get("created_time", ""),
            "score_snapshot": get_number(props, "score"),
            "rent": get_number(props, "賃料"),
            "area": get_number(props, "面積"),
            "age": get_number(props, "築年"),
            "layout": get_rich_text(props, "間取り"),
            "station": get_rich_text(props, "最寄駅"),
            "start_date": get_formula_date(props, "start"),
            "end_date": get_date_start(props, "end"),
            "reverbs": int(reverbs),
            "has_rev": 1 if reverbs > 0 else 0,
        })

    log(f"  REINS_ID-聚合: {len(by_reins)} 个物件 (跳过 {skipped_no_reins} 缺 ad_code, {skipped_non_reins} 个 ad-system 独自 ID)")
    log(f"  反响>0 的物件: {sum(1 for v in by_reins.values() if v > 0)}")

    # 3. 拉 おすすめ DB 全部 row, 找匹配的 REINS_ID
    log(f"\n拉取おすすめ DB ({OSUSUME_DB_ID})...")
    osusume_rows = notion_query_all(OSUSUME_DB_ID)
    log(f"  → {len(osusume_rows)} 行")

    # 4. 写 実反響数 字段
    updated = 0
    skipped_no_match = 0
    skipped_zero = 0
    for row in osusume_rows:
        props = row["properties"]
        reins_id = get_title(props, "REINS_ID")
        if not reins_id:
            continue
        new_value = by_reins.get(reins_id)
        if new_value is None:
            skipped_no_match += 1
            continue
        cur_value = get_number(props, "実反響数")
        if cur_value == new_value:
            continue  # 一致, 不重写
        if DRY_RUN:
            log(f"  [DRY] {reins_id}: 実反響数 {cur_value} → {new_value}")
            updated += 1
        else:
            ok = notion_update(row["id"], {"実反響数": {"number": new_value}})
            if ok:
                updated += 1

    log(f"\n更新 おすすめ.実反響数: {updated} 件 (跳过 {skipped_no_match} 件无投放记录的 row)")

    # 5. 写/append outcomes_history.csv
    if csv_rows:
        fieldnames = list(csv_rows[0].keys())
        write_header = not OUTCOMES_CSV.exists()
        if DRY_RUN:
            log(f"  [DRY] 会写 csv: {len(csv_rows)} 行 → {OUTCOMES_CSV} ({'新建' if write_header else 'append'})")
        else:
            mode = "w" if write_header else "a"
            with open(OUTCOMES_CSV, mode, encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if write_header:
                    writer.writeheader()
                for r in csv_rows:
                    writer.writerow(r)
            log(f"  csv 写入: {len(csv_rows)} 行 → {OUTCOMES_CSV} ({'新建' if write_header else 'append'})")

    log("\n" + "=" * 60)
    log("完成!")
    if DRY_RUN:
        log("  [DRY_RUN] 未实际写。设 DRY_RUN=0 真做")
    log("=" * 60)


if __name__ == "__main__":
    main()
