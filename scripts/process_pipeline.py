"""
Property Processing Pipeline (V1)
处理每个物件: 预测view → (低分跳过) → 商号检查 → 反响数 → SUUMO排名 → SUUMO广告数 → 推薦点数 → 实时写入TOP表
所有步骤逐物件串行执行，与旧的逐步骤批处理不同
"""
import os
import sys
import json
import pickle
import re
import math
import time
import csv
import threading
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
from queue import Queue, Empty

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from dotenv import load_dotenv
import numpy as np

sys.stdout.reconfigure(line_buffering=True)
load_dotenv()

# ============================================================
# Constants
# ============================================================
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"
RECOMMEND_DATABASE_ID = "3171c1974dad80439367df13aa67f012"  # 新着物件おすすめ (合并后唯一 TOP DB, 2026-04-28)
# 注: 旧 PENDING_DATABASE_ID (3181...) 已废弃, 122 行迁到 おすすめ DB

# 截止时间（JST）— 每天 11:00, 15:00, 19:00, 23:00 是新物件登载截止时间
# 流水线只评估"最近一次截止时间之后"创建的物件
JST = timezone(timedelta(hours=9))
CUTOFF_HOURS = [11, 15, 19, 23]
CUTOFF_MINUTE = 0

# 阈值
VIEW_THRESHOLD = 6.0           # view < 此值跳过后续步骤
RECOMMEND_THRESHOLD = 5.8      # 推薦点数 >= 此值才写入TOP表
MAX_COMPETITION_FOR_ENTRY = 5  # SUUMO 上已被 > 此值家中介公开的物件 → 不写入 TOP 表(高竞争跳过)
# 注: 之前有 MAX_RECOMMENDATIONS=20 的滚动上限, 2026-04-27 移除以支持广告生命周期跟踪
# (满 20 时自动 archive 最老一条 → 但可能 archive 掉还在投放中的 row → ad-script 失联)
# 现在 TOP DB 不限大小, 由 scripts/archive_old_recommendations.py 周期归档终态老 row

# 推薦点数权重
WEIGHTS = {
    'view_score': 0.30,
    'inquiry_score': 0.25,
    'competition': 0.25,
    'market_rank': 0.20,
}

# 热门駅(反响/千供給 50-368, 平均的 10-60 倍)— findings/stations_costs.md Top 20
HOT_STATIONS = {
    '世田谷代田', '緑が丘', '千石', '若林', '京成小岩',
    '雑司が谷', '東大前', '上北沢', '尾久', '千住大橋',
    '自由が丘', '桜新町', '湯島', '大井町', '新中野',
    '尾山台', '西大井', '不動前', '東十条', '四谷三丁目',
}
HOT_STATION_BONUS = 0.3   # 物件最寄駅 ∈ HOT_STATIONS → 推薦点数 +0.3

# 区别反响効率 bonus (analysis-claude 提案 2026-04-30, 保守版 ±0.3 圧縮)
# 2026-04-30 更新: ±0.3 暂定版 → shrinkage 精算版 (analysis-claude finding 3521c197-4dad-81d6-...)
# 公式: ward_smoothed = (n*rate + K*GLOBAL) / (n + K), bonus = clip(delta * SCALE, ±CLIP)
# パラメータ: K=10 prior 重み / GLOBAL_RATE=0.16 / SCALE=1.5 / CLIP=±0.5 / N_MIN=5
# 効果: 极端値缓和 (大田 -0.3→-0.13, 葛飾 -0.3→-0.09), 文京単独強化 (+0.3→+0.43)
# HOT_STATIONS と二重カウント回避: HOT_STATIONS 適用時は ward bonus skip
# 3 ヶ月毎に再キャリブ予定 (next: 5/20 頃, 投放分布変化を観察してから)
WARD_REVERB_BONUS = {
    '文京区':   +0.43,  # n=7, rate 86% → smoothed 45%, +強化
    '板橋区':   +0.16,  # n=15, rate 33%
    '中野区':   +0.13,  # n=21, rate 29%
    '新宿区':   +0.12,  # n=26, rate 27%
    '杉並区':   +0.12,  # n=9, rate 33%
    '品川区':   -0.03,  # n=9 軽い負
    '江戸川区': -0.03,  # n=16 軽い負
    '世田谷区': -0.07,  # n=142 強い証拠で軽い負
    '葛飾区':   -0.09,  # n=6, rate 0%
    '江東区':   -0.11,  # n=8, rate 0%
    '大田区':   -0.13,  # n=40, rate 5%
}

DATA_DIR = Path("data")
BLACKLIST_FILE = DATA_DIR / "blacklist_companies.txt"
WHITELIST_FILE = DATA_DIR / "whitelist_companies.txt"
FULL_DATA_FILE = DATA_DIR / "management_companies.csv"

LOG_FILE = Path("logs") / "process_pipeline.log"
LOG_FILE.parent.mkdir(exist_ok=True)

# 并发控制
WORKER_COUNT = int(os.getenv("WORKER_COUNT", "3"))
_notion_lock = threading.Lock()
_stats_lock = threading.Lock()
_log_lock = threading.Lock()


def log(msg):
    with _log_lock:
        print(msg)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


# ============================================================
# 加载模型
# ============================================================
log("加载 view 预测模型...")
with open("models/xgboost_regressor_v2.pkl", "rb") as f:
    view_model = pickle.load(f)
with open("models/model_config_v2.json", "r", encoding='utf-8') as f:
    view_config = json.load(f)

log("加载 inquiry 预测模型...")
with open("models/inquiry_model.pkl", "rb") as f:
    inquiry_model = pickle.load(f)
with open("models/inquiry_model_config.json", "r") as f:
    inquiry_config = json.load(f)


