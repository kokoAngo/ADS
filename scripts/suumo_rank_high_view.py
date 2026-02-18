"""
SUUMO市场排名分析 - 使用URL参数确保筛选条件生效
"""
import os
import sys
import time
import re
import math
import json
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

sys.stdout.reconfigure(line_buffering=True)
load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")

notion_headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# 沿线名称映射 (内部名称 -> SUUMO页面上的label文字)
RAILWAY_NAME_MAP = {
    "総武中央線": "ＪＲ総武線",
    "中央線": "ＪＲ中央線",
    "総武線": "ＪＲ総武線",
    "山手線": "山手線",
    "大江戸線": "大江戸線",
    "西武新宿線": "西武新宿線",
    "東西線": "東西線",
    "有楽町線": "有楽町線",
    "副都心線": "副都心線",
    "都営新宿線": "都営新宿線",
    "京王線": "京王線",
}

# 沿线代码映射 (SUUMO用) - 保留备用
RAILWAY_CODES = {
    "山手線": "0220",
    "大江戸線": "0235",
    "西武新宿線": "0407",
    "中央線": "0221",
    "丸ノ内線": "0225",
    "総武線": "0222",
    "総武中央線": "0222",
    "東西線": "0227",
    "有楽町線": "0229",
    "副都心線": "0242",
    "都営新宿線": "0233",
    "京王線": "0401",
}

# 车站代码映射
STATION_CODES = {
    # 山手線
    "新宿": "02200700", "新大久保": "02200800", "高田馬場": "02200900",
    "目白": "02201000", "池袋": "02201100", "大崎": "02200100",
    # 大江戸線
    "若松河田": "02350200", "牛込柳町": "02350300", "牛込神楽坂": "02350400",
    "都庁前": "02352600", "西新宿五丁目": "02352700", "中野坂上": "02352800",
    "東新宿": "02350100", "新宿西口": "02352500",
    # 西武新宿線
    "西武新宿": "04070100", "下落合": "04070300", "中井": "04070400",
    "新井薬師前": "04070500",
    # 東西線
    "中野": "02270100", "落合": "02270200", "高田馬場": "02270300",
    "早稲田": "02270400", "神楽坂": "02270500", "飯田橋": "02270600",
    # 有楽町線
    "護国寺": "02291000", "江戸川橋": "02291100", "飯田橋": "02291200",
    # 副都心線
    "雑司が谷": "02420900", "西早稲田": "02421000", "東新宿": "02421100",
    "新宿三丁目": "02421200",
    # 都営新宿線
    "新宿": "02330100", "新宿三丁目": "02330200", "曙橋": "02330300",
    "市ヶ谷": "02330400",
    # 京王線
    "新宿": "04010100", "笹塚": "04010200", "初台": "04010150",
}

# 沿线车站顺序
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
    "東西線": ["中野", "落合", "高田馬場", "早稲田", "神楽坂", "飯田橋", "九段下", "竹橋", "大手町"],
    "有楽町線": ["池袋", "東池袋", "護国寺", "江戸川橋", "飯田橋", "市ケ谷", "麹町", "永田町"],
    "副都心線": ["池袋", "雑司が谷", "西早稲田", "東新宿", "新宿三丁目", "北参道", "明治神宮前", "渋谷"],
    "都営新宿線": ["新宿", "新宿三丁目", "曙橋", "市ヶ谷", "九段下", "神保町"],
    "京王線": ["新宿", "初台", "笹塚", "代田橋", "明大前"],
    "総武中央線": ["三鷹", "吉祥寺", "西荻窪", "荻窪", "阿佐ケ谷", "高円寺", "中野", "東中野", "大久保", "新宿"],
}


def get_neighboring_stations(railway, station):
    """获取前后站（共3站范围）"""
    for line_name, stations in RAILWAY_STATIONS.items():
        if railway.replace("線", "") in line_name.replace("線", "") or line_name.replace("線", "") in railway:
            if station in stations:
                idx = stations.index(station)
                neighbors = []
                if idx > 0:
                    neighbors.append(stations[idx - 1])
                neighbors.append(station)
                if idx < len(stations) - 1:
                    neighbors.append(stations[idx + 1])
                return neighbors
    return [station]


