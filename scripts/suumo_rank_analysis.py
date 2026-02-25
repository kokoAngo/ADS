"""
SUUMO市场排名分析 v2
对于高分物件（得分>7），在SUUMO上搜索同等条件的物件
查看该物件的价格在市场中的排名
"""
import os
import sys
import time
import re
import math
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

# 条件记录文件
CONDITIONS_FILE = "Conditions.md"
# 日志文件
LOG_FILE = "logs/suumo_rank.log"

# 确保logs目录存在
os.makedirs("logs", exist_ok=True)

# 日志输出函数
def log(msg):
    """同时输出到控制台和日志文件"""
    print(msg)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import requests

sys.stdout.reconfigure(line_buffering=True)
load_dotenv()

# Notion配置
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}


def update_notion_rank(page_id, rank_data):
    """更新Notion中的市场排名列"""
    url = f"https://api.notion.com/v1/pages/{page_id}"

    # 格式: "3/25 (12.0%)"
    rank_text = f"{rank_data['rank']}/{rank_data['total_properties']} ({rank_data['percentile']}%)"

    data = {
        "properties": {
            "市場順位": {
                "rich_text": [{"text": {"content": rank_text}}]
            }
        }
    }

    try:
        response = requests.patch(url, headers=headers, json=data, timeout=30)
        return response.status_code == 200
    except Exception as e:
        log(f"    Notion更新失败: {e}")
        return False


def log_search_condition(prop, rank_data, stations_searched):
    """记录搜索条件到Markdown文件"""
    file_exists = os.path.exists(CONDITIONS_FILE)

    with open(CONDITIONS_FILE, 'a', encoding='utf-8') as f:
        # 如果文件不存在，写入标题和表头
        if not file_exists:
            f.write("# SUUMO市场排名搜索条件记录\n\n")
            f.write("| 时间 | REINS_ID | 得分 | 月费(租金+管理费) | 面积 | 徒步 | 沿线/车站 | 搜索条件 | 排名 | 百分位 |\n")
            f.write("|------|----------|------|-------------------|------|------|-----------|----------|------|--------|\n")

        # 格式化月费显示
        rent = prop.get('rent', 0)
        mgmt = prop.get('management_fee', 0)
        total = prop.get('total_monthly', rent)
        if mgmt > 0:
            monthly_str = f"¥{rent:,}+{mgmt:,}=¥{total:,}"
        else:
            monthly_str = f"¥{rent:,}"

        # 格式化搜索条件
        conditions = []
        if rank_data:
            conditions.append(f"≤{rank_data.get('price_upper')}万")
            if rank_data.get('walk_tier'):
                conditions.append(f"徒步≤{rank_data.get('walk_tier')}分")
            if rank_data.get('area_tier'):
                conditions.append(f"面积≥{rank_data.get('area_tier')}㎡")
            if rank_data.get('no_key_money'):
                conditions.append("礼金なし")
        conditions_str = ", ".join(conditions)

        # 格式化沿线/车站
        railway = prop.get('railway', '')
        station = prop.get('station', '')
        stations_str = '/'.join(stations_searched) if stations_searched else station
        location_str = f"{railway} {stations_str}"

        # 格式化排名
        if rank_data:
            rank_str = f"{rank_data.get('rank')}/{rank_data.get('total_properties')}"
            percentile_str = f"{rank_data.get('percentile')}%"
        else:
            rank_str = "-"
            percentile_str = "-"

        # 写入数据行
        time_str = datetime.now().strftime('%m-%d %H:%M')
        area = prop.get('area', '-')
        walk = prop.get('walk', '-')
        score = prop.get('score', '-')

        f.write(f"| {time_str} | {prop.get('reins_id', '')} | {score} | {monthly_str} | {area}㎡ | {walk}分 | {location_str} | {conditions_str} | {rank_str} | {percentile_str} |\n")


# 沿线车站顺序映射（用于获取前后站）
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


def get_price_upper_limit(rent):
    """计算价格上限（向上取整到0.5万）
    例: 6.3万 → 6.5万, 6.5万 → 6.5万, 6.7万 → 7.0万
    """
    rent_man = rent / 10000  # 转换为万
    # 向上取整到0.5
    upper_man = math.ceil(rent_man * 2) / 2
    return upper_man


