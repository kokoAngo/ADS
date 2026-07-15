#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Watch Registrations - 独立服务 (2026-05-18 〜 中介数ベース撤退判定 再導入、現閾値 10)

3 DB 跨ぎフロー:
1. 掲載物件のみ DB (35f1c197..., ADs-Manager が forrent 掲載中物件を upsert) 全件 query
   → `自社物件番号` (title) から prefix 剥がして 12 桁 REINS_ID 抽出 (og 系は skip)
2. おすすめ DB (3171c197...) 全 row を pre-fetch して dict 化、REINS_ID で lookup
   → 該当 row なければ skip (書き戻し先なし)
3. MAIN DB (3031c197...) から REINS_ID で rent/area 取得 (SUUMO 過滤用)
4. SUUMO kwd 検索 → cassette を rent±0.5万 & area±2m2 で過滤 → 中介数 count
5. おすすめ DB.登録店舗数 + 掲載物件のみ DB.競合仲介数 に書き戻し + Obsidian Fango Listings ノートに append
6. 中介数 >= RETIRE_BY_LISTING_COUNT (=13) なら 2 つの DB (おすすめ + 掲載物件のみ) の
   Status=要取り下げ 自動セット + logs/audit_listing_retired.csv に audit 行追加

PV ベース撤退 (PVMonitor/retire_by_pv.py) と並列 OR 条件で動作。
冪等: 終態 (要取り下げ / 取下済み / 入稿失敗 等) は再判定 skip。

该服务与物件评估服务(process_pipeline.py / workflow_trigger.py)完全独立:
  - 独立日志: logs/watch_registrations.log
  - 独立进程, 一次性运行后退出 (launchd 驱动)
  - 独立 Playwright 浏览器实例
  - 不依赖模型 / 沿線字典

调度: ~/Library/LaunchAgents/jp.ango.watchregistrations.plist
  每天 :30 每 2 小时 (0:30, 2:30, ..., 22:30 JST)
