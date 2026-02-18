"""
SUUMO市场排名分析 - 从CSV读取高分物件
"""
import os
import sys
import time
import re
import math
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(line_buffering=True)


# 沿线车站顺序映射
RAILWAY_STATIONS = {
    "山手線": ["大崎", "五反田", "目黒", "恵比寿", "渋谷", "原宿", "代々木", "新宿", "新大久保", "高田馬場",
               "目白", "池袋", "大塚", "巣鴨", "駒込", "田端", "西日暮里", "日暮里", "鶯谷", "上野",
               "御徒町", "秋葉原", "神田", "東京", "有楽町", "新橋", "浜松町", "田町", "品川"],
}


def get_price_upper_limit(rent):
    """计算价格上限（向上取整到0.5万）"""
    rent_man = rent / 10000
    upper_man = math.ceil(rent_man * 2) / 2
    return upper_man


WALK_TIERS = [1, 3, 5, 7, 10, 15, 20]


def get_walk_tier(walk_minutes):
    """获取徒步时间档位"""
    if not walk_minutes:
        return 10
    for tier in WALK_TIERS:
        if walk_minutes <= tier:
            return tier
    return 20


AREA_TIERS = [20, 25, 30, 40, 50, 60, 70, 80, 100]


def get_area_tier(area_sqm):
    """获取面积档位（下限）"""
    if not area_sqm:
        return None
    result = None
    for tier in AREA_TIERS:
        if area_sqm >= tier:
            result = tier
        else:
            break
    return result


def analyze_market_rank_simple(page, prop):
    """使用SUUMO搜索分析市场排名 - 简化版，按区搜索"""
    rent = prop.get("rent", 0)
    area = prop.get("area_sqm", 0)
    walk = prop.get("walk_minutes", 10)
    city = prop.get("city", "")

    price_upper = get_price_upper_limit(rent)
    walk_tier = get_walk_tier(walk)
    area_tier = get_area_tier(area)

    print(f"    筛选: 价格≤{price_upper}万, 徒步≤{walk_tier}分, 面积≥{area_tier}㎡, 区域={city}")

    try:
        # 构建SUUMO搜索URL
        # 东京23区代码映射
        area_codes = {
            "千代田区": "sc13101", "中央区": "sc13102", "港区": "sc13103",
            "新宿区": "sc13104", "文京区": "sc13105", "台東区": "sc13106",
            "墨田区": "sc13107", "江東区": "sc13108", "品川区": "sc13109",
            "目黒区": "sc13110", "大田区": "sc13111", "世田谷区": "sc13112",
            "渋谷区": "sc13113", "中野区": "sc13114", "杉並区": "sc13115",
            "豊島区": "sc13116", "北区": "sc13117", "荒川区": "sc13118",
            "板橋区": "sc13119", "練馬区": "sc13120", "足立区": "sc13121",
            "葛飾区": "sc13122", "江戸川区": "sc13123"
        }

        sc = area_codes.get(city, "")
        if not sc:
            print(f"    未找到区域代码: {city}")
            return None

        # 构建URL参数
        # cb: 賃料下限(万), ct: 賃料上限(万)
        # mb: 面積下限(㎡), mt: 面積上限(㎡)
        # et: 徒歩分数
        ct = price_upper  # 上限价格（万）
        et = walk_tier  # 徒步上限
        mb = area_tier if area_tier else ""  # 面积下限

        url = f"https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&{sc}=1&ct={ct}&et={et}"
        if mb:
            url += f"&mb={mb}"
        url += "&cn=9999999&tc=0401303&shkr1=03&shkr2=03&shkr3=03&shkr4=03"

        print(f"    搜索URL: {url[:80]}...")
        page.goto(url, timeout=60000)
        time.sleep(3)

        # 获取搜索结果中的价格
        prices = []
        price_upper_yen = price_upper * 10000

        # 尝试获取价格
        price_elements = page.locator('.cassetteitem_price--rent').all()
        for elem in price_elements:
            try:
                text = elem.inner_text()
                m = re.search(r'(\d+(?:\.\d+)?)\s*万', text)
                if m:
                    price = float(m.group(1)) * 10000
                    if 10000 < price <= price_upper_yen:
                        prices.append(price)
            except:
                continue

        # 如果上面的选择器没找到，尝试从页面文本提取
        if not prices:
            body_text = page.locator("body").inner_text()
            matches = re.findall(r'(\d+(?:\.\d+)?)\s*万円', body_text)
            for m in matches:
                price = float(m) * 10000
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
        import traceback
        traceback.print_exc()
        return None