# SUUMO徒步时间档位
WALK_TIERS = [1, 3, 5, 7, 10, 15, 20]


def get_walk_tier(walk_minutes):
    """获取徒步时间档位（选择大于等于实际时间的最小档位）
    例: 8分 → 10分以内, 5分 → 5分以内, 12分 → 15分以内
    """
    if not walk_minutes:
        return None
    for tier in WALK_TIERS:
        if walk_minutes <= tier:
            return tier
    return None  # 超过20分钟不设限制


# SUUMO面积档位（平米）
AREA_TIERS = [20, 25, 30, 40, 50, 60, 70, 80, 100]


def get_area_tier(area_sqm):
    """获取面积档位（选择小于等于实际面积的最大档位作为下限）
    例: 33㎡ → 30㎡以上, 25㎡ → 25㎡以上, 45㎡ → 40㎡以上
    """
    if not area_sqm:
        return None
    result = None
    for tier in AREA_TIERS:
        if area_sqm >= tier:
            result = tier
        else:
            break
    return result


def get_neighboring_stations(railway, station):
    """获取前后站（共3站范围）"""
    stations = RAILWAY_STATIONS.get(railway, [])
    if not stations or station not in stations:
        return [station]  # 如果找不到，只返回原站

    idx = stations.index(station)
    neighbors = []

    # 前一站
    if idx > 0:
        neighbors.append(stations[idx - 1])

    # 当前站
    neighbors.append(station)

    # 后一站
    if idx < len(stations) - 1:
        neighbors.append(stations[idx + 1])

    return neighbors


def get_high_score_properties(min_score=7.0):
    """获取高分且没有市場順位的物件列表"""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    data = {
        "filter": {
            "and": [
                {
                    "property": "予測_view数",
                    "number": {"greater_than_or_equal_to": min_score}
                },
                {
                    "property": "市場順位",
                    "rich_text": {"is_empty": True}
                }
            ]
        },
        "page_size": 50
    }

    response = requests.post(url, headers=headers, json=data, timeout=30)
    result = response.json()
    pages = result.get("results", [])

    properties = []
    for page in pages:
        props = page.get("properties", {})

        def get_number(prop_name):
            prop = props.get(prop_name, {})
            return prop.get("number")

        def get_text(prop_name):
            prop = props.get(prop_name, {})
            if prop.get("type") == "rich_text":
                arr = prop.get("rich_text", [])
                return arr[0].get("text", {}).get("content", "") if arr else ""
            elif prop.get("type") == "title":
                arr = prop.get("title", [])
                return arr[0].get("text", {}).get("content", "") if arr else ""
            return ""

        reins_id = get_text("REINS_ID")
        score = get_number("予測_view数")
        rent = get_number("賃料") or get_number("価格_賃料(万)")
        area = get_number("専有面積") or get_number("面積・不動産ID_使用部分面積(m2)")
        walk = get_number("徒歩分数") or get_number("交通1_駅より徒歩(分)")
        railway = get_text("交通1_沿線名")
        station = get_text("交通1_駅名")
        floor_plan = get_text("間取り")
        key_money = get_number("礼金(ヶ月)")  # 礼金（月数）
        management_fee = get_number("管理費(万)")  # 管理費（万円）

        # 租金单位转换（如果是万为单位）
        if rent and rent < 1000:
            rent = int(rent * 10000)

        # 管理费单位转换（从万转换为日元）
        if management_fee:
            management_fee = int(management_fee * 10000)
        else:
            management_fee = 0

        if reins_id and rent:
            properties.append({
                "page_id": page["id"],
                "reins_id": reins_id,
                "score": score,
                "rent": int(rent),
                "management_fee": management_fee,  # 管理費
                "total_monthly": int(rent) + management_fee,  # 月总费用
                "area": area,
                "walk": walk,
                "railway": railway,
                "station": station,
                "floor_plan": floor_plan,
                "key_money": key_money  # 礼金
            })

    return properties