def update_notion_rank(page_id, rank_data):
    """更新Notion中的市场排名列和広告数列"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    rank_text = f"{rank_data['rank']}/{rank_data['total_properties']}"

    data = {
        "properties": {
            "市場順位": {
                "rich_text": [{"text": {"content": rank_text}}]
            }
        }
    }

    # 如果有広告数，也更新
    if "ad_count" in rank_data and rank_data["ad_count"] is not None:
        data["properties"]["広告数"] = {"number": rank_data["ad_count"]}

    try:
        response = requests.patch(url, headers=notion_headers, json=data, timeout=30)
        return response.status_code == 200
    except Exception as e:
        print(f"    Notion更新失败: {e}")
        return False


def get_ad_count_from_detail(page, context, prop):
    """在搜索结果中找到物件并获取広告数"""
    rent = prop.get("rent", 0)
    area = prop.get("area_sqm", 0)
    rent_man = rent / 10000  # 转换为万円

    try:
        # 最多检查3页
        for page_num in range(3):
            if page_num > 0:
                # 点击下一页
                next_btn = page.locator('a:has-text("次へ")').first
                if next_btn.count() > 0:
                    next_btn.click()
                    time.sleep(2)
                else:
                    break  # 没有下一页了

            # 在搜索结果中查找匹配的物件
            casettes = page.locator('.cassetteitem').all()

            for casette in casettes:
                try:
                    # 获取租金
                    rent_elem = casette.locator('.cassetteitem_price--rent').first
                    if rent_elem.count() == 0:
                        continue
                    rent_text = rent_elem.inner_text()

                    # 检查租金是否匹配
                    rent_match = re.search(r'(\d+(?:\.\d+)?)\s*万', rent_text)
                    if not rent_match:
                        continue

                    casette_rent = float(rent_match.group(1))
                    if abs(casette_rent - rent_man) > 0.15:  # 允许0.15万的误差
                        continue

                    # 获取面积
                    area_elem = casette.locator('.cassetteitem_menseki').first
                    if area_elem.count() > 0:
                        area_text = area_elem.inner_text()
                        area_match = re.search(r'(\d+(?:\.\d+)?)', area_text)
                        if area_match:
                            casette_area = float(area_match.group(1))
                            if abs(casette_area - area) > 2:  # 允许2㎡的误差
                                continue

                    # 找到匹配的物件，获取详情链接
                    detail_links = casette.locator('a').all()
                    for link in detail_links:
                        href = link.get_attribute('href') or ''
                        if '/chintai/' in href and 'jnc_' in href:
                            full_url = 'https://suumo.jp' + href if href.startswith('/') else href

                            # 打开新标签页访问详情
                            detail_page = context.new_page()
                            detail_page.goto(full_url, timeout=30000)
                            time.sleep(2)

                            # 获取広告数
                            html = detail_page.content()

                            # 查找"他の店舗がX店あります"
                            other_count = 0
                            match = re.search(r'他の店舗が(\d+)店', html)
                            if match:
                                other_count = int(match.group(1))

                            # 総広告数 = 当前店舗(1) + 他の店舗
                            ad_count = 1 + other_count

                            detail_page.close()

                            print(f"    ✓ 找到物件详情(第{page_num+1}页)，広告数: {ad_count}")
                            return ad_count

                except Exception as e:
                    continue

        print(f"    ✗ 未在前3页搜索结果中找到匹配物件")
        return None

    except Exception as e:
        print(f"    获取広告数失败: {e}")
        return None


def get_price_upper_limit(rent):
    rent_man = rent / 10000
    upper_man = math.ceil(rent_man * 2) / 2
    return upper_man


WALK_TIERS = [1, 3, 5, 7, 10, 15, 20]

def get_walk_tier(walk_minutes):
    if not walk_minutes:
        return 10
    for tier in WALK_TIERS:
        if walk_minutes <= tier:
            return tier
    return 20


AREA_TIERS = [20, 25, 30, 40, 50, 60, 70, 80, 100]

def get_area_tier(area_sqm):
    if not area_sqm:
        return None
    result = None
    for tier in AREA_TIERS:
        if area_sqm >= tier:
            result = tier
        else:
            break
    return result


def analyze_market_rank(page, context, prop):
    """使用SUUMO搜索分析市场排名 - 正确的页面点选流程"""
    rent = prop.get("rent", 0)
    area = prop.get("area_sqm", 0)
    walk = prop.get("walk_minutes", 10)
    railway = prop.get("railway", "")
    station = prop.get("station", "")

    price_upper = get_price_upper_limit(rent)
    walk_tier = get_walk_tier(walk)
    area_tier = get_area_tier(area)

    neighboring_stations = get_neighboring_stations(railway, station)

    print(f"    沿线: {railway}")
    print(f"    三站范围: {' / '.join(neighboring_stations)}")
    print(f"    条件: 租金≤{price_upper}万, 徒步≤{walk_tier}分, 面积≥{area_tier}㎡")

    try:
        # 步骤1: 访问SUUMO东京租赁首页
        page.goto("https://suumo.jp/chintai/tokyo/", timeout=60000)
        time.sleep(2)

        # 步骤2: 点击"沿線・駅から探す"
        ensen_link = page.locator('a:has-text("沿線・駅から探す")').first
        if ensen_link.count() > 0:
            ensen_link.click()
            time.sleep(2)
        else:
            print("    未找到沿線入口")
            return None

        # 步骤3: 选择沿线 (直接点击label)
        # 使用映射表获取SUUMO上的沿线名称
        suumo_railway = RAILWAY_NAME_MAP.get(railway, railway)

        line_found = False
        # 尝试精确匹配
        line_label = page.locator(f'label:has-text("{suumo_railway}")').first
        if line_label.count() > 0:
            line_label.click()
            line_found = True
            print(f"    ✓ 选中沿线: {suumo_railway}")
            time.sleep(1)

        # 如果精确匹配失败，尝试原始名称
        if not line_found:
            line_label = page.locator(f'label:has-text("{railway}")').first
            if line_label.count() > 0:
                line_label.click()
                line_found = True
                print(f"    ✓ 选中沿线: {railway}")
                time.sleep(1)

        if not line_found:
            print(f"    未找到沿线: {railway} (也尝试了: {suumo_railway})")
            return None

        # 步骤4: 选择三站 (直接点击label)
        selected_count = 0
        for st in neighboring_stations:
            try:
                station_label = page.locator(f'label:has-text("{st}")').first
                if station_label.count() > 0:
                    station_label.click()
                    selected_count += 1
                    time.sleep(0.5)
            except Exception as e:
                print(f"    选择车站 {st} 失败: {e}")

        print(f"    ✓ 选中车站: {selected_count}个")

        if selected_count == 0:
            print("    未能选择任何车站")
            return None

        # 步骤5: 点击"この条件で検索する"
        time.sleep(1)
        search_link = page.locator('a:has-text("この条件で検索する")').first
        if search_link.count() > 0:
            search_link.click()
            time.sleep(3)
        else:
            print("    未找到搜索按钮")
            return None

        # 步骤6: 设置筛选条件 - 租金上限 (name=ct)
        try:
            ct_select = page.locator('select[name="ct"]').first
            if ct_select.count() > 0:
                # 构建价格文本
                if price_upper == int(price_upper):
                    price_text = f"{int(price_upper)}万円"
                else:
                    price_text = f"{price_upper}万円"

                options = ct_select.locator('option').all()
                for opt in options:
                    opt_text = opt.inner_text()
                    if price_text in opt_text:
                        ct_select.select_option(label=opt_text)
                        print(f"    ✓ 设置租金上限: {opt_text}")
                        break
        except Exception as e:
            print(f"    设置租金上限失败: {e}")

        # 步骤7: 设置筛选条件 - 徒步时间 (name=et)
        try:
            et_select = page.locator('select[name="et"]').first
            if et_select.count() > 0:
                walk_text = f"{walk_tier}分以内"
                options = et_select.locator('option').all()
                for opt in options:
                    opt_text = opt.inner_text()
                    if f"{walk_tier}分" in opt_text:
                        et_select.select_option(label=opt_text)
                        print(f"    ✓ 设置徒步上限: {opt_text}")
                        break
        except Exception as e:
            print(f"    设置徒步上限失败: {e}")

        # 步骤8: 设置筛选条件 - 面积下限 (name=mb)
        if area_tier:
            try:
                mb_select = page.locator('select[name="mb"]').first
                if mb_select.count() > 0:
                    area_text = f"{area_tier}m"
                    options = mb_select.locator('option').all()
                    for opt in options:
                        opt_text = opt.inner_text()
                        if area_text in opt_text:
                            mb_select.select_option(label=opt_text)
                            print(f"    ✓ 设置面积下限: {opt_text}")
                            break
            except Exception as e:
                print(f"    设置面积下限失败: {e}")

        # 步骤9: 点击"検索する"按钮应用筛选
        time.sleep(1)
        search_btn = page.locator('a:has-text("検索する")').first
        if search_btn.count() > 0:
            search_btn.click()
            time.sleep(3)

        # 步骤10: 获取搜索结果
        current_url = page.url
        print(f"    最终URL: {current_url[:80]}...")

        # 获取结果数
        result_elem = page.locator('.paginate_set-hit').first
        if result_elem.count() > 0:
            result_text = result_elem.inner_text()
            print(f"    搜索结果: {result_text}")

        # 步骤11: 提取价格
        prices = []
        price_upper_yen = price_upper * 10000

        # 获取所有价格
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

        prices = list(set(prices))
        print(f"    找到价格数: {len(prices)}")

        if prices:
            total = len(prices)
            cheaper_count = sum(1 for p in prices if p < rent)
            rank = cheaper_count + 1

            # 步骤12: 获取広告数（在搜索结果中找到物件并查看详情）
            ad_count = get_ad_count_from_detail(page, context, prop)

            return {
                "total_properties": total,
                "rank": rank,
                "min_price": min(prices),
                "max_price": max(prices),
                "avg_price": sum(prices) / len(prices),
                "ad_count": ad_count
            }

        return None

    except Exception as e:
        print(f"  分析错误: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    print("=" * 60)
    print("SUUMO市场排名分析 - 修复版")
    print("=" * 60)

    # 读取高分物件 (可通过命令行参数指定测试数量)
    with open('data/high_view_properties_6plus.json', 'r', encoding='utf-8') as f:
        properties = json.load(f)

    # 如果有命令行参数，只处理指定数量
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
            properties = properties[:limit]
            print(f"测试模式: 只处理前 {limit} 个物件")
        except:
            pass

    print(f"\n找到 {len(properties)} 个高分物件")

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
            print(f"  view得分: {prop['score']}, 租金: ¥{prop['rent']:,}")

            rank_data = analyze_market_rank(page, context, prop)

            if rank_data:
                ad_info = f", 広告数: {rank_data.get('ad_count', '?')}" if rank_data.get('ad_count') else ""
                print(f"  ✓ 市场排名: {rank_data['rank']}/{rank_data['total_properties']}{ad_info}")

                if update_notion_rank(prop["page_id"], rank_data):
                    print(f"    ✓ 已更新Notion")
                else:
                    print(f"    ✗ Notion更新失败")

                prop["rank_data"] = rank_data
                results.append(prop)
            else:
                print(f"  ✗ 无法获取市场数据")

            time.sleep(2)

        print("\n" + "=" * 60)
        print("分析完成!")
        print("=" * 60)

        if results:
            print(f"\n成功分析: {len(results)}/{len(properties)} 个物件")
            for r in results:
                rd = r.get("rank_data", {})
                ad_str = f", 広告:{rd.get('ad_count')}" if rd.get('ad_count') else ""
                print(f"  {r['reins_id']}: {rd.get('rank')}/{rd.get('total_properties')}{ad_str}")

    finally:
        browser.close()
        playwright.stop()
        print("\n浏览器关闭")


if __name__ == "__main__":
    main()