def main():
    print("=" * 60)
    print("SUUMO市场排名分析 - 从CSV读取高分物件")
    print("=" * 60)

    # 读取预测结果
    df = pd.read_csv('data/notion_predictions_v2.csv')

    # 筛选高分物件（得分>=7）
    high_score = df[df['predicted_response'] >= 7.0].copy()
    print(f"\n找到 {len(high_score)} 个得分>=7的物件:")

    properties = []
    for _, row in high_score.iterrows():
        prop = {
            'bukken_number': str(row['bukken_number']),
            'score': row['predicted_response'],
            'rent': int(row['rent']),
            'area_sqm': row['area_sqm'],
            'walk_minutes': row.get('walk_minutes', 10),
            'city': row.get('city', ''),
            'floor_plan': row.get('floor_plan', '')
        }
        properties.append(prop)
        print(f"  - {prop['bukken_number']}: 得分{prop['score']:.1f}, ¥{prop['rent']:,}, {prop['city']}")

    if not properties:
        print("没有找到高分物件")
        return

    # 启动浏览器
    print("\n启动浏览器进行SUUMO分析...")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="ja-JP")
    page = context.new_page()

    results = []

    try:
        for i, prop in enumerate(properties):
            print(f"\n[{i+1}/{len(properties)}] {prop['bukken_number']}")
            print(f"  得分: {prop['score']:.1f}, 租金: ¥{prop['rent']:,}, 区域: {prop['city']}")

            rank_data = analyze_market_rank_simple(page, prop)

            if rank_data:
                print(f"  市场排名: {rank_data['rank']}/{rank_data['total_properties']}")
                print(f"    价格百分位: {rank_data['percentile']}% (越低越便宜)")
                print(f"    市场范围: ¥{rank_data['min_price']:,.0f} ~ ¥{rank_data['max_price']:,.0f}")
                print(f"    市场均价: ¥{rank_data['avg_price']:,.0f}")

                # 评估
                if rank_data['percentile'] < 30:
                    status = "很便宜"
                elif rank_data['percentile'] < 50:
                    status = "较便宜"
                elif rank_data['percentile'] < 70:
                    status = "中等"
                else:
                    status = "偏贵"
                print(f"    评估: {status}")

                prop["rank_data"] = rank_data
                results.append(prop)
            else:
                print(f"  无法获取市场数据")

            time.sleep(2)

        # 输出总结
        print("\n" + "=" * 60)
        print("分析完成! 高分物件市场排名汇总:")
        print("=" * 60)

        if results:
            for r in sorted(results, key=lambda x: x.get("rank_data", {}).get("percentile", 100)):
                rd = r.get("rank_data", {})
                if rd.get('percentile', 100) < 30:
                    status = "很便宜"
                elif rd.get('percentile', 100) < 50:
                    status = "较便宜"
                elif rd.get('percentile', 100) < 70:
                    status = "中等"
                else:
                    status = "偏贵"

                print(f"\n{r['bukken_number']} ({r['city']})")
                print(f"  得分: {r['score']:.1f}, 租金: ¥{r['rent']:,}")
                print(f"  排名: {rd.get('rank')}/{rd.get('total_properties')}, 百分位: {rd.get('percentile')}% ({status})")
                print(f"  市场均价: ¥{rd.get('avg_price', 0):,.0f}")

    finally:
        browser.close()
        playwright.stop()
        print("\n浏览器关闭")


if __name__ == "__main__":
    main()