# ============================================================
# 加载管理公司黑白名单
# ============================================================
def load_company_lists():
    blacklist = set()
    whitelist = set()
    case_by_case = set()

    if BLACKLIST_FILE.exists():
        with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
            blacklist = {line.strip() for line in f if line.strip()}

    if WHITELIST_FILE.exists():
        with open(WHITELIST_FILE, 'r', encoding='utf-8') as f:
            whitelist = {line.strip() for line in f if line.strip()}

    if FULL_DATA_FILE.exists():
        with open(FULL_DATA_FILE, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                company = row.get("管理会社名", "").strip()
                status = row.get("広告可否", "").strip()
                if company and status == "物件による":
                    case_by_case.add(company)

    return blacklist, whitelist, case_by_case


def match_company(company_name, company_set):
    if not company_name:
        return None
    if company_name in company_set:
        return company_name
    for known in company_set:
        name1 = company_name.replace("（株）", "").replace("(株)", "").replace("株式会社", "").strip()
        name2 = known.replace("（株）", "").replace("(株)", "").replace("株式会社", "").strip()
        if name1 in name2 or name2 in name1:
            return known
    return None


BLACKLIST, WHITELIST, CASE_BY_CASE = load_company_lists()
log(f"管理公司: 黑名单 {len(BLACKLIST)}, 白名单 {len(WHITELIST)}, 物件による {len(CASE_BY_CASE)}")


# ============================================================
# Notion API
# ============================================================
notion_headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}


def notion_query(db_id, filter_obj=None, sorts=None, page_size=100):
    """查询Notion数据库（支持分页）"""
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {"page_size": page_size}
        if filter_obj:
            payload["filter"] = filter_obj
        if sorts:
            payload["sorts"] = sorts
        if start_cursor:
            payload["start_cursor"] = start_cursor

        try:
            r = requests.post(url, headers=notion_headers, json=payload, timeout=60)
            data = r.json()
        except Exception as e:
            log(f"  Notion query 错误: {e}")
            break

        if "results" in data:
            all_results.extend(data["results"])
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        else:
            log(f"  Notion query error: {data}")
            break

    return all_results


def notion_update(page_id, properties):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    try:
        r = requests.patch(url, headers=notion_headers, json={"properties": properties}, timeout=60)
        return r.status_code == 200
    except Exception as e:
        log(f"  Notion update 错误: {e}")
        return False


def notion_create(db_id, properties):
    url = "https://api.notion.com/v1/pages"
    try:
        r = requests.post(url, headers=notion_headers, json={
            "parent": {"database_id": db_id},
            "properties": properties
        }, timeout=60)
        return r.status_code == 200
    except Exception as e:
        log(f"  Notion create 错误: {e}")
        return False


def notion_archive(page_id):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    try:
        r = requests.patch(url, headers=notion_headers, json={"archived": True}, timeout=60)
        return r.status_code == 200
    except Exception as e:
        return False


# ============================================================
# 物件数据提取
# ============================================================
TOKYO_WARDS = ['千代田区', '中央区', '港区', '新宿区', '文京区', '台東区', '墨田区',
               '江東区', '品川区', '目黒区', '大田区', '世田谷区', '渋谷区', '中野区',
               '杉並区', '豊島区', '北区', '荒川区', '板橋区', '練馬区', '足立区',
               '葛飾区', '江戸川区']


def parse_months(text):
    """解析敷金/礼金: '1ヶ月/-'→1.0, 'なし/-'→0.0"""
    if not text:
        return 0.0
    first = text.split("/")[0].strip()
    if "なし" in first or first in ["-", "ー"]:
        return 0.0
    m = re.search(r'([\d.]+)\s*[ヶヵか]?月', first)
    if m:
        return float(m.group(1))
    if "万" in first:
        return 0.0
    m = re.search(r'([\d.]+)', first)
    return float(m.group(1)) if m else 0.0


def extract_property(page):
    """从Notion页面提取物件全部数据"""
    props = page["properties"]
    data = {"page_id": page["id"]}

    def get_text(field):
        if field in props and props[field].get("rich_text"):
            return props[field]["rich_text"][0]["plain_text"]
        return ""

    # REINS_ID
    if "REINS_ID" in props and props["REINS_ID"].get("title"):
        data["bukken_number"] = props["REINS_ID"]["title"][0]["plain_text"]

    # 賃料（万円→円）
    rent_text = get_text("賃料（万円）")
    if rent_text:
        try:
            data["rent"] = int(float(rent_text) * 10000)
        except ValueError:
            pass

    # 面積
    area_text = get_text("使用部分面積（m2）")
    if area_text:
        try:
            data["area_sqm"] = float(area_text)
        except ValueError:
            pass

    # 築年月
    chiku = get_text("築年月")
    if chiku and len(chiku) >= 4:
        try:
            data["built_year"] = int(chiku[:4])
        except ValueError:
            pass

    # 徒歩
    walk = get_text("徒歩(分)")
    if walk:
        try:
            data["walk_minutes"] = int(float(walk))
        except ValueError:
            pass

    # 間取
    madori = get_text("間取")
    if madori:
        data["floor_plan"] = madori

    # 所在地 → 区
    address = get_text("所在地")
    if address:
        data["address"] = address
        for ward in TOKYO_WARDS:
            if ward in address:
                data["city"] = ward
                break

    # 物件種目
    shubetsu = get_text("物件種目")
    if shubetsu:
        data["property_type"] = shubetsu

    # 管理費 + 共益費
    mgmt = 0
    for f in ["管理費（円）", "共益費（円）"]:
        t = get_text(f)
        if t:
            try:
                mgmt += int(float(t.replace(",", "")))
            except ValueError:
                pass
    if mgmt > 0:
        data["management_fee"] = mgmt

    # 敷金/礼金
    if get_text("敷金/保証金"):
        data["deposit"] = parse_months(get_text("敷金/保証金"))
    if get_text("礼金/権利金"):
        data["key_money"] = parse_months(get_text("礼金/権利金"))

    # 商号 (管理公司)
    company = get_text("商号")
    if company:
        data["management_company"] = company

    # 沿線駅
    ensen = get_text("沿線駅")
    if ensen:
        parts = re.split(r'[\s　]+', ensen, maxsplit=1)
        if len(parts) >= 2:
            data["railway"] = parts[0]
            data["station"] = parts[1]

    # 建物名
    building = get_text("建物名")
    if building:
        data["building_name"] = building

    return data if data.get("bukken_number") else None