def analyze_market_rank(page, prop):
    """在SUUMO上分析市场排名 - 通过手动导航
    返回: (rank_data, stations_searched) 元组
    使用房租+管理费的总和进行比较
    """
    rent = prop.get("rent", 0)
    management_fee = prop.get("management_fee", 0)
    total_monthly = prop.get("total_monthly", rent)  # 月总费用 = 房租 + 管理费
    area = prop.get("area", 0)
    walk = prop.get("walk", 10)
    station = prop.get("station", "")
    railway = prop.get("railway", "")
    key_money = prop.get("key_money")  # 礼金（月数）
    stations_searched = []  # 记录搜索的车站

    # 计算筛选条件 - 使用月总费用计算价格上限
    price_upper = get_price_upper_limit(total_monthly)
    walk_tier = get_walk_tier(walk)
    area_tier = get_area_tier(area)
    no_key_money = (key_money is None or key_money == 0)  # 是否无礼金

    # 显示筛选条件
    conditions = [f"价格≤{price_upper}万"]
    if walk_tier:
        conditions.append(f"徒步≤{walk_tier}分")
    if area_tier:
        conditions.append(f"面积≥{area_tier}㎡")
    if no_key_money:
        conditions.append("礼金なし")
    log(f"    筛选条件: {', '.join(conditions)}")

    try:
        # 1. 访问SUUMO首页
        page.goto("https://suumo.jp/kanto/", timeout=60000)
        time.sleep(1)

        # 2. 点击賃貸物件
        rental_link = page.locator('a:has-text("賃貸物件")').first
        if rental_link.count() > 0:
            rental_link.click()
            time.sleep(1)

        # 3. 点击東京都
        tokyo_link = page.locator('a:has-text("東京都")').first
        if tokyo_link.count() > 0:
            tokyo_link.click()
            time.sleep(1)

        # 4. 点击沿線
        ensen_link = page.locator('a:has-text("沿線")').first
        if ensen_link.count() > 0:
            ensen_link.click()
            time.sleep(1)

        # 5. 查找并点击沿线
        if railway:
            # 简化沿线名称
            railway_short = railway.replace("線", "").replace("東京メトロ", "").strip()
            line_checkbox = page.locator(f'label:has-text("{railway}")').first
            if line_checkbox.count() == 0:
                line_checkbox = page.locator(f'label:has-text("{railway_short}")').first

            if line_checkbox.count() > 0:
                line_checkbox.click()
                time.sleep(1)

        # 6. 点击搜索/确定按钮
        search_btn = page.locator('button:has-text("検索"), input[value*="検索"], button:has-text("この条件で検索")').first
        if search_btn.count() > 0:
            search_btn.click()
            time.sleep(1.5)

        # 7. 如果有车站选择页面，选择车站（包括前后站，共3站）
        if station:
            # 获取前后站
            neighboring_stations = get_neighboring_stations(railway, station)
            log(f"    搜索范围: {' / '.join(neighboring_stations)}")

            selected_count = 0
            for st in neighboring_stations:
                station_checkbox = page.locator(f'label:has-text("{st}")').first
                if station_checkbox.count() > 0:
                    try:
                        station_checkbox.click()
                        stations_searched.append(st)  # 记录搜索的车站
                        selected_count += 1
                        time.sleep(0.5)
                    except:
                        pass

            if selected_count > 0:
                # 点击搜索
                search_btn = page.locator('button:has-text("検索"), input[value*="検索"]').first
                if search_btn.count() > 0:
                    search_btn.click()
                    time.sleep(1.5)

        # 8. 设置筛选条件（价格上限 + 徒步时间 + 面积下限）
        try:
            # 查找"条件を変更"或类似按钮
            change_btn = page.locator('a:has-text("条件を変更"), button:has-text("条件を変更"), a:has-text("絞り込み")').first
            if change_btn.count() > 0:
                change_btn.click()
                time.sleep(1)

            # 8-1. 设置賃料上限
            price_upper_value = str(price_upper).replace('.0', '').replace('.5', '5')
            rent_max_select = page.locator('select[name*="cb"], select[name*="rt"], select:near(:text("賃料"))').first
            if rent_max_select.count() > 0:
                try:
                    rent_max_select.select_option(value=price_upper_value)
                    time.sleep(1)
                except:
                    try:
                        rent_max_select.select_option(label=f"{price_upper}万円")
                    except:
                        pass

            # 8-2. 设置徒步时间上限
            if walk_tier:
                # SUUMO徒步时间选择器，常见name: ts, tc, walk
                walk_select = page.locator('select[name*="ts"], select[name*="tc"], select[name*="walk"], select:near(:text("徒歩"))').first
                if walk_select.count() > 0:
                    try:
                        # 尝试用value选择（如 "10"）
                        walk_select.select_option(value=str(walk_tier))
                        time.sleep(1)
                    except:
                        try:
                            # 尝试用label选择（如 "10分以内"）
                            walk_select.select_option(label=f"{walk_tier}分以内")
                        except:
                            pass

            # 8-3. 设置面积下限
            if area_tier:
                # SUUMO面积选择器，常见name: mb, md, menseki
                area_select = page.locator('select[name*="mb"], select[name*="md"], select[name*="menseki"], select:near(:text("専有面積"))').first
                if area_select.count() > 0:
                    try:
                        # 尝试用value选择（如 "30"）
                        area_select.select_option(value=str(area_tier))
                        time.sleep(1)
                    except:
                        try:
                            # 尝试用label选择（如 "30㎡以上"）
                            area_select.select_option(label=f"{area_tier}㎡以上")
                        except:
                            pass

            # 8-4. 如果没有礼金，勾选礼金なし
            if no_key_money:
                try:
                    # 尝试多种方式找到礼金なし复选框
                    reikin_checkbox = page.locator('input[type="checkbox"][name*="kz"], label:has-text("礼金なし"), input[id*="reikin"]').first
                    if reikin_checkbox.count() > 0:
                        # 检查是否已勾选
                        if not reikin_checkbox.is_checked():
                            reikin_checkbox.click()
                            time.sleep(0.5)
                    else:
                        # 尝试通过文本查找
                        reikin_label = page.locator('label:has-text("礼金なし")').first
                        if reikin_label.count() > 0:
                            reikin_label.click()
                            time.sleep(0.5)
                except Exception as e:
                    log(f"    礼金なし复选框设置失败: {e}")

            # 点击搜索按钮应用条件
            apply_btn = page.locator('button:has-text("検索"), input[value*="検索"], button:has-text("この条件で検索")').first
            if apply_btn.count() > 0:
                apply_btn.click()
                time.sleep(1.5)
        except Exception as e:
            log(f"    设置筛选条件失败: {e}")

        # 9. 获取搜索结果中的价格
        prices = []
        price_upper_yen = price_upper * 10000  # 转换为日元

        # 等待结果加载
        time.sleep(1)

        # 尝试多种选择器获取价格
        price_selectors = [
            '.cassetteitem_price--rent',
            '.detailbox-property-point',
            '[class*="price"]',
            '[class*="rent"]'
        ]

        for selector in price_selectors:
            elements = page.locator(selector).all()
            for elem in elements:
                try:
                    text = elem.inner_text()
                    # 匹配价格模式
                    m = re.search(r'(\d+(?:\.\d+)?)\s*万', text)
                    if m:
                        price = float(m.group(1)) * 10000
                        # 只统计价格上限以内的物件
                        if 10000 < price <= price_upper_yen:
                            prices.append(price)
                except:
                    continue

            if prices:
                break

        # 如果还没有找到，尝试从页面文本中提取
        if not prices:
            body_text = page.locator("body").inner_text()
            matches = re.findall(r'(\d+(?:\.\d+)?)\s*万円', body_text)
            for m in matches:
                price = float(m) * 10000
                # 只统计价格上限以内的物件
                if 10000 < price <= price_upper_yen:
                    prices.append(price)

        # 去重
        prices = list(set(prices))

        if prices:
            total = len(prices)
            # 使用月总费用（房租+管理费）进行比较
            cheaper_count = sum(1 for p in prices if p < total_monthly)
            rank = cheaper_count + 1
            percentile = (cheaper_count / total) * 100 if total > 0 else 0

            rank_data = {
                "total_properties": total,
                "rank": rank,
                "percentile": round(percentile, 1),
                "min_price": min(prices),
                "max_price": max(prices),
                "avg_price": sum(prices) / len(prices),
                "price_upper": price_upper,
                "walk_tier": walk_tier,
                "area_tier": area_tier,
                "no_key_money": no_key_money,  # 是否筛选礼金なし
                "total_monthly": total_monthly  # 月总费用
            }
            return rank_data, stations_searched

        return None, stations_searched

    except Exception as e:
        log(f"  分析错误: {e}")
        return None, stations_searched