"""
import os
import sys
import re
import csv
import time
import requests
from pathlib import Path
from datetime import datetime

# 固定 cwd 到项目根
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # scripts/ で同階層 module を import 可能に
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import pg_main   # 2026-07-15: rent/area は PG から取得 (Notion MAIN DB は 25万行上限で query 不能)

from playwright.sync_api import sync_playwright
from datetime import timezone, timedelta

from obsidian_listings import append_observation

sys.stdout.reconfigure(line_buffering=True)

JST = timezone(timedelta(hours=9))


# ============================================================
# 配置
# ============================================================
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
# 多社対応: 会社別に異なるのは LISTING_ONLY_DB_ID (掲載物件のみ) のみ。env 未設定なら funts 既定値。
# SS 等は .env で LISTING_ONLY_DB_ID=<その会社の表> を指定する (MAIN/OSUSUME は共有=既定のまま)。
MAIN_DATABASE_ID = os.getenv("MAIN_DATABASE_ID", "3031c197-4dad-800b-917d-d09b8602ec39")   # 全物件 DB (rent/area、共有)
LISTING_ONLY_DB_ID = os.getenv("LISTING_ONLY_DB_ID", "35f1c1974dad80ccb54fe17cfd116328")   # 掲載物件のみ (会社別)
OSUSUME_DB_ID = os.getenv("OSUSUME_DB_ID", "3171c1974dad80439367df13aa67f012")             # おすすめ DB (共有)

SUUMO_SEARCH_URL = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&ta=13"

# 匹配容差
RENT_TOL_MAN = 0.5     # 万円
AREA_TOL_M2 = 2.0      # m2

# 中介数ベース自動撤退 (2026-05-18 再導入、旧 RETIRE_BY_LISTING_COUNT=10 から 30 に引き上げ)
# PV ベース撤退 (PVMonitor/retire_by_pv.py) と並列 OR 条件。
RETIRE_BY_LISTING_COUNT = int(os.getenv("RETIRE_BY_LISTING_COUNT", "10"))   # SUUMO 上の中介数 >= 此值 → 要取り下げ (既定10; 2026-07-03:15→13, 07-15:13→10)
RETIRE_DRY_RUN = os.getenv("RETIRE_DRY_RUN", "0") == "1"   # 1 なら撤退時 Notion 書込 skip、log と audit CSV のみ

# 撤退対象外 Status (終態 + 既に要取り下げ済み)
_RETIRE_SKIP_STATUSES = {"要取り下げ", "取下済み", "入稿失敗", "広告掲載禁止", "時間超過", "【時間超過】掲載保留"}

LOG_FILE = Path("logs") / "watch_registrations.log"
AUDIT_CSV = Path("logs") / "audit_listing_retired.csv"
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
# 从 MAIN DB 按 REINS_ID 查 rent/area (过滤搜索结果用)
# ============================================================
def _rich_text(props, field):
    if field in props and props[field].get("rich_text"):
        return props[field]["rich_text"][0]["plain_text"]
    return ""


def fetch_property_details(reins_id):
    """返回 dict {rent_man, area_sqm} 或 None。
    2026-07-15: Notion MAIN DB(3031) が 25万行上限に到達し query 不能になったため、
    PG (main.shinchaku_bukken) から取得に変更 (process_pipeline と同じデータソース)。"""
    return pg_main.fetch_rent_area(reins_id)


# ============================================================
# 物件名清洗
# ============================================================
def normalize_building_name(name):
    """
    清洗物件名, 便于 SUUMO kwd 搜索:
    - 去掉括号内读み仮名 (如「（マハロテラス）」)
    - 去掉尾部号室后缀 (如「２０３号室」)
    - 保留全角/半角空格 (SUUMO 搜索需要)
    - 多余空格压缩
    """
    if not name:
        return ""
    # 去全角/半角括号内容
    name = re.sub(r"（[^）]*）", "", name)
    name = re.sub(r"\([^)]*\)", "", name)
    # 去尾部的「XXX号室」(全/半角数字和漢数字)
    name = re.sub(r"[\s　]?[\dO〇零一二三四五六七八九十百千０-９]+\s*号\s*室\s*$", "", name)
    # 末尾の棟番号除去 (2026-06-15: SUUMO 検索で「Ⅱ」等が付くと 0 件→競合見逃しになるバグ修正。
    #   別棟が混ざっても後段の rent/area フィルタで同部屋に収束する)
    name = re.sub(r"[\s　]?[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫⅰⅱⅲⅳⅴ]+\s*$", "", name)               # Unicodeローマ数字棟
    name = re.sub(r"[\s　]?[A-ZＡ-Ｚ0-9０-９一二三四五六七八九]\s*号?棟\s*$", "", name)     # A棟 / 2号棟 等
    name = re.sub(r"[\s　](?:VIII|VII|VI|IV|IX|III|II|V|X)\s*$", "", name)                 # ラテンローマ数字棟
    # 多余空格压缩(保留至少一个 半角空格, 复数空格 → 单个)
    name = re.sub(r"[\s　]{2,}", " ", name)
    return name.strip()


# ============================================================
# SUUMO 搜索 + 过滤
# ============================================================
def _route_filter(route):
    rt = route.request.resource_type
    url = route.request.url
    if rt in ("image", "media", "font", "stylesheet"):
        route.abort()
        return
    if any(x in url for x in ("googletagmanager", "doubleclick", "google-analytics",
                               "googlesyndication", "adnxs", "facebook.com/tr",
                               "criteo", "yimg.jp/images/ad", "adservice")):
        route.abort()
        return
    route.continue_()


def _parse_cassette(text):
    """从 cassette inner_text 提取 rent_man 和 area_sqm"""
    rent_m = re.search(r"賃料[:：]\s*([\d.]+)\s*万", text)
    area_m = re.search(r"専有面積[:：]\s*([\d.]+)\s*m", text)
    rent = float(rent_m.group(1)) if rent_m else None
    area = float(area_m.group(1)) if area_m else None
    return rent, area


def count_suumo_listings(page, building_name, target_rent_man, target_area_sqm):
    """
    按物件名搜 SUUMO, 过滤到同房间(rent±RENT_TOL & area±AREA_TOL),
    返回过滤后件数。无结果返回 0, 出错返回 None。
    """
    keyword = normalize_building_name(building_name)
    if not keyword:
        return None

    try:
        page.goto(SUUMO_SEARCH_URL, timeout=30000)
        time.sleep(1.5)

        kwd_input = page.locator('input[name="kwd"]').first
        if kwd_input.count() == 0:
            log(f"    SUUMO 页面结构异常: 找不到 kwd 输入框")
            return None
        kwd_input.fill(keyword)
        time.sleep(0.3)
        kwd_input.press("Enter")

        page.wait_for_url("**/JJ901FC001/**", timeout=15000)
        time.sleep(1.5)

        # 先看总件数
        body = page.locator("body").inner_text()
        total_m = re.search(r"物件\s*\n?\s*([\d,]+)\s*件\s*\n?\s*検索条件", body)
        total = int(total_m.group(1).replace(",", "")) if total_m else 0
        if total == 0:
            return 0

        # 抓所有 cassette, 按 rent/area 过滤
        cassettes = page.locator(".cassettebox").all()
        matched = 0
        for c in cassettes:
            try:
                text = c.inner_text()
                rent_c, area_c = _parse_cassette(text)
                if rent_c is None or area_c is None:
                    continue
                if abs(rent_c - target_rent_man) > RENT_TOL_MAN:
                    continue
                if abs(area_c - target_area_sqm) > AREA_TOL_M2:
                    continue
                matched += 1
            except Exception:
                continue

        # 注意: 搜索结果页默认只显示前 30 件左右,如果总件数 > 30 且过滤结果小,
        # 可能漏掉后续页的匹配。大多数情况下单栋建筑的 cassette 不超过 30 条。
        # 这里如果 matched=0 但 total > 0 且 total 较小(<30),记录一下以供排查。
        if matched == 0 and 0 < total <= 50:
            log(f"    (总件数 {total} 但 rent/area 过滤后为 0, 可能物件 ID 对应的房间不在 SUUMO 当前在线)")
        elif total > 50:
            log(f"    (总件数 {total} 较多, 分页可能漏算; 当前页过滤后 {matched} 件)")

        return matched

    except Exception as e:
        log(f"    SUUMO 搜索异常: {str(e)[:120]}")
        return None


# ============================================================
# Main
# ============================================================
def _append_audit(reins_id, page_id, name, timestamp, count, status_before, status_after):
    """撤退実行を logs/audit_listing_retired.csv に 1 行 append (新規ファイル時はヘッダも書く)."""
    is_new = not AUDIT_CSV.exists()
    with open(AUDIT_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp", "reins_id", "page_id", "building_name",
                        "listing_count", "status_before", "status_after", "reason"])
        w.writerow([timestamp, reins_id, page_id, name, count, status_before,
                    status_after, f"listing_count>={RETIRE_BY_LISTING_COUNT}"])


def mark_for_retirement_by_listing(item, count):
    """中介数 >= RETIRE_BY_LISTING_COUNT で 2 つの DB (おすすめ + 掲載物件のみ) の Status=要取り下げ にセット.

    冪等: おすすめ DB 側の現 Status が終態/要取り下げ なら何もしない (False 返却).
    掲載物件のみ DB 側は おすすめ と同時に更新するため、独立判定はしない.
    env RETIRE_DRY_RUN=1 で Notion 書込 skip、audit CSV のみ.
    """
    current_status = item.get("status", "")
    if current_status in _RETIRE_SKIP_STATUSES:
        return False

    reins_id = item["reins_id"]
    osusume_page_id = item["page_id"]
    listing_only_page_id = item.get("listing_only_page_id")
    name = item.get("building_name", "")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log(f"  🚨 中介数 {count} >= {RETIRE_BY_LISTING_COUNT} → Status=要取り下げ にセット (REINS_ID={reins_id})")

    if RETIRE_DRY_RUN:
        log(f"    [DRY-RUN] Notion 両 DB 書込 skip")
        _append_audit(reins_id, osusume_page_id, name, ts, count, current_status, "要取り下げ (dry-run)")
        return True

    retire_props = {"Status": {"status": {"name": "要取り下げ"}}}

    ok_osusume = notion_update(osusume_page_id, retire_props)
    if not ok_osusume:
        log(f"    ⚠ おすすめ DB Notion update 失敗")
        return False
    log(f"    ✓ おすすめ DB Status=要取り下げ")

    if listing_only_page_id:
        ok_listing = notion_update(listing_only_page_id, retire_props)
        if not ok_listing:
            log(f"    ⚠ 掲載物件のみ DB Notion update 失敗 (おすすめ DB は成功済)")
        else:
            log(f"    ✓ 掲載物件のみ DB Status=要取り下げ")

    _append_audit(reins_id, osusume_page_id, name, ts, count, current_status, "要取り下げ")
    return True


def process_one(item, browser_page):
    reins_id = item["reins_id"]
    page_id = item["page_id"]
    name = item["building_name"]
    koukai_date = item.get("koukai_date", "")
    status = item.get("status", "")

    # 拉物件详情用于 SUUMO 搜索
    details = fetch_property_details(reins_id)
    if not details:
        log(f"  ✗ MAIN DB 无该 REINS_ID 或缺 rent/area")
        notion_update(page_id, {"登録店舗数": {"number": 0}})
        return "skip_no_data"

    log(f"  物件名: {name} | 賃料: {details['rent_man']}万 | 面積: {details['area_sqm']}m2 | 公開={koukai_date}")
    count = count_suumo_listings(browser_page, name,
                                  details["rent_man"], details["area_sqm"])

    value = int(count) if count and count > 0 else 0
    update_props = {"登録店舗数": {"number": value}}

    ok = notion_update(page_id, update_props)
    if not ok:
        return "update_failed"

    # 掲載物件のみ DB の「競合仲介数」にも同値を書き戻し (列名 別、書込み失敗は警告のみ続行)
    listing_only_page_id = item.get("listing_only_page_id")
    if listing_only_page_id:
        ok_listing = notion_update(listing_only_page_id, {"競合仲介数": {"number": value}})
        if not ok_listing:
            log(f"    ⚠ 掲載物件のみ DB.競合仲介数 書込み失敗 (おすすめ DB は成功)")

    # Obsidian 物件ノートに時系列 append (count is None = SUUMO エラーは skip、value=0 は記録)
    if count is not None:
        try:
            ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
            append_observation(
                reins_id=reins_id,
                property_name=name,
                koukai_date=koukai_date,
                timestamp=ts,
                count=value,
                status=status,
            )
        except Exception as e:
            log(f"    Obsidian append 失敗: {str(e)[:120]}")

    # 中介数ベース自動撤退 (count is None なら skip、value < 閾値 なら skip)
    if count is not None and value >= RETIRE_BY_LISTING_COUNT:
        try:
            mark_for_retirement_by_listing(item, value)
        except Exception as e:
            log(f"    撤退判定例外: {str(e)[:120]}")

    if value > 0:
        log(f"  → 登録店舗数: {value}")
        return "success"
    log(f"  → 登録店舗数: 0 (未匹配)")
    return "not_found"


# 掲載物件のみ DB の `自社物件番号` (title) prefix 規則 (sync_outcomes.py:156 と同じ)
#   AI100138178064 / fng100138625774 → 前置を剥がせば REINS_ID
#   og844 / og50 → ad-system 独自 ID (REINS なし、照合不可なので skip)
_REINS_PREFIX_RE = re.compile(r"^(AI|fng)(\d{12})$")


def extract_reins_id(name):
    """`自社物件番号` (title) から 12 桁 REINS_ID を取り出す。og 系などは None."""
    if not name:
        return None
    m = _REINS_PREFIX_RE.match(name.strip())
    return m.group(2) if m else None


def build_osusume_lookup():
    """おすすめ DB 全 row を {reins_id: {page_id, building_name, koukai_date, status}} で返す。
    O(N) 1 回の query で済ませる。終態 Status を含む全行を返却 (filter は呼出し側で判断)。"""
    pages = notion_query(OSUSUME_DB_ID)
    lookup = {}
    for p in pages:
        props = p["properties"]
        reins_id = ""
        if props.get("REINS_ID", {}).get("title"):
            reins_id = props["REINS_ID"]["title"][0]["plain_text"]
        if not reins_id:
            continue
        name = ""
        if props.get("物件名", {}).get("rich_text"):
            name = props["物件名"]["rich_text"][0]["plain_text"]
        koukai = ""
        ko = props.get("公開日時", {}).get("date")
        if ko:
            koukai = ko.get("start", "")[:10]
        if not koukai:
            koukai = p.get("created_time", "")[:10]
        status_val = ""
        so = props.get("Status", {}).get("status")
        if so:
            status_val = so.get("name", "")
        lookup[reins_id] = {
            "page_id": p["id"],
            "building_name": name,
            "koukai_date": koukai,
            "status": status_val,
        }
    return lookup


def collect_from_listing_only_db():
    """掲載物件のみ DB 全件 → REINS_ID 抽出 → おすすめ DB で lookup → item dict 配列を返す。"""
    log(f"查询 掲載物件のみ ({LISTING_ONLY_DB_ID})...")
    pages = notion_query(LISTING_ONLY_DB_ID)
    log(f"  → {len(pages)} 件 (全件)")

    log(f"事前 lookup: おすすめ DB ({OSUSUME_DB_ID}) 全 row 取得...")
    lookup = build_osusume_lookup()
    log(f"  → {len(lookup)} 個 REINS_ID をキャッシュ")

    items = []
    stat = {"non_reins": 0, "no_osusume_row": 0, "ok": 0}
    for p in pages:
        title = p["properties"].get("自社物件番号", {}).get("title", [])
        name_raw = "".join(t.get("plain_text", "") for t in title).strip()
        reins_id = extract_reins_id(name_raw)
        if not reins_id:
            stat["non_reins"] += 1
            continue
        osusume = lookup.get(reins_id)
        if not osusume:
            stat["no_osusume_row"] += 1
            continue
        # Status は 掲載物件のみ DB 側を一手データとして採用 (ADs-Manager が forrent 最新状況を反映、
        # おすすめ DB 側の Status はズレてる場合がある。例: ad-script 再試行成功後の「入稿失敗」残置)
        listing_status_obj = p["properties"].get("Status", {}).get("status")
        listing_status = listing_status_obj.get("name", "") if listing_status_obj else ""
        items.append({
            "page_id": osusume["page_id"],            # おすすめ DB 側 (登録店舗数 書戻 + Status 更新先)
            "listing_only_page_id": p["id"],          # 掲載物件のみ DB 側 (Status 更新先)
            "reins_id": reins_id,
            "building_name": osusume["building_name"],
            "source_db": "掲載物件のみ",
            "koukai_date": osusume["koukai_date"],
            "status": listing_status,                 # 掲載物件のみ DB 側を一手データ採用
            "osusume_status": osusume["status"],      # 参考用 (audit ログには載らないが debug 用)
        })
        stat["ok"] += 1

    log(f"  処理対象: {stat['ok']} 件 (og 系 skip {stat['non_reins']}, おすすめ未登録 skip {stat['no_osusume_row']})")
    return items


def main():
    log("=" * 60)
    log("Watch Registrations — 独立服务")
    log("=" * 60)

    # 掲載物件のみ DB ベース (3 DB 跨ぎ)
    all_items = collect_from_listing_only_db()

    log(f"\n合计待处理: {len(all_items)} 件")

    _limit = os.getenv("TEST_LIMIT")
    if _limit:
        all_items = all_items[: int(_limit)]
        log(f"TEST_LIMIT={_limit}: 只处理前 {len(all_items)} 件")

    if not all_items:
        log("没有待处理物件")
        return

    log("启动 Playwright headless 浏览器...")
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="ja-JP")
    context.route("**/*", _route_filter)
    page = context.new_page()

    stats = {"success": 0, "not_found": 0,
             "skip_no_data": 0, "update_failed": 0, "error": 0}

    try:
        for i, item in enumerate(all_items):
            log(f"\n[{i + 1}/{len(all_items)}] [{item['source_db']}] REINS_ID={item['reins_id']}")
            try:
                result = process_one(item, page)
                stats[result] = stats.get(result, 0) + 1
            except Exception as e:
                log(f"  ✗ 处理异常: {str(e)[:120]}")
                stats["error"] += 1
    finally:
        try:
            browser.close()
            pw.stop()
        except Exception:
            pass
        log("\n浏览器已关闭")

    log("\n" + "=" * 60)
    log("完成!")
    for k, v in stats.items():
        if v > 0:
            log(f"  {k}: {v}")
    log("=" * 60)


if __name__ == "__main__":
    main()