# ============================================================
# Step 1: View 数预测
# ============================================================
def prepare_view_features(data):
    """V2 模型 21 个特征"""
    rent = data.get('rent', 80000)
    area_sqm = data.get('area_sqm', 25)
    built_year = data.get('built_year', 2010)
    walk_minutes = data.get('walk_minutes', 10)
    city = data.get('city', '')
    floor_plan = data.get('floor_plan', '1K')
    property_type = data.get('property_type', '')
    management_fee = data.get('management_fee', 0)
    deposit = data.get('deposit', 1.0)
    key_money = data.get('key_money', 1.0)

    if isinstance(deposit, str):
        try: deposit = float(deposit)
        except: deposit = 1.0
    if isinstance(key_money, str):
        try: key_money = float(key_money)
        except: key_money = 1.0

    total_rent = rent + management_fee
    rent_per_sqm = rent / area_sqm if area_sqm > 0 else 0
    total_rent_per_sqm = total_rent / area_sqm if area_sqm > 0 else 0
    age = 2026 - built_year
    zero_deposit = 1 if deposit == 0 else 0
    zero_key_money = 1 if key_money == 0 else 0
    initial_cost = deposit + key_money

    high_heat = view_config.get('high_heat_areas', [])
    mid_heat = view_config.get('mid_heat_areas', [])
    heat_level = 2 if city in high_heat else (1 if city in mid_heat else 0)

    walk_level = 2 if walk_minutes <= 5 else (1 if walk_minutes <= 10 else 0)

    high_plans = view_config.get('high_response_plans', [])
    mid_plans = view_config.get('mid_response_plans', [])
    plan_type = 2 if floor_plan in high_plans else (1 if floor_plan in mid_plans else 0)

    if 'マンション' in str(property_type):
        building_type = 2
    elif 'アパート' in str(property_type):
        building_type = 1
    else:
        building_type = 0

    if rent < 60000: rent_level = 0
    elif rent < 80000: rent_level = 1
    elif rent < 100000: rent_level = 2
    elif rent < 150000: rent_level = 3
    else: rent_level = 4

    if area_sqm < 20: area_level = 0
    elif area_sqm < 30: area_level = 1
    elif area_sqm < 50: area_level = 2
    else: area_level = 3

    city_encoded = view_config.get('city_mapping', {}).get(city, 0)

    return [
        rent, area_sqm, built_year, walk_minutes, management_fee,
        total_rent, deposit, key_money, initial_cost,
        zero_deposit, zero_key_money,
        rent_per_sqm, total_rent_per_sqm, age,
        city_encoded, heat_level, walk_level,
        plan_type, building_type, rent_level, area_level
    ]


def predict_view(data):
    features = prepare_view_features(data)
    pred = view_model.predict(np.array([features]))[0]
    return float(round(max(0, pred), 1))


# ============================================================
# Step 3: Inquiry 数预测
# ============================================================
def prepare_inquiry_features(data):
    rent = data.get('rent', 80000)
    area_sqm = data.get('area_sqm', 25)
    built_year = data.get('built_year', 2015)
    walk_minutes = data.get('walk_minutes', 10)
    city = data.get('city', '')
    floor_plan = data.get('floor_plan', '1K')

    rent_per_sqm = rent / area_sqm if area_sqm > 0 else 0
    age = 2025 - built_year

    high_heat = inquiry_config.get('high_heat_areas', [])
    mid_heat = inquiry_config.get('mid_heat_areas', [])
    heat_level = 2 if city in high_heat else (1 if city in mid_heat else 0)

    walk_level = 1 if walk_minutes <= 5 else (2 if walk_minutes <= 10 else 0)

    high_plans = inquiry_config.get('high_response_plans', ['1DK', '2DK', '2K', '3DK', '3K'])
    mid_plans = inquiry_config.get('mid_response_plans', ['1LDK', '3LDK', '1K', '2LDK'])
    plan_type = 2 if floor_plan in high_plans else (1 if floor_plan in mid_plans else 0)

    if rent < 60000: rent_level = 0
    elif rent < 80000: rent_level = 1
    elif rent < 100000: rent_level = 2
    elif rent < 150000: rent_level = 3
    else: rent_level = 4

    if area_sqm < 20: area_level = 0
    elif area_sqm < 30: area_level = 1
    elif area_sqm < 50: area_level = 2
    else: area_level = 3

    city_encoded = inquiry_config.get('city_mapping', {}).get(city, 0)

    return [
        rent, area_sqm, built_year, walk_minutes,
        city_encoded, heat_level, rent_per_sqm, age,
        walk_level, plan_type, rent_level, area_level
    ]


def predict_inquiry(data):
    features = prepare_inquiry_features(data)
    pred = inquiry_model.predict(np.array([features]))[0]
    return int(round(max(1, pred) * 10)) / 10


# ============================================================
# Step 2: 管理公司检查
# ============================================================
def check_management(company_name):
    """返回 可 / 不可（仲介） / 物件による / 確認待ち"""
    if not company_name:
        return "確認待ち"
    if match_company(company_name, BLACKLIST):
        return "不可（仲介）"
    if match_company(company_name, WHITELIST):
        return "可"
    if match_company(company_name, CASE_BY_CASE):
        return "物件による"
    return "確認待ち"


