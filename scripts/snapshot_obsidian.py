#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Snapshot Obsidian - 独立服务

おすすめ DB の active row (Status ∈ {掲載指示済み, 広告待ち, 掲載保留}) を
日付別 1 ファイルの Markdown でスナップショット保存する。

目的: 変更履歴の厳密追跡 (証拠保全)。Notion 側は last_edited_time しか持たず
差分が辿れないため、定期スナップショットで補完。

出力先: $OBSIDIAN_VAULT/Fango Snapshots/YYYY-MM-DD.md
        既定の vault は ~/Documents/Obsidian Vault/ (env で上書き可)
        同日内 4 cutoff 分のセクションを append (上書きしない)。

調度: launchd jp.ango.snapshotobsidian、毎日 4 回 (11:30, 15:30, 19:30, 23:30 JST)。

引数:
  --dry-run: Obsidian 書き込みせず Markdown を stdout に出すだけ
"""
import os
import sys
import argparse
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(line_buffering=True)


# ============================================================
# 配置
# ============================================================
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
OSUSUME_DB_ID = "3171c1974dad80439367df13aa67f012"

JST = timezone(timedelta(hours=9))

ACTIVE_STATUSES = ["掲載指示済み", "広告待ち", "掲載保留"]

OBSIDIAN_VAULT = Path(
    os.getenv("OBSIDIAN_VAULT", "/Users/developer_recika/Documents/Obsidian Vault")
)
SNAPSHOT_DIR = OBSIDIAN_VAULT / "Fango Snapshots"

LOG_FILE = Path("logs") / "snapshot_obsidian.log"
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
def notion_query_active():
    """Status ∈ ACTIVE_STATUSES の row 全部 (pagination 対応)"""
    url = f"https://api.notion.com/v1/databases/{OSUSUME_DB_ID}/query"
    or_filter = [{"property": "Status", "status": {"equals": s}} for s in ACTIVE_STATUSES]
    payload = {"filter": {"or": or_filter}, "page_size": 100}
    all_results = []
    cursor = None
    while True:
        if cursor:
            payload["start_cursor"] = cursor
        r = requests.post(url, headers=notion_headers, json=payload, timeout=60)
        data = r.json()
        if "results" not in data:
            log(f"  Notion query error: {data}")
            break
        all_results.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return all_results


# ============================================================
# Row 抽出
# ============================================================
def _title(prop):
    if not prop:
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get("title", []))


def _rich_text(prop):
    if not prop:
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))


def _number(prop):
    if not prop:
        return None
    return prop.get("number")


def _date_start(prop):
    if not prop:
        return ""
    d = prop.get("date")
    if not d:
        return ""
    return (d.get("start") or "")[:10]


def _status_name(prop):
    if not prop:
        return ""
    s = prop.get("status")
    return s.get("name", "") if s else ""


def _select_name(prop):
    if not prop:
        return ""
    s = prop.get("select")
    return s.get("name", "") if s else ""


def extract_row(page):
    props = page["properties"]
    return {
        "reins_id": _title(props.get("REINS_ID")),
        "building_name": _rich_text(props.get("物件名")),
        "score": _number(props.get("推薦点数")),
        "company": _rich_text(props.get("管理会社")),
        "koukai_date": _date_start(props.get("公開日時")),
        "listing_count": _number(props.get("登録店舗数")),
        "status": _status_name(props.get("Status")),
        "ad_ok": _select_name(props.get("会社広告可否")),
        "reverb": _number(props.get("実反響数")),
    }


# ============================================================
# Markdown 生成
# ============================================================
def _md_escape(s):
    """Markdown 表内で `|` が壊さないようエスケープ"""
    if s is None:
        return "-"
    return str(s).replace("|", "\\|").replace("\n", " ")


def _fmt_num(n):
    if n is None:
        return "-"
    if isinstance(n, float):
        return f"{n:g}"
    return str(n)


def _bucket_cutoff_label(now_jst):
    """now_jst (JST datetime) を直近 cutoff (11/15/19/23) に丸めた HH:MM ラベルを返す。
    snapshot 起動が 11:30 ならラベルは 11:00 cutoff snapshot。"""
    cutoffs = [11, 15, 19, 23]
    h = now_jst.hour
    past = [c for c in cutoffs if c <= h]
    if past:
        return f"{max(past):02d}:00"
    return "23:00"  # 0:00〜10:59 → 前日 23:00 cutoff 扱い (理論上は走らないが安全側)


def build_section(rows, now_jst):
    cutoff_label = _bucket_cutoff_label(now_jst)
    ts = now_jst.strftime("%Y-%m-%d %H:%M:%S")
    counts = {s: 0 for s in ACTIVE_STATUSES}
    for r in rows:
        if r["status"] in counts:
            counts[r["status"]] += 1
    total = len(rows)
    counts_str = " / ".join(f"{s} {counts[s]}" for s in ACTIVE_STATUSES)

    lines = []
    lines.append(f"## {cutoff_label} JST cutoff snapshot (取得時刻: {ts})")
    lines.append("")
    lines.append(f"合計 {total} 件 ({counts_str})")
    lines.append("")
    lines.append("| REINS_ID | 物件名 | 推薦点数 | 管理会社 | 公開日時 | 登録店舗数 | Status | 会社広告可否 | 実反響数 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    rows_sorted = sorted(
        rows,
        key=lambda r: (
            ACTIVE_STATUSES.index(r["status"]) if r["status"] in ACTIVE_STATUSES else 99,
            -(r["score"] or 0),
        ),
    )
    for r in rows_sorted:
        lines.append(
            "| "
            + " | ".join(
                _md_escape(v)
                for v in [
                    r["reins_id"],
                    r["building_name"],
                    _fmt_num(r["score"]),
                    r["company"],
                    r["koukai_date"],
                    _fmt_num(r["listing_count"]),
                    r["status"],
                    r["ad_ok"] or "-",
                    _fmt_num(r["reverb"]),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def write_snapshot(section, now_jst, dry_run):
    date_str = now_jst.strftime("%Y-%m-%d")
    filename = f"{date_str}.md"
    path = SNAPSHOT_DIR / filename

    if dry_run:
        print(f"\n[DRY-RUN] would write to: {path}")
        print("=" * 60)
        if not path.exists():
            print(f"# {date_str} おすすめ DB active snapshots\n")
        print(section)
        return

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# {date_str} おすすめ DB active snapshots\n\n")
        f.write(section)
        f.write("\n")
    log(f"  → {'created' if is_new else 'appended to'} {path}")


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log("=" * 60)
    log(f"Snapshot Obsidian — 独立服务{' (DRY-RUN)' if args.dry_run else ''}")
    log("=" * 60)

    now_jst = datetime.now(JST)
    log(f"now (JST): {now_jst.strftime('%Y-%m-%d %H:%M:%S')}")

    log(f"おすすめ DB から Status ∈ {ACTIVE_STATUSES} を取得...")
    pages = notion_query_active()
    log(f"  取得: {len(pages)} 件")

    rows = [extract_row(p) for p in pages]
    section = build_section(rows, now_jst)
    write_snapshot(section, now_jst, args.dry_run)

    log("完了")


if __name__ == "__main__":
    main()
