#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Seed Company Judgment DB — 一回限りの初回投入 script

新「管理会社判定 DB」(Notion) に既存の判定実績を流し込む。
4 ソースから unique 会社を集約 → title (会社名) で重複チェック → なければ upsert。

  1. data/whitelist_companies.txt   → 会社広告可否=可
  2. data/blacklist_companies.txt   → 会社広告可否=不可
  3. data/management_companies.csv の 広告可否=='物件による' → 会社広告可否=物件による
  4. おすすめ DB の Status=確認待ち の 管理会社 (unique) → 会社広告可否=空 (staff 未判定)

優先順位 (同じ会社名が複数 source に出現した場合): whitelist > blacklist > case_by_case > 空
                                                  (より確定的な判定を採用)

冪等: 既に新 DB に同名 row があれば skip。--force で上書き(非推奨)。

使い方:
  ./venv/bin/python scripts/seed_company_db.py --dry-run    # 件数表示のみ
  ./venv/bin/python scripts/seed_company_db.py --seed       # 本実行

環境変数:
  NOTION_API_KEY              — 本体トークン (既存)
  COMPANY_JUDGMENT_DB_ID      — 新「管理会社判定 DB」の ID (.env に追記してから実行)
"""
import os
import sys
import csv
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(line_buffering=True)


NOTION_API_KEY = os.getenv("NOTION_API_KEY")
COMPANY_DB_ID = os.getenv("COMPANY_JUDGMENT_DB_ID")
RECOMMEND_DATABASE_ID = "3171c1974dad80439367df13aa67f012"

DATA_DIR = Path("data")
BLACKLIST_FILE = DATA_DIR / "blacklist_companies.txt"
WHITELIST_FILE = DATA_DIR / "whitelist_companies.txt"
CASE_FILE = DATA_DIR / "management_companies.csv"

LOG_FILE = Path("logs") / "seed_company_db.log"
LOG_FILE.parent.mkdir(exist_ok=True)

H = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

REQ_SLEEP = 0.35  # ~3 req/s, Notion rate limit


def log(msg):
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def post(url, body, tries=4):
    for i in range(tries):
        r = requests.post(url, headers=H, json=body, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "1")))
            continue
        if r.status_code >= 500:
            time.sleep(2 ** i)
            continue
        log(f"  HTTP {r.status_code}: {r.text[:200]}")
        return None
    return None


def query_all(db_id, body):
    out, cursor = [], None
    while True:
        b = dict(body)
        if cursor:
            b["start_cursor"] = cursor
        d = post(f"https://api.notion.com/v1/databases/{db_id}/query", b)
        if not d:
            break
        out += d["results"]
        if not d.get("has_more"):
            break
        cursor = d["next_cursor"]
        time.sleep(REQ_SLEEP)
    return out


def load_txt_set(path):
    if not path.exists():
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def load_case_set():
    if not CASE_FILE.exists():
        return set()
    out = set()
    with open(CASE_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            name = r.get("管理会社名", "").strip()
            status = r.get("広告可否", "").strip()
            if name and status == "物件による":
                out.add(name)
    return out


def collect_pending_companies():
    """おすすめ DB の Status=確認待ち から 管理会社 (rich_text) を unique 抽出"""
    pages = query_all(RECOMMEND_DATABASE_ID, {
        "filter": {"property": "Status", "status": {"equals": "確認待ち"}},
        "page_size": 100,
    })
    out = set()
    for p in pages:
        rt = p["properties"].get("管理会社", {}).get("rich_text") or []
        if rt:
            name = rt[0]["plain_text"].strip()
            if name:
                out.add(name)
    return out


def list_existing_companies():
    """新 DB の既存 row 全部の 会社名 (title) を集める"""
    pages = query_all(COMPANY_DB_ID, {"page_size": 100})
    out = {}
    for p in pages:
        # title プロパティ名は「会社名」想定 (新 DB schema)
        for prop_name, prop_val in p["properties"].items():
            if prop_val.get("type") == "title":
                title = "".join(t["plain_text"] for t in prop_val["title"]).strip()
                if title:
                    out[title] = p["id"]
                break
    return out


def create_company_row(name, judgment):
    """新 DB に 1 row 作成。judgment は '可'/'不可'/'物件による' or None(空)"""
    properties = {
        "会社名": {"title": [{"text": {"content": name}}]},
    }
    if judgment:
        properties["会社広告可否"] = {"select": {"name": judgment}}
    body = {
        "parent": {"database_id": COMPANY_DB_ID},
        "properties": properties,
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=H, json=body, timeout=30)
    if r.status_code == 200:
        return r.json()["id"]
    log(f"  create 失敗 [{name}]: HTTP {r.status_code} {r.text[:200]}")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="件数表示のみ、書込しない")
    ap.add_argument("--seed", action="store_true", help="本実行: 新 DB に upsert")
    ap.add_argument("--pending-only", action="store_true",
                    help="既存 whitelist/blacklist/case は無視、おすすめ DB の確認待ち unique 会社だけを投入 (空判定)")
    args = ap.parse_args()

    if not (args.dry_run or args.seed):
        ap.print_help()
        sys.exit(1)

    if not NOTION_API_KEY:
        log("ERROR: NOTION_API_KEY 未設定")
        sys.exit(2)
    if not COMPANY_DB_ID:
        log("ERROR: COMPANY_JUDGMENT_DB_ID 未設定 (.env に追記してください)")
        sys.exit(2)

    log("=" * 60)
    log(f"Seed Company DB — {'DRY-RUN' if args.dry_run else 'SEED'}")
    log("=" * 60)

    plan = {}
    if args.pending_only:
        log("--pending-only: 既存 whitelist/blacklist/case はスキップ、確認待ち unique のみ投入")
        log("おすすめ DB から Status=確認待ち の 管理会社 を抽出 ...")
        pending = collect_pending_companies()
        log(f"  確認待ち unique 管理会社: {len(pending)}")
        for name in pending:
            plan[name] = None  # 空 (staff 未判定)
    else:
        whitelist = load_txt_set(WHITELIST_FILE)
        blacklist = load_txt_set(BLACKLIST_FILE)
        case_set = load_case_set()
        log(f"local files: whitelist={len(whitelist)} blacklist={len(blacklist)} case={len(case_set)}")

        log("おすすめ DB から Status=確認待ち の 管理会社 を抽出 ...")
        pending = collect_pending_companies()
        log(f"  確認待ち unique 管理会社: {len(pending)}")

        # 集約 (優先順位: 可 > 不可 > 物件による > 空)
        for name in whitelist:
            plan[name] = "可"
        for name in blacklist:
            if name not in plan:
                plan[name] = "不可"
        for name in case_set:
            if name not in plan:
                plan[name] = "物件による"
        for name in pending:
            if name not in plan:
                plan[name] = None  # 空 (staff 未判定)

    by_judgment = {"可": 0, "不可": 0, "物件による": 0, None: 0}
    for j in plan.values():
        by_judgment[j] = by_judgment.get(j, 0) + 1

    log(f"投入計画: 合計 {len(plan)} 社")
    log(f"  可          : {by_judgment.get('可', 0)}")
    log(f"  不可        : {by_judgment.get('不可', 0)}")
    log(f"  物件による  : {by_judgment.get('物件による', 0)}")
    log(f"  空 (未判定) : {by_judgment.get(None, 0)}")

    if args.dry_run:
        log("DRY-RUN 完了 (書込なし)")
        return

    log("\n新 DB 既存 row を取得 ...")
    existing = list_existing_companies()
    log(f"  既存 row: {len(existing)}")

    new_count, skip_count, fail_count = 0, 0, 0
    for i, (name, judgment) in enumerate(sorted(plan.items())):
        if name in existing:
            skip_count += 1
            continue
        page_id = create_company_row(name, judgment)
        if page_id:
            new_count += 1
            existing[name] = page_id
        else:
            fail_count += 1
        time.sleep(REQ_SLEEP)
        if (i + 1) % 50 == 0:
            log(f"  進捗 {i + 1}/{len(plan)} (新規 {new_count} / skip {skip_count} / 失敗 {fail_count})")

    log("\n" + "=" * 60)
    log(f"完了: 新規 {new_count} / skip {skip_count} / 失敗 {fail_count}")
    log("=" * 60)


if __name__ == "__main__":
    main()