# ============================================================
# Step 4 & 5: SUUMO 共享浏览器查询
# ============================================================
RAILWAY_STATIONS = {
    "山手線": ["大崎", "五反田", "目黒", "恵比寿", "渋谷", "原宿", "代々木", "新宿", "新大久保", "高田馬場",
               "目白", "池袋", "大塚", "巣鴨", "駒込", "田端", "西日暮里", "日暮里", "鶯谷", "上野",
               "御徒町", "秋葉原", "神田", "東京", "有楽町", "新橋", "浜松町", "田町", "品川"],
    "大江戸線": ["新宿西口", "東新宿", "若松河田", "牛込柳町", "牛込神楽坂", "飯田橋", "春日", "本郷三丁目",
                "上野御徒町", "新御徒町", "蔵前", "両国", "森下", "清澄白河", "門前仲町", "月島", "勝どき",
                "築地市場", "汐留", "大門", "赤羽橋", "麻布十番", "六本木", "青山一丁目", "国立競技場",
                "代々木", "新宿", "都庁前", "西新宿五丁目", "中野坂上", "東中野", "中井", "落合南長崎",
                "新江古田", "練馬", "豊島園", "練馬春日町", "光が丘"],
    "西武新宿線": ["西武新宿", "高田馬場", "下落合", "中井", "新井薬師前", "沼袋", "野方", "都立家政",
                  "鷺ノ宮", "下井草", "井荻", "上井草", "上石神井", "武蔵関", "東伏見", "西武柳沢",
                  "田無", "花小金井", "小平", "久米川", "東村山"],
    "中央線": ["東京", "神田", "御茶ノ水", "四ツ谷", "新宿", "中野", "高円寺", "阿佐ケ谷", "荻窪",
              "西荻窪", "吉祥寺", "三鷹", "武蔵境", "東小金井", "武蔵小金井", "国分寺", "西国分寺",
              "国立", "立川", "日野", "豊田", "八王子"],
    "丸ノ内線": ["荻窪", "南阿佐ケ谷", "新高円寺", "東高円寺", "新中野", "中野坂上", "西新宿", "新宿",
               "新宿三丁目", "新宿御苑前", "四谷三丁目", "四ツ谷", "赤坂見附", "国会議事堂前", "霞ケ関",
               "銀座", "東京", "大手町", "淡路町", "御茶ノ水", "本郷三丁目", "後楽園", "茗荷谷", "新大塚", "池袋"],
    "有楽町線": ["和光市", "地下鉄成増", "地下鉄赤塚", "平和台", "氷川台", "小竹向原", "千川", "要町",
                "池袋", "東池袋", "護国寺", "江戸川橋", "飯田橋", "市ケ谷", "麹町", "永田町", "桜田門",
                "有楽町", "銀座一丁目", "新富町", "月島", "豊洲", "辰巳", "新木場"],
    "副都心線": ["和光市", "地下鉄成増", "地下鉄赤塚", "平和台", "氷川台", "小竹向原", "千川", "要町",
                "池袋", "雑司が谷", "西早稲田", "東新宿", "新宿三丁目", "北参道", "明治神宮前", "渋谷"],
    "東西線": ["中野", "落合", "高田馬場", "早稲田", "神楽坂", "飯田橋", "九段下", "竹橋", "大手町",
              "日本橋", "茅場町", "門前仲町", "木場", "東陽町", "南砂町", "西葛西", "葛西", "浦安", "西船橋"],
    "都営新宿線": ["新宿", "新宿三丁目", "曙橋", "市ヶ谷", "九段下", "神保町", "小川町", "岩本町",
                  "馬喰横山", "浜町", "森下", "菊川", "住吉", "西大島", "大島", "東大島", "船堀", "一之江", "瑞江", "篠崎", "本八幡"],
    "京王線": ["新宿", "初台", "幡ヶ谷", "笹塚", "代田橋", "明大前", "下高井戸", "桜上水", "上北沢",
              "八幡山", "芦花公園", "千歳烏山", "仙川", "つつじヶ丘", "柴崎", "国領", "布田", "調布"],
    "総武線": ["東京", "新日本橋", "馬喰町", "錦糸町", "亀戸", "平井", "新小岩", "小岩", "市川", "本八幡",
              "下総中山", "西船橋", "船橋", "東船橋", "津田沼", "幕張本郷", "幕張", "新検見川", "稲毛", "千葉"],
    "総武中央線": ["三鷹", "吉祥寺", "西荻窪", "荻窪", "阿佐ケ谷", "高円寺", "中野", "東中野", "大久保", "新宿",
                  "代々木", "千駄ケ谷", "信濃町", "四ツ谷", "市ケ谷", "飯田橋", "水道橋", "御茶ノ水", "秋葉原",
                  "浅草橋", "両国", "錦糸町", "亀戸", "平井", "新小岩", "小岩", "市川", "本八幡", "西船橋", "津田沼", "千葉"],
    "半蔵門線": ["渋谷", "表参道", "青山一丁目", "永田町", "半蔵門", "九段下", "神保町", "大手町", "三越前",
                "水天宮前", "清澄白河", "住吉", "錦糸町", "押上"],
}

WALK_TIERS = [1, 3, 5, 7, 10, 15, 20]
AREA_TIERS = [20, 25, 30, 40, 50, 60, 70, 80, 100]


def get_price_upper_limit(rent_yen):
    """rent_yen → 万円，向上取整到0.5万"""
    return math.ceil(rent_yen / 10000 * 2) / 2


def get_walk_tier(walk_minutes):
    if not walk_minutes:
        return None
    for t in WALK_TIERS:
        if walk_minutes <= t:
            return t
    return None


def get_area_tier(area_sqm):
    if not area_sqm:
        return None
    result = None
    for t in AREA_TIERS:
        if area_sqm >= t:
            result = t
        else:
            break
    return result


def get_neighboring_stations(railway, station):
    stations = RAILWAY_STATIONS.get(railway, [])
    if not stations or station not in stations:
        return [station] if station else []
    idx = stations.index(station)
    neighbors = []
    if idx > 0:
        neighbors.append(stations[idx - 1])
    neighbors.append(station)
    if idx < len(stations) - 1:
        neighbors.append(stations[idx + 1])
    return neighbors


