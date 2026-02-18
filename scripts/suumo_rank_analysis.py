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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

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
        print(f"    Notion更新失败: {e}")
        return False


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
    """获取高分物件列表"""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    data = {
        "filter": {
            "property": "予測_view数",
            "number": {"greater_than_or_equal_to": min_score}
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

        # 租金单位转换（如果是万为单位）
        if rent and rent < 1000:
            rent = int(rent * 10000)

        if reins_id and rent:
            properties.append({
                "page_id": page["id"],
                "reins_id": reins_id,
                "score": score,
                "rent": int(rent),
                "area": area,
                "walk": walk,
                "railway": railway,
                "station": station,
                "floor_plan": floor_plan
            })

    return properties


def analyze_market_rank(page, prop):
    """在SUUMO上分析市场排名 - 通过手动导航"""
    rent = prop.get("rent", 0)
    area = prop.get("area", 0)
    walk = prop.get("walk", 10)
    station = prop.get("station", "")
    railway = prop.get("railway", "")

    # 计算筛选条件
    price_upper = get_price_upper_limit(rent)
    walk_tier = get_walk_tier(walk)
    area_tier = get_area_tier(area)

    # 显示筛选条件
    conditions = [f"价格≤{price_upper}万"]
    if walk_tier:
        conditions.append(f"徒步≤{walk_tier}分")
    if area_tier:
        conditions.append(f"面积≥{area_tier}㎡")
    print(f"    筛选条件: {', '.join(conditions)}")

    try:
        # 1. 访问SUUMO首页
        page.goto("https://suumo.jp/kanto/", timeout=60000)
        time.sleep(2)

        # 2. 点击賃貸物件
        rental_link = page.locator('a:has-text("賃貸物件")').first
        if rental_link.count() > 0:
            rental_link.click()
            time.sleep(2)

        # 3. 点击東京都
        tokyo_link = page.locator('a:has-text("東京都")').first
        if tokyo_link.count() > 0:
            tokyo_link.click()
            time.sleep(2)

        # 4. 点击沿線
        ensen_link = page.locator('a:has-text("沿線")').first
        if ensen_link.count() > 0:
            ensen_link.click()
            time.sleep(2)

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
            time.sleep(3)

        # 7. 如果有车站选择页面，选择车站（包括前后站，共3站）
        if station:
            # 获取前后站
            neighboring_stations = get_neighboring_stations(railway, station)
            print(f"    搜索范围: {' / '.join(neighboring_stations)}")

            selected_count = 0
            for st in neighboring_stations:
                station_checkbox = page.locator(f'label:has-text("{st}")').first
                if station_checkbox.count() > 0:
                    try:
                        station_checkbox.click()
                        selected_count += 1
                        time.sleep(0.5)
                    except:
                        pass

            if selected_count > 0:
                # 点击搜索
                search_btn = page.locator('button:has-text("検索"), input[value*="検索"]').first
                if search_btn.count() > 0:
                    search_btn.click()
                    time.sleep(3)

        # 8. 设置筛选条件（价格上限 + 徒步时间 + 面积下限）
        try:
            # 查找"条件を変更"或类似按钮
            change_btn = page.locator('a:has-text("条件を変更"), button:has-text("条件を変更"), a:has-text("絞り込み")').first
            if change_btn.count() > 0:
                change_btn.click()
                time.sleep(2)

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

            # 点击搜索按钮应用条件
            apply_btn = page.locator('button:has-text("検索"), input[value*="検索"], button:has-text("この条件で検索")').first
            if apply_btn.count() > 0:
                apply_btn.click()
                time.sleep(3)
        except Exception as e:
            print(f"    设置筛选条件失败: {e}")

        # 9. 获取搜索结果中的价格
        prices = []
        price_upper_yen = price_upper * 10000  # 转换为日元

        # 等待结果加载
        time.sleep(2)

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
            cheaper_count = sum(1 for p in prices if p < rent)
            rank = cheaper_count + 1
            percentile = (cheaper_count / total) * 100 if total > 0 else 0

            return {
                "total_properties": total,
                "rank": rank,
                "percentile": round(percentile, 1),
                "min_price": min(prices),
                "max_price": max(prices),
                "avg_price": sum(prices) / len(prices),
                "price_upper": price_upper,
                "walk_tier": walk_tier,
                "area_tier": area_tier
            }

        return None

    except Exception as e:
        print(f"  分析错误: {e}")
        return None


def main():
    print("=" * 60)
    print("SUUMO市场排名分析 v2")
    print("分析得分>=7的物件在市场中的价格排名")
    print("=" * 60)

    # 获取高分物件
    print("\n获取高分物件...")
    properties = get_high_score_properties(min_score=7.0)
    print(f"找到 {len(properties)} 个得分>=7的物件")

    if not properties:
        print("没有找到高分物件")
        return

    # 显示物件信息
    for p in properties:
        print(f"  - {p['reins_id']}: 得分{p['score']}, ¥{p['rent']:,}, {p['railway']}/{p['station']}")

    # 启动浏览器
    print("\n启动浏览器...")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="ja-JP")
    page = context.new_page()

    results = []

    try:
        for i, prop in enumerate(properties):
            print(f"\n[{i+1}/{len(properties)}] {prop['reins_id']}")
            print(f"  得分: {prop['score']}, 租金: ¥{prop['rent']:,}")
            print(f"  沿线: {prop.get('railway', 'N/A')}, 车站: {prop.get('station', 'N/A')}")

            rank_data = analyze_market_rank(page, prop)

            if rank_data:
                filters = [f"≤{rank_data.get('price_upper')}万"]
                if rank_data.get('walk_tier'):
                    filters.append(f"徒步≤{rank_data.get('walk_tier')}分")
                if rank_data.get('area_tier'):
                    filters.append(f"面积≥{rank_data.get('area_tier')}㎡")
                print(f"  ✓ 市场排名: {rank_data['rank']}/{rank_data['total_properties']} ({', '.join(filters)})")
                print(f"    价格百分位: {rank_data['percentile']}% (越低越便宜)")
                print(f"    市场范围: ¥{rank_data['min_price']:,.0f} ~ ¥{rank_data['max_price']:,.0f}")
                print(f"    市场均价: ¥{rank_data['avg_price']:,.0f}")

                # 更新Notion
                if update_notion_rank(prop["page_id"], rank_data):
                    print(f"    ✓ 已更新Notion")
                else:
                    print(f"    ✗ Notion更新失败")

                prop["rank_data"] = rank_data
                results.append(prop)
            else:
                print(f"  ✗ 无法获取市场数据")

            time.sleep(2)

        # 输出总结
        print("\n" + "=" * 60)
        print("分析完成!")
        print("=" * 60)

        if results:
            print("\n高分物件市场排名汇总:")
            print("-" * 60)
            for r in sorted(results, key=lambda x: x.get("rank_data", {}).get("percentile", 100)):
                rd = r.get("rank_data", {})
                status = "便宜" if rd.get('percentile', 100) < 30 else ("中等" if rd.get('percentile', 100) < 70 else "偏贵")
                print(f"{r['reins_id']}: 得分{r['score']}, ¥{r['rent']:,}")
                print(f"  排名: {rd.get('rank')}/{rd.get('total_properties')}, 百分位: {rd.get('percentile')}% ({status})")
                print()

    finally:
        browser.close()
        playwright.stop()
        print("浏览器关闭")


if __name__ == "__main__":
    main()