def main():
    # 添加运行时间戳到日志
    log(f"\n\n{'='*60}")
    log(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("SUUMO市场排名分析 v2")
    log("分析得分>=6且无市場順位的物件")
    log("=" * 60)

    # 获取高分物件
    log("\n获取高分物件...")
    properties = get_high_score_properties(min_score=6.0)
    log(f"找到 {len(properties)} 个得分>=6的物件")

    if not properties:
        log("没有找到高分物件")
        return

    # 显示物件信息
    for p in properties:
        if p.get('management_fee', 0) > 0:
            log(f"  - {p['reins_id']}: 得分{p['score']}, ¥{p['rent']:,}+{p['management_fee']:,}=¥{p['total_monthly']:,}, {p['railway']}/{p['station']}")
        else:
            log(f"  - {p['reins_id']}: 得分{p['score']}, ¥{p['rent']:,}, {p['railway']}/{p['station']}")

    # 启动浏览器
    log("\n启动浏览器...")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="ja-JP")
    page = context.new_page()

    results = []

    try:
        for i, prop in enumerate(properties):
            log(f"\n[{i+1}/{len(properties)}] {prop['reins_id']}")
            if prop.get('management_fee', 0) > 0:
                log(f"  得分: {prop['score']}, 月费: ¥{prop['rent']:,}+¥{prop['management_fee']:,}=¥{prop['total_monthly']:,}")
            else:
                log(f"  得分: {prop['score']}, 租金: ¥{prop['rent']:,}")
            log(f"  沿线: {prop.get('railway', 'N/A')}, 车站: {prop.get('station', 'N/A')}")

            rank_data, stations_searched = analyze_market_rank(page, prop)

            # 记录搜索条件到Markdown
            log_search_condition(prop, rank_data, stations_searched)
            log(f"    ✓ 已记录搜索条件到 {CONDITIONS_FILE}")

            if rank_data:
                filters = [f"≤{rank_data.get('price_upper')}万"]
                if rank_data.get('walk_tier'):
                    filters.append(f"徒步≤{rank_data.get('walk_tier')}分")
                if rank_data.get('area_tier'):
                    filters.append(f"面积≥{rank_data.get('area_tier')}㎡")
                if rank_data.get('no_key_money'):
                    filters.append("礼金なし")
                log(f"  ✓ 市场排名: {rank_data['rank']}/{rank_data['total_properties']} ({', '.join(filters)})")
                log(f"    价格百分位: {rank_data['percentile']}% (越低越便宜)")
                log(f"    市场范围: ¥{rank_data['min_price']:,.0f} ~ ¥{rank_data['max_price']:,.0f}")
                log(f"    市场均价: ¥{rank_data['avg_price']:,.0f}")

                # 更新Notion
                if update_notion_rank(prop["page_id"], rank_data):
                    log(f"    ✓ 已更新Notion")
                else:
                    log(f"    ✗ Notion更新失败")

                prop["rank_data"] = rank_data
                results.append(prop)
            else:
                log(f"  ✗ 无法获取市场数据")

            time.sleep(1)

        # 输出总结
        log("\n" + "=" * 60)
        log("分析完成!")
        log("=" * 60)

        if results:
            log("\n高分物件市场排名汇总:")
            log("-" * 60)
            for r in sorted(results, key=lambda x: x.get("rank_data", {}).get("percentile", 100)):
                rd = r.get("rank_data", {})
                status = "便宜" if rd.get('percentile', 100) < 30 else ("中等" if rd.get('percentile', 100) < 70 else "偏贵")
                if r.get('management_fee', 0) > 0:
                    log(f"{r['reins_id']}: 得分{r['score']}, 月费¥{r['total_monthly']:,}")
                else:
                    log(f"{r['reins_id']}: 得分{r['score']}, ¥{r['rent']:,}")
                log(f"  排名: {rd.get('rank')}/{rd.get('total_properties')}, 百分位: {rd.get('percentile')}% ({status})")
                log("")

    finally:
        browser.close()
        playwright.stop()
        log("浏览器关闭")


if __name__ == "__main__":
    main()