def query_market_rank(page, prop):
    """SUUMO 市场排名查询，返回 rank_data 或 None"""
    rent = prop.get("rent", 0)
    management_fee = prop.get("management_fee", 0)
    total_monthly = rent + management_fee
    area = prop.get("area_sqm", 0)
    walk = prop.get("walk_minutes", 10)
    station = prop.get("station", "")
    railway = prop.get("railway", "")
    key_money = prop.get("key_money")

    price_upper = get_price_upper_limit(total_monthly)
    walk_tier = get_walk_tier(walk)
    area_tier = get_area_tier(area)
    no_key_money = (key_money is None or key_money == 0)

    if not railway or not station:
        return None

    try:
        page.goto("https://suumo.jp/kanto/", timeout=60000)
        time.sleep(1)

        for label_text in ["賃貸物件", "東京都", "沿線"]:
            link = page.locator(f'a:has-text("{label_text}")').first
            if link.count() > 0:
                link.click()
                time.sleep(1)

        railway_short = railway.replace("線", "").replace("東京メトロ", "").strip()
        line_cb = page.locator(f'label:has-text("{railway}")').first
        if line_cb.count() == 0:
            line_cb = page.locator(f'label:has-text("{railway_short}")').first
        if line_cb.count() > 0:
            line_cb.click()
            time.sleep(1)

        search_btn = page.locator('button:has-text("検索"), input[value*="検索"], button:has-text("この条件で検索")').first
        if search_btn.count() > 0:
            search_btn.click()
            time.sleep(1.5)

        # 选车站
        neighbors = get_neighboring_stations(railway, station)
        for st in neighbors:
            cb = page.locator(f'label:has-text("{st}")').first
            if cb.count() > 0:
                try:
                    cb.click()
                    time.sleep(0.3)
                except:
                    pass

        search_btn = page.locator('button:has-text("検索"), input[value*="検索"]').first
        if search_btn.count() > 0:
            search_btn.click()
            time.sleep(1.5)

        # 设置价格/徒步/面积/礼金
        try:
            change = page.locator('a:has-text("条件を変更"), button:has-text("条件を変更"), a:has-text("絞り込み")').first
            if change.count() > 0:
                change.click()
                time.sleep(1)

            price_val = str(price_upper).replace('.0', '').replace('.5', '5')
            rent_sel = page.locator('select[name*="cb"], select[name*="rt"]').first
            if rent_sel.count() > 0:
                try:
                    rent_sel.select_option(value=price_val)
                except:
                    try: rent_sel.select_option(label=f"{price_upper}万円")
                    except: pass

            if walk_tier:
                walk_sel = page.locator('select[name*="ts"], select[name*="tc"]').first
                if walk_sel.count() > 0:
                    try: walk_sel.select_option(value=str(walk_tier))
                    except:
                        try: walk_sel.select_option(label=f"{walk_tier}分以内")
                        except: pass

            if area_tier:
                area_sel = page.locator('select[name*="mb"], select[name*="md"]').first
                if area_sel.count() > 0:
                    try: area_sel.select_option(value=str(area_tier))
                    except:
                        try: area_sel.select_option(label=f"{area_tier}㎡以上")
                        except: pass

            if no_key_money:
                try:
                    rk = page.locator('input[type="checkbox"][name*="kz"], label:has-text("礼金なし")').first
                    if rk.count() > 0 and not rk.is_checked():
                        rk.click()
                except:
                    pass

            apply_btn = page.locator('button:has-text("検索"), input[value*="検索"], button:has-text("この条件で検索")').first
            if apply_btn.count() > 0:
                apply_btn.click()
                time.sleep(1.5)
        except Exception:
            pass

        # 收集价格
        prices = []
        price_upper_yen = price_upper * 10000
        time.sleep(1)

        for selector in ['.cassetteitem_price--rent', '.detailbox-property-point', '[class*="price"]', '[class*="rent"]']:
            for elem in page.locator(selector).all():
                try:
                    text = elem.inner_text()
                    m = re.search(r'(\d+(?:\.\d+)?)\s*万', text)
                    if m:
                        price = float(m.group(1)) * 10000
                        if 10000 < price <= price_upper_yen:
                            prices.append(price)
                except:
                    continue
            if prices:
                break

        if not prices:
            body = page.locator("body").inner_text()
            for m in re.findall(r'(\d+(?:\.\d+)?)\s*万円', body):
                price = float(m) * 10000
                if 10000 < price <= price_upper_yen:
                    prices.append(price)

        prices = list(set(prices))
        if not prices:
            return None

        total = len(prices)
        cheaper = sum(1 for p in prices if p < total_monthly)
        rank = cheaper + 1
        percentile = round((cheaper / total) * 100, 1) if total > 0 else 0

        return {
            "total_properties": total,
            "rank": rank,
            "percentile": percentile
        }

    except Exception as e:
        log(f"    market_rank 异常: {str(e)[:80]}")
        return None


def query_ad_count(page, context, prop):
    """SUUMO 广告数查询，返回 int 或 None"""
    rent = prop.get("rent", 0)
    area = prop.get("area_sqm", 0)
    walk = prop.get("walk_minutes", 10)
    railway = prop.get("railway", "")
    station = prop.get("station", "")

    if not railway or not station:
        return None

    price_upper = get_price_upper_limit(rent)
    walk_tier = get_walk_tier(walk)
    area_tier = get_area_tier(area)
    neighbors = get_neighboring_stations(railway, station)

    try:
        page.goto('https://suumo.jp/chintai/tokyo/', timeout=30000)
        time.sleep(2)
        try:
            page.click('a:has-text("沿線・駅から探す")', timeout=10000)
            time.sleep(2)
        except:
            return None

        railway_short = railway.replace("線", "").replace("東京メトロ", "").strip()
        line_cb = page.locator(f'label:has-text("{railway}")').first
        if line_cb.count() == 0:
            line_cb = page.locator(f'label:has-text("{railway_short}")').first
        if line_cb.count() == 0:
            return None
        line_cb.click(timeout=10000)
        time.sleep(1)

        clicked = 0
        for st in neighbors:
            try:
                page.click(f'label:has-text("{st}")', timeout=5000)
                clicked += 1
                time.sleep(0.3)
            except:
                pass
        if clicked == 0:
            return None

        page.click('a:has-text("この条件で検索する")', timeout=15000)
        time.sleep(3)

        # 设置筛选
        price_text = f'{int(price_upper)}万円' if price_upper == int(price_upper) else f'{price_upper}万円'
        try:
            ct = page.locator('select[name="ct"]').first
            if ct.count() > 0:
                for opt in ct.locator('option').all():
                    if price_text in opt.inner_text():
                        ct.select_option(label=opt.inner_text())
                        break

            et = page.locator('select[name="et"]').first
            if et.count() > 0 and walk_tier:
                for opt in et.locator('option').all():
                    if f'{walk_tier}分' in opt.inner_text():
                        et.select_option(label=opt.inner_text())
                        break

            if area_tier:
                mb = page.locator('select[name="mb"]').first
                if mb.count() > 0:
                    for opt in mb.locator('option').all():
                        if f'{area_tier}m' in opt.inner_text():
                            mb.select_option(label=opt.inner_text())
                            break

            page.click('a:has-text("検索する")', timeout=15000)
            time.sleep(3)
        except:
            return None

        rent_man = rent / 10000
        ad_count = None

        for page_num in range(5):
            try:
                if page_num > 0:
                    nb = page.locator('a:has-text("次へ")').first
                    if nb.count() > 0:
                        nb.click(timeout=10000)
                        time.sleep(2)
                    else:
                        break

                for casette in page.locator('.cassetteitem').all():
                    try:
                        rent_elem = casette.locator('.cassetteitem_price--rent').first
                        if rent_elem.count() == 0:
                            continue
                        m = re.search(r'(\d+(?:\.\d+)?)\s*万', rent_elem.inner_text())
                        if not m:
                            continue
                        if abs(float(m.group(1)) - rent_man) > 0.2:
                            continue

                        area_elem = casette.locator('.cassetteitem_menseki').first
                        if area_elem.count() > 0:
                            am = re.search(r'(\d+(?:\.\d+)?)', area_elem.inner_text())
                            if am and abs(float(am.group(1)) - area) > 2:
                                continue

                        for link in casette.locator('a').all():
                            href = link.get_attribute('href') or ''
                            if '/chintai/' in href and 'jnc_' in href:
                                full_url = 'https://suumo.jp' + href if href.startswith('/') else href
                                detail_page = context.new_page()
                                try:
                                    detail_page.goto(full_url, timeout=30000)
                                    time.sleep(2)
                                    html = detail_page.content()
                                    other = 0
                                    om = re.search(r'他の店舗が(\d+)店', html)
                                    if om:
                                        other = int(om.group(1))
                                    ad_count = 1 + other
                                except:
                                    pass
                                finally:
                                    detail_page.close()
                                break
                        if ad_count:
                            break
                    except:
                        continue
                if ad_count:
                    break
            except:
                break

        return ad_count

    except Exception as e:
        log(f"    ad_count 异常: {str(e)[:80]}")
        return None


