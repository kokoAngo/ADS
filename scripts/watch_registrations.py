#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Watch Registrations - 独立服务

定期扫描推荐 DB (新着物件おすすめ / 確認待ち物件):
1. 跳过 Status=取下済 的物件
2. 按 REINS_ID 从 MAIN DB 取得 rent/area (用于过滤 SUUMO 搜索结果)
3. 用物件名在 SUUMO 上做キーワード搜索, 按 rent±0.5万 & area±2m2 过滤
4. 过滤后剩下的 cassette 数 = 登録店舗数(一般一家中介一次登録该房间)
5. 写回「登録店舗数」列。搜不到或出错写 0

该服务与物件评估服务(process_pipeline.py / workflow_trigger.py)完全独立:
  - 独立日志: logs/watch_registrations.log
  - 独立进程,一次性运行后退出 (launchd/cron 驱动)
  - 独立 Playwright 浏览器实例
  - 不依赖模型 / 沿線字典

调度: ~/Library/LaunchAgents/jp.ango.watchregistrations.plist
  每天 :30 每 2 小时 (0:30, 2:30, ..., 22:30 JST)
"""
import os
import sys
import re
import time
import requests
from pathlib import Path
from datetime import datetime

# 固定 cwd 到项目根
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(line_buffering=True)


# ============================================================
# 配置
# ============================================================
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
MAIN_DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"       # 全物件 DB
# 需要扫描的推荐 DB(都有 REINS_ID / 物件名 / Status / 登録店舗数 字段)
# 每项: (显示名, DB ID, 要跳过的 Status 列表)
TARGET_DATABASES = [
    ("新着物件おすすめ", "3171c1974dad80439367df13aa67f012", ["取下済"]),
    ("確認待ち物件",      "3181c1974dad80279cb7dfdeb92b946f", []),
]

SUUMO_SEARCH_URL = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&ta=13"

# 匹配容差
RENT_TOL_MAN = 0.5     # 万円
AREA_TOL_M2 = 2.0      # m2

LOG_FILE = Path("logs") / "watch_registrations.log"
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
    """返回 dict {rent_man, area_sqm} 或 None"""
    pages = notion_query(MAIN_DATABASE_ID, filter_obj={
        "property": "REINS_ID",
        "title": {"equals": reins_id}
    })
    if not pages:
        return None
    props = pages[0]["properties"]

    data = {}
    rent_text = _rich_text(props, "賃料（万円）")
    if rent_text:
        try:
            data["rent_man"] = float(rent_text)
        except ValueError:
            pass

    area_text = _rich_text(props, "使用部分面積（m2）")
    if area_text:
        try:
            data["area_sqm"] = float(area_text)
        except ValueError:
            pass

    if data.get("rent_man") and data.get("area_sqm"):
        return data
    return None


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
def process_one(item, browser_page):
    reins_id = item["reins_id"]
    page_id = item["page_id"]
    name = item["building_name"]

    details = fetch_property_details(reins_id)
    if not details:
        log(f"  ✗ MAIN DB 无该 REINS_ID 或缺 rent/area")
        notion_update(page_id, {"登録店舗数": {"number": 0}})
        return "skip_no_data"

    log(f"  物件名: {name} | 賃料: {details['rent_man']}万 | 面積: {details['area_sqm']}m2")
    count = count_suumo_listings(browser_page, name,
                                  details["rent_man"], details["area_sqm"])

    value = int(count) if count and count > 0 else 0
    ok = notion_update(page_id, {"登録店舗数": {"number": value}})
    if not ok:
        return "update_failed"
    if value > 0:
        log(f"  → 登録店舗数: {value}")
        return "success"
    log(f"  → 登録店舗数: 0 (未匹配)")
    return "not_found"


def collect_items(db_label, db_id, skip_statuses):
    """
    从一个推荐 DB 取出物件列表, 附带来源 DB 名称。
    skip_statuses: 要跳过的 Status 名称列表, 空列表表示全表扫描。
    """
    log(f"查询 {db_label} ({db_id})...")
    filter_obj = None
    if skip_statuses:
        # 多个 skip 用 and 串联: Status 不等于每一个
        conds = [{"property": "Status", "status": {"does_not_equal": s}} for s in skip_statuses]
        filter_obj = {"and": conds} if len(conds) > 1 else conds[0]
    pages = notion_query(db_id, filter_obj=filter_obj)
    items = []
    for p in pages:
        props = p["properties"]
        reins_id = ""
        if props.get("REINS_ID", {}).get("title"):
            reins_id = props["REINS_ID"]["title"][0]["plain_text"]
        name = ""
        if props.get("物件名", {}).get("rich_text"):
            name = props["物件名"]["rich_text"][0]["plain_text"]
        if reins_id and name:
            items.append({
                "page_id": p["id"],
                "reins_id": reins_id,
                "building_name": name,
                "source_db": db_label,
            })
    suffix = f" (已排除 Status={'/'.join(skip_statuses)})" if skip_statuses else " (全表)"
    log(f"  → {len(items)} 件{suffix}")
    return items


def main():
    log("=" * 60)
    log("Watch Registrations — 独立服务")
    log("=" * 60)

    # 从多个推荐 DB 收集物件
    all_items = []
    for db_label, db_id, skip_statuses in TARGET_DATABASES:
        all_items.extend(collect_items(db_label, db_id, skip_statuses))

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

    stats = {"success": 0, "not_found": 0, "skip_no_data": 0, "update_failed": 0, "error": 0}

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
