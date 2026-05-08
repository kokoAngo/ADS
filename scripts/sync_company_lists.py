#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Sync Company Lists - 独立服务

读取「管理会社判定 DB」 (会社単位、env COMPANY_JUDGMENT_DB_ID) staff が判定済みの行を
data/blacklist_companies.txt / whitelist_companies.txt / data/management_companies.csv (case_by_case)
に反映、process_pipeline の次回実行で会社判定が即座に効くようにする。

工作流(staff 视角):
  staff は「管理会社判定 DB」だけを見て、各会社の「会社広告可否」を:
    可 / 不可 / 物件による
  と select する (空のままなら本 script は無視)

工作流(脚本视角):
  1. 管理会社判定 DB から「会社広告可否 != 空」の全行を取得
  2. 1 社 1 判定 (DB 構造上重複なし) — title (会社名) と select (会社広告可否) を抜く
  3. 既存 blacklist / whitelist / case_by_case と突合
  4. 新規追加 / 別カテゴリへ移動を反映
  5. Notion 側の判定は変更しない (audit 記録)

幂等: 重複実行しても変化のないエントリは skip。

調度: launchd 毎日 1 回 (jp.ango.synccompanylists)。
"""
import os
import sys
import csv
import re
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
COMPANY_JUDGMENT_DB_ID = os.getenv("COMPANY_JUDGMENT_DB_ID")  # 管理会社判定 DB (会社単位)

DATA_DIR = Path("data")
BLACKLIST_FILE = DATA_DIR / "blacklist_companies.txt"
WHITELIST_FILE = DATA_DIR / "whitelist_companies.txt"
CASE_FILE = DATA_DIR / "management_companies.csv"   # 物件による 在 CSV 里 (与 process_pipeline 一致)

LOG_FILE = Path("logs") / "sync_company_lists.log"
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


# ============================================================
# 文件读写助手
# ============================================================
def load_txt_set(path):
    if not path.exists():
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def append_txt(path, names):
    """追加 names 到 path, 自动去重(读现有 set 后比较)"""
    existing = load_txt_set(path)
    new = sorted(set(names) - existing)
    if not new:
        return 0
    with open(path, "a", encoding="utf-8") as f:
        for n in new:
            f.write(n + "\n")
    return len(new)


def remove_from_txt(path, names_to_remove):
    """从 path 里移除指定 names, 返回移除数"""
    if not path.exists():
        return 0
    existing = load_txt_set(path)
    target_remove = existing & set(names_to_remove)
    if not target_remove:
        return 0
    remaining = sorted(existing - target_remove)
    with open(path, "w", encoding="utf-8") as f:
        for n in remaining:
            f.write(n + "\n")
    return len(target_remove)


CSV_DEFAULT_FIELDS = ["管理会社名", "広告可否", "対応区域", "連絡方法", "電話番号"]


def _read_csv_rows():
    """读取 CSV 全部行(保留所有状态), 返回 (fieldnames, rows[])"""
    if not CASE_FILE.exists():
        return CSV_DEFAULT_FIELDS, []
    with open(CASE_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or CSV_DEFAULT_FIELDS)
        return fieldnames, list(reader)


def _write_csv_rows(fieldnames, rows):
    with open(CASE_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def load_case_set():
    """返回当前 広告可否=='物件による' 的公司 set (与 process_pipeline 语义一致)"""
    _, rows = _read_csv_rows()
    return {
        r.get("管理会社名", "").strip()
        for r in rows
        if r.get("管理会社名", "").strip() and r.get("広告可否", "").strip() == "物件による"
    }


def upsert_case_csv(name):
    """把 name 加入 CSV 以「物件による」状态。如已有同名+物件による行 → no-op; 没有 → 新增一行"""
    fieldnames, rows = _read_csv_rows()
    for r in rows:
        if r.get("管理会社名", "").strip() == name and r.get("広告可否", "").strip() == "物件による":
            return False  # 已经存在
    new_row = {fn: "" for fn in fieldnames}
    new_row["管理会社名"] = name
    new_row["広告可否"] = "物件による"
    rows.append(new_row)
    _write_csv_rows(fieldnames, rows)
    return True


def remove_case_from_csv(names_to_remove):
    """只删 広告可否=='物件による' 且 名在 names_to_remove 里的行。其他状态行保留"""
    if not CASE_FILE.exists():
        return 0
    fieldnames, rows = _read_csv_rows()
    keep = []
    removed = 0
    for r in rows:
        nm = r.get("管理会社名", "").strip()
        st = r.get("広告可否", "").strip()
        if nm in names_to_remove and st == "物件による":
            removed += 1
            continue
        keep.append(r)
    if removed > 0:
        _write_csv_rows(fieldnames, keep)
    return removed


# ============================================================
# 主逻辑
# ============================================================
def collect_judgments():
    """管理会社判定 DB から「会社広告可否 != 空」の row を取得し、{会社名: 判定} の dict に。
    1 社 1 row が前提 (新 DB 構造)。title プロパティ名は schema 依存なので type で検出。"""
    if not COMPANY_JUDGMENT_DB_ID:
        log("ERROR: COMPANY_JUDGMENT_DB_ID 未設定 (.env を確認)")
        return {}
    pages = notion_query(COMPANY_JUDGMENT_DB_ID, filter_obj={
        "property": "会社広告可否",
        "select": {"is_not_empty": True}
    })
    by_company = {}
    for p in pages:
        props = p["properties"]
        company = ""
        for prop_val in props.values():
            if prop_val.get("type") == "title":
                company = "".join(t["plain_text"] for t in prop_val["title"]).strip()
                break
        sel = props.get("会社広告可否", {}).get("select")
        judgment = sel.get("name") if sel else None
        if not company or not judgment:
            continue
        by_company[company] = judgment
    return by_company


def main():
    log("=" * 60)
    log("Sync Company Lists — 独立服务")
    log("=" * 60)

    # 1. 现有名单状态
    blacklist = load_txt_set(BLACKLIST_FILE)
    whitelist = load_txt_set(WHITELIST_FILE)
    case_by_case = load_case_set()
    log(f"现有: blacklist={len(blacklist)} whitelist={len(whitelist)} case_by_case={len(case_by_case)}")

    # 2. 拉 Notion 判定
    log("拉取 管理会社判定 DB 的 会社広告可否 ...")
    judgments = collect_judgments()
    log(f"staff 已填 判定: {len(judgments)} 个公司")

    if not judgments:
        log("没有新的判定, 退出")
        return

    # 3. 应用每个判定
    new_to_white = []
    new_to_black = []
    new_to_case = []
    moved = []  # (company, from_list, to_list)

    for company, judgment in sorted(judgments.items()):
        # 目标列表
        target = {"可": "white", "不可": "black", "物件による": "case"}.get(judgment)
        if not target:
            continue

        # 当前所在列表
        current = None
        if company in whitelist: current = "white"
        elif company in blacklist: current = "black"
        elif company in case_by_case: current = "case"

        if current == target:
            continue  # 已经一致, 跳过

        # 需要移动: 先从原列表移除
        if current == "white":
            remove_from_txt(WHITELIST_FILE, [company])
            whitelist.discard(company)
        elif current == "black":
            remove_from_txt(BLACKLIST_FILE, [company])
            blacklist.discard(company)
        elif current == "case":
            remove_case_from_csv([company])
            case_by_case.discard(company)

        # 加入新列表
        if target == "white":
            new_to_white.append(company)
            whitelist.add(company)
        elif target == "black":
            new_to_black.append(company)
            blacklist.add(company)
        elif target == "case":
            new_to_case.append(company)
            case_by_case.add(company)

        if current:
            moved.append((company, current, target))
            log(f"  移: {company} ({current} → {target})")
        else:
            log(f"  新增: {company} → {target}")

    # 批量写入
    if new_to_white:
        added = append_txt(WHITELIST_FILE, new_to_white)
        log(f"  whitelist 新增 {added} 条")
    if new_to_black:
        added = append_txt(BLACKLIST_FILE, new_to_black)
        log(f"  blacklist 新增 {added} 条")
    if new_to_case:
        for n in new_to_case:
            upsert_case_csv(n)
        log(f"  case_by_case 新增 {len(new_to_case)} 条")

    log("\n" + "=" * 60)
    log(f"完成! 总变更:")
    log(f"  → whitelist:    +{len(new_to_white)}")
    log(f"  → blacklist:    +{len(new_to_black)}")
    log(f"  → case_by_case: +{len(new_to_case)}")
    if moved:
        log(f"  迁移(同公司换列表): {len(moved)} 条")
    log("=" * 60)


if __name__ == "__main__":
    main()