# ============================================================
# SUUMO kwd 关键字搜索 (写 TOP 前的高竞争预过滤)
# 注: 这段是 watch_registrations.py:count_suumo_listings 的副本,
# 用户决策 (2026-04-27) 两边独立维护, 改一边不影响另一边。
# 如需修复 SUUMO 页面变化, 两处都要同步改。
# ============================================================
KWD_SEARCH_URL = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&ta=13"
KWD_RENT_TOL_MAN = 0.5
KWD_AREA_TOL_M2 = 2.0


def _kwd_normalize_name(name):
    """物件名清洗: 去括号读み + 去尾部号室 + 多空格压一格"""
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


def _kwd_parse_cassette(text):
    """从 cassette inner_text 提取 rent_man 和 area_sqm"""
    rent_m = re.search(r"賃料[:：]\s*([\d.]+)\s*万", text)
    area_m = re.search(r"専有面積[:：]\s*([\d.]+)\s*m", text)
    rent = float(rent_m.group(1)) if rent_m else None
    area = float(area_m.group(1)) if area_m else None
    return rent, area


def _kwd_count_listings(page, building_name, target_rent_man, target_area_sqm):
    """
    用物件名做 SUUMO kwd 搜索, 按 rent±KWD_RENT_TOL & area±KWD_AREA_TOL 过滤,
    返回过滤后件数 = 当前已公开此房间的中介数。无结果 0, 出错 None。
    """
    keyword = _kwd_normalize_name(building_name)
    if not keyword:
        return None

    try:
        page.goto(KWD_SEARCH_URL, timeout=30000)
        time.sleep(1.5)

        kwd_input = page.locator('input[name="kwd"]').first
        if kwd_input.count() == 0:
            log(f"    kwd 搜索: 找不到 input[name=kwd]")
            return None
        kwd_input.fill(keyword)
        time.sleep(0.3)
        kwd_input.press("Enter")

        page.wait_for_url("**/JJ901FC001/**", timeout=15000)
        time.sleep(1.5)

        body = page.locator("body").inner_text()
        total_m = re.search(r"物件\s*\n?\s*([\d,]+)\s*件\s*\n?\s*検索条件", body)
        total = int(total_m.group(1).replace(",", "")) if total_m else 0
        if total == 0:
            return 0

        cassettes = page.locator(".cassettebox").all()
        matched = 0
        for c in cassettes:
            try:
                text = c.inner_text()
                rent_c, area_c = _kwd_parse_cassette(text)
                if rent_c is None or area_c is None:
                    continue
                if abs(rent_c - target_rent_man) > KWD_RENT_TOL_MAN:
                    continue
                if abs(area_c - target_area_sqm) > KWD_AREA_TOL_M2:
                    continue
                matched += 1
            except Exception:
                continue

        return matched

    except Exception as e:
        log(f"    kwd_count 异常: {str(e)[:80]}")
        return None


# ============================================================
# Step 6: 推薦点数计算
# ============================================================
def calculate_recommendation(view, inquiry, ad_count, station=None, ward=None):
    norm_view = min(view / 10, 1.0) * 10
    norm_inquiry = min(inquiry / 5, 1.0) * 10
    competition = max(0, 10 - (ad_count - 1) * 0.5) if ad_count else 5.0
    market = norm_view  # 暂用 view 作为市场指标
    total = (
        norm_view * WEIGHTS['view_score'] +
        norm_inquiry * WEIGHTS['inquiry_score'] +
        competition * WEIGHTS['competition'] +
        market * WEIGHTS['market_rank']
    )
    # 热门駅 / 区 加分 (HOT_STATIONS 优先, 二重カウント回避)
    is_hot_station = False
    if station:
        normalized = station.rstrip("駅").strip()
        if normalized in HOT_STATIONS:
            total += HOT_STATION_BONUS
            is_hot_station = True
    # HOT_STATIONS 非適用時のみ区 bonus 適用
    if not is_hot_station and ward and ward in WARD_REVERB_BONUS:
        total += WARD_REVERB_BONUS[ward]
    return round(total, 2)


# ============================================================
# Step 7: 实时写入TOP表 (无大小限制, 由 archive_old_recommendations.py 周期归档)
# ============================================================
def reins_id_exists_in_top(db_id, reins_id):
    """单点查询: TOP DB 里是否已有 REINS_ID 的活跃 row"""
    hits = notion_query(db_id, filter_obj={
        "property": "REINS_ID",
        "title": {"equals": reins_id}
    })
    return len(hits) > 0


def add_to_top_db(db_id, db_name, prop, listing_count=None, status_name="広告待ち"):
    """物件实时添加到 TOP DB. 去重(REINS_ID 已存在则跳过), 不再有大小上限。
    listing_count: 写入时同步设置「登録店舗数」字段(免得 watch_registrations 2h 后才填)
    status_name: 初始 Status (可→広告待ち; 確認待ち→確認待ち, staff 还需走商号判定)"""
    with _notion_lock:
        if reins_id_exists_in_top(db_id, prop["reins_id"]):
            return False  # 已存在

        properties = {
            "REINS_ID": {"title": [{"text": {"content": prop["reins_id"]}}]},
            "推薦点数": {"number": prop["score"]},
            "物件名": {"rich_text": [{"text": {"content": prop.get("building_name", "")}}]} if prop.get("building_name") else {"rich_text": []},
            "管理会社": {"rich_text": [{"text": {"content": prop.get("management_company", "")}}]} if prop.get("management_company") else {"rich_text": []},
            "公開日時": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
            "Status": {"status": {"name": status_name}}
        }
        if listing_count is not None:
            properties["登録店舗数"] = {"number": int(listing_count)}

        if notion_create(db_id, properties):
            extra = f" 登録店舗数={listing_count}" if listing_count is not None else ""
            log(f"    → 写入 {db_name}: {prop['reins_id']} ({prop['score']}分) Status={status_name}{extra}")
            return True
        return False


# ============================================================
# 单物件主流程
# ============================================================
def process_property(prop_data, browser_page, browser_context):
    """处理单个物件，返回 status 字符串"""
    page_id = prop_data["page_id"]
    rid = prop_data.get("bukken_number", "?")

    # Step 1: 预测 view
    if not prop_data.get("rent"):
        log(f"  缺少賃料，跳过")
        return "no_rent"

    view = predict_view(prop_data)
    notion_update(page_id, {"予測_view数": {"number": view}})
    log(f"  view: {view} | ¥{prop_data['rent']:,} {prop_data.get('area_sqm','?')}㎡ {prop_data.get('city','')} {prop_data.get('floor_plan','')}")

    # Step 2: 低分跳过
    if view < VIEW_THRESHOLD:
        notion_update(page_id, {
            "広告可": {"select": {"name": "--"}},
            "市場順位": {"rich_text": [{"text": {"content": "--"}}]}
        })
        return "low_view"

    # Step 3: 管理公司检查
    company = prop_data.get("management_company", "")
    ad_status = check_management(company)
    notion_update(page_id, {"広告可": {"select": {"name": ad_status}}})
    log(f"  広告可: {ad_status} ({company[:30]})")

    # 不可（仲介）: 永不进 TOP 表,跳过后续 SUUMO 抓取
    if ad_status == "不可（仲介）":
        notion_update(page_id, {"市場順位": {"rich_text": [{"text": {"content": "--"}}]}})
        return "unallowed"

    # Step 4: 反响数预测
    inquiry = predict_inquiry(prop_data)
    notion_update(page_id, {"予測_反響数": {"number": inquiry}})
    log(f"  反響: {inquiry}")

    # Step 5: SUUMO 市场排名
    try:
        rank_data = query_market_rank(browser_page, prop_data)
        if rank_data:
            rank_text = f"{rank_data['rank']}/{rank_data['total_properties']} ({rank_data['percentile']}%)"
            notion_update(page_id, {"市場順位": {"rich_text": [{"text": {"content": rank_text}}]}})
            log(f"  市場順位: {rank_text}")
        else:
            notion_update(page_id, {"市場順位": {"rich_text": [{"text": {"content": "err"}}]}})
            log(f"  市場順位: err")
    except Exception as e:
        notion_update(page_id, {"市場順位": {"rich_text": [{"text": {"content": "err"}}]}})
        log(f"  市場順位 异常: {str(e)[:80]}")

    # Step 6: SUUMO 广告数
    ad_count = None
    try:
        ad_count = query_ad_count(browser_page, browser_context, prop_data)
        if ad_count:
            notion_update(page_id, {"広告数": {"number": ad_count}})
            log(f"  広告数: {ad_count}")
        else:
            log(f"  広告数: 未找到")
    except Exception as e:
        log(f"  広告数 异常: {str(e)[:80]}")

    # Step 7: 推薦点数 (含热门駅 / 区 反响効率 加分)
    station = prop_data.get("station", "")
    ward = prop_data.get("city", "")
    is_hot = bool(station and station.rstrip("駅").strip() in HOT_STATIONS)
    ward_bonus = WARD_REVERB_BONUS.get(ward, 0.0) if not is_hot else 0.0
    score = calculate_recommendation(view, inquiry, ad_count, station=station, ward=ward)
    notion_update(page_id, {"推薦点数": {"number": score}})
    if is_hot:
        log(f"  ⭐ HOT 駅 ({station}) → score +{HOT_STATION_BONUS} → {score}")
    elif ward_bonus != 0.0:
        sign = "+" if ward_bonus > 0 else ""
        log(f"  📍 区 bonus ({ward}) → score {sign}{ward_bonus} → {score}")
    else:
        log(f"  推薦点数: {score}")

    # Step 8: 实时写入TOP表 (含高竞争预过滤)
    # 合并后只有 1 张 TOP DB (おすすめ), 用 Status 区分商号已认可 vs 待确认
    if score >= RECOMMEND_THRESHOLD:
        initial_status = None
        if ad_status == "可":
            initial_status = "広告待ち"      # 商号已是 whitelist, 直接进广告队列
        elif ad_status == "確認待ち":
            initial_status = "確認待ち"      # 商号未确认, staff 看到后填 会社広告可否, 再改 Status

        if initial_status:
            # 高竞争预过滤: 用 SUUMO kwd 搜索看已有几家中介公开了此房间
            listing_count = _kwd_count_listings(
                browser_page,
                prop_data.get("building_name", ""),
                prop_data.get("rent", 0) / 10000,
                prop_data.get("area_sqm", 0) or 0,
            )
            if listing_count is not None and listing_count > MAX_COMPETITION_FOR_ENTRY:
                log(f"  ⚠ SUUMO 中介数 {listing_count} > {MAX_COMPETITION_FOR_ENTRY}, 跳过写 TOP 表 (高竞争)")
                return "high_competition"

            top_prop = {
                "reins_id": rid,
                "score": score,
                "building_name": prop_data.get("building_name", ""),
                "management_company": company
            }
            add_to_top_db(RECOMMEND_DATABASE_ID, "新着物件おすすめ", top_prop,
                          listing_count=listing_count, status_name=initial_status)

    return "success"


# ============================================================
# 主流程
# ============================================================
def get_current_cutoff():
    """返回当前最近一次截止时间(JST)。流水线只处理此时间之后创建的物件"""
    now = datetime.now(JST)
    today_cutoffs = [
        now.replace(hour=h, minute=CUTOFF_MINUTE, second=0, microsecond=0)
        for h in CUTOFF_HOURS
    ]
    past = [c for c in today_cutoffs if c <= now]
    if past:
        return max(past)
    # 在今天第一个截止时间(11:00)之前 → 用昨天的最后一个截止时间(23:00)
    yesterday = now - timedelta(days=1)
    return yesterday.replace(hour=23, minute=CUTOFF_MINUTE, second=0, microsecond=0)


def fetch_unscored_properties(cutoff):
    """从 Notion 获取指定截止时间之后未评分的物件（最新的优先）"""
    return notion_query(
        DATABASE_ID,
        filter_obj={
            "and": [
                {"property": "予測_view数", "number": {"is_empty": True}},
                {"timestamp": "created_time", "created_time": {"after": cutoff.isoformat()}}
            ]
        },
        sorts=[{"timestamp": "created_time", "direction": "descending"}]
    )


def _route_filter(route):
    """拦截图片/字体/CSS/分析脚本,加速 Playwright 页面加载"""
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


def _worker(name, work_queue, stats, stop_event):
    """Worker 线程: 独占一个 Playwright 浏览器实例,持续从队列消费物件"""
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1920, 'height': 1080}, locale='ja-JP')
    context.route("**/*", _route_filter)
    page = context.new_page()
    processed = 0
    try:
        while not stop_event.is_set():
            try:
                item = work_queue.get(timeout=1)
            except Empty:
                continue
            if item is None:
                work_queue.task_done()
                break
            idx, total, data = item
            log(f"\n[{name} {idx}/{total}] {data.get('bukken_number','?')}")
            try:
                result = process_property(data, page, context)
                with _stats_lock:
                    stats[result] = stats.get(result, 0) + 1
            except Exception as e:
                log(f"  ✗ [{name}] 处理异常: {str(e)[:120]}")
                with _stats_lock:
                    stats["error"] = stats.get("error", 0) + 1
            finally:
                work_queue.task_done()
                processed += 1
    finally:
        try:
            browser.close()
            pw.stop()
        except Exception:
            pass
        log(f"[{name}] 退出 (处理 {processed} 件)")


def _fill_queue(work_queue, pages):
    """把 Notion 原始页转为 data 后入队,返回入队数量"""
    enqueued = 0
    total = len(pages)
    for raw_page in pages:
        data = extract_property(raw_page)
        if data:
            enqueued += 1
            work_queue.put((enqueued, total, data))
    return enqueued


def main():
    log("=" * 60)
    log("Property Processing Pipeline V2 - 截止时间感知")
    log(f"WORKER_COUNT = {WORKER_COUNT}")
    log("=" * 60)

    current_cutoff = get_current_cutoff()
    log(f"\n当前截止时间: {current_cutoff.strftime('%Y-%m-%d %H:%M JST')}")

    log("查询未评分物件...")
    pages = fetch_unscored_properties(current_cutoff)
    log(f"未评分物件: {len(pages)} 个")

    _limit = os.getenv("PIPELINE_LIMIT")
    if _limit:
        pages = pages[:int(_limit)]
        log(f"PIPELINE_LIMIT={_limit}: 只处理前 {len(pages)} 个")

    if not pages:
        log("没有需要处理的物件")
        return

    work_queue = Queue()
    enqueued = _fill_queue(work_queue, pages)
    log(f"入队: {enqueued} 个")

    stats = {"success": 0, "low_view": 0, "unallowed": 0, "high_competition": 0, "no_rent": 0, "error": 0}
    stop_event = threading.Event()

    log(f"\n启动 {WORKER_COUNT} 个并发 Playwright worker (headless, 资源拦截开启)...")
    workers = []
    for i in range(WORKER_COUNT):
        t = threading.Thread(
            target=_worker,
            args=(f"W{i+1}", work_queue, stats, stop_event),
            daemon=False,
            name=f"worker-{i+1}"
        )
        t.start()
        workers.append(t)

    try:
        # 主线程:监控截止时间 + 等待队列空
        while any(t.is_alive() for t in workers):
            new_cutoff = get_current_cutoff()
            if new_cutoff != current_cutoff:
                log(f"\n>>> 截止时间变化: {current_cutoff.strftime('%H:%M')} → {new_cutoff.strftime('%H:%M')}")
                log(f">>> 清空队列,重新拉取")
                while True:
                    try:
                        work_queue.get_nowait()
                        work_queue.task_done()
                    except Empty:
                        break
                current_cutoff = new_cutoff
                pages = fetch_unscored_properties(current_cutoff)
                added = _fill_queue(work_queue, pages)
                log(f">>> 新队列: {added} 个")

            if work_queue.empty():
                # 队列空 → 发毒丸让 worker 退出
                for _ in range(WORKER_COUNT):
                    work_queue.put(None)
                break

            time.sleep(10)
    except KeyboardInterrupt:
        log("\n>>> 收到中断,清空队列让 worker 收尾")
        stop_event.set()
        while True:
            try:
                work_queue.get_nowait()
                work_queue.task_done()
            except Empty:
                break
        for _ in range(WORKER_COUNT):
            work_queue.put(None)
    finally:
        for t in workers:
            t.join(timeout=120)
        log("\n所有 worker 已关闭")

    log(f"\n{'='*60}")
    log("完成!")
    log(f"截止时间: {current_cutoff.strftime('%Y-%m-%d %H:%M JST')}")
    for k, v in stats.items():
        if v > 0:
            log(f"  {k}: {v}")
    log(f"{'='*60}")


if __name__ == "__main__":
    main()
