"""
計算広告数（有多少中介推荐该物件）
直接从Notion获取没有広告数的物件
"""
import os
import sys
import time
import re
import math
import requests
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

# 设置工作目录
os.chdir(r"D:\Fango Ads")
load_dotenv()

NOTION_API_KEY = os.getenv('NOTION_API_KEY', 'ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q')
DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

notion_headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}

# 沿线车站顺序映射
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

def get_neighboring_stations(railway, station):
    """获取前后站（共3站范围）"""
    stations = RAILWAY_STATIONS.get(railway, [])
    if not stations or station not in stations:
        return [station]

    idx = stations.index(station)
    neighbors = []
    if idx > 0:
        neighbors.append(stations[idx - 1])
    neighbors.append(station)
    if idx < len(stations) - 1:
        neighbors.append(stations[idx + 1])
    return neighbors


def get_properties_without_ad_count(min_score=6.0):
    """从Notion获取没有広告数的物件"""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    data = {
        "filter": {
            "and": [
                {"property": "予測_view数", "number": {"greater_than_or_equal_to": min_score}},
                {"property": "広告数", "number": {"is_empty": True}}
            ]
        },
        "page_size": 50
    }

    response = requests.post(url, headers=notion_headers, json=data, timeout=30)
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
        rent = get_number("賃料") or get_number("価格_賃料(万)")
        area = get_number("専有面積") or get_number("面積・不動産ID_使用部分面積(m2)")
        walk = get_number("徒歩分数") or get_number("交通1_駅より徒歩(分)")
        railway = get_text("交通1_沿線名")
        station = get_text("交通1_駅名")
        score = get_number("予測_view数")

        if rent and rent < 1000:
            rent = int(rent * 10000)

        if reins_id and rent:
            properties.append({
                "page_id": page["id"],
                "reins_id": reins_id,
                "rent": int(rent),
                "area_sqm": area or 20,
                "walk_minutes": walk or 10,
                "railway": railway,
                "station": station,
                "score": score
            })

    return properties

def get_price_upper_limit(rent):
    rent_man = rent / 10000
    return math.ceil(rent_man * 2) / 2

WALK_TIERS = [1, 3, 5, 7, 10, 15, 20]
def get_walk_tier(walk_minutes):
    for tier in WALK_TIERS:
        if walk_minutes <= tier:
            return tier
    return 20

AREA_TIERS = [20, 25, 30, 40, 50, 60, 70, 80, 100]
def get_area_tier(area_sqm):
    result = None
    for tier in AREA_TIERS:
        if area_sqm >= tier:
            result = tier
        else:
            break
    return result

def main():
    print("=" * 60)
    print("広告数計算 - 查找有多少中介推荐该物件")
    print("=" * 60)

    print("\n获取没有広告数的物件...")
    properties = get_properties_without_ad_count(min_score=6.0)
    print(f'找到 {len(properties)} 个缺失広告数的物件')

    if not properties:
        print("没有需要处理的物件")
        return

    for p in properties:
        print(f'  - {p["reins_id"]}: 得分{p.get("score", "N/A")}, ¥{p["rent"]:,}, {p["railway"]}/{p["station"]}')

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(viewport={'width': 1920, 'height': 1080}, locale='ja-JP')
    page = context.new_page()

    print("\n启动浏览器...")

    for i, prop in enumerate(properties):
        print(f'\n[{i+1}/{len(properties)}] {prop["reins_id"]}')
        print(f'  得分: {prop.get("score", "N/A")}, 租金: ¥{prop["rent"]:,}, 面积: {prop["area_sqm"]}㎡')

        railway = prop['railway']
        station = prop['station']
        rent = prop['rent']
        area = prop['area_sqm']
        walk = prop.get('walk_minutes', 10)

        price_upper = get_price_upper_limit(rent)
        walk_tier = get_walk_tier(walk)
        area_tier = get_area_tier(area)
        neighboring_stations = get_neighboring_stations(railway, station)

        print(f'  沿线: {railway}, 车站: {station}')
        print(f'  搜索范围: {" / ".join(neighboring_stations)}')
        print(f'  筛选: ≤{price_upper}万, 徒步≤{walk_tier}分' + (f', 面积≥{area_tier}㎡' if area_tier else ''))

        # 搜索
        page.goto('https://suumo.jp/chintai/tokyo/')
        time.sleep(2)
        page.click('a:has-text("沿線・駅から探す")')
        time.sleep(2)

        # 点击沿线
        railway_short = railway.replace("線", "").replace("東京メトロ", "").strip()
        line_checkbox = page.locator(f'label:has-text("{railway}")').first
        if line_checkbox.count() == 0:
            line_checkbox = page.locator(f'label:has-text("{railway_short}")').first
        if line_checkbox.count() > 0:
            line_checkbox.click()
        time.sleep(1)

        for st in neighboring_stations:
            try:
                page.click(f'label:has-text("{st}")')
                time.sleep(0.3)
            except:
                pass

        page.click('a:has-text("この条件で検索する")')
        time.sleep(3)

        # 设置条件
        if price_upper == int(price_upper):
            price_text = f'{int(price_upper)}万円'
        else:
            price_text = f'{price_upper}万円'

        ct = page.locator('select[name="ct"]').first
        if ct.count() > 0:
            opts = ct.locator('option').all()
            for opt in opts:
                if price_text in opt.inner_text():
                    ct.select_option(label=opt.inner_text())
                    break

        et = page.locator('select[name="et"]').first
        if et.count() > 0:
            opts = et.locator('option').all()
            for opt in opts:
                if f'{walk_tier}分' in opt.inner_text():
                    et.select_option(label=opt.inner_text())
                    break

        if area_tier:
            mb = page.locator('select[name="mb"]').first
            if mb.count() > 0:
                opts = mb.locator('option').all()
                for opt in opts:
                    if f'{area_tier}m' in opt.inner_text():
                        mb.select_option(label=opt.inner_text())
                        break

        page.click('a:has-text("検索する")')
        time.sleep(3)

        # 搜索広告数 - 翻页查找
        rent_man = rent / 10000
        ad_count = None

        for page_num in range(5):  # 最多检查5页
            if page_num > 0:
                next_btn = page.locator('a:has-text("次へ")').first
                if next_btn.count() > 0:
                    next_btn.click()
                    time.sleep(2)
                else:
                    break

            casettes = page.locator('.cassetteitem').all()

            for casette in casettes:
                try:
                    rent_elem = casette.locator('.cassetteitem_price--rent').first
                    if rent_elem.count() == 0:
                        continue
                    rent_text = rent_elem.inner_text()

                    rent_match = re.search(r'(\d+(?:\.\d+)?)\s*万', rent_text)
                    if not rent_match:
                        continue

                    casette_rent = float(rent_match.group(1))
                    if abs(casette_rent - rent_man) > 0.2:
                        continue

                    area_elem = casette.locator('.cassetteitem_menseki').first
                    if area_elem.count() > 0:
                        area_text = area_elem.inner_text()
                        area_match = re.search(r'(\d+(?:\.\d+)?)', area_text)
                        if area_match:
                            casette_area = float(area_match.group(1))
                            if abs(casette_area - area) > 2:
                                continue

                    # 找到匹配，获取详情
                    detail_links = casette.locator('a').all()
                    for link in detail_links:
                        href = link.get_attribute('href') or ''
                        if '/chintai/' in href and 'jnc_' in href:
                            full_url = 'https://suumo.jp' + href if href.startswith('/') else href

                            detail_page = context.new_page()
                            detail_page.goto(full_url, timeout=30000)
                            time.sleep(2)

                            html = detail_page.content()
                            other_count = 0
                            match = re.search(r'他の店舗が(\d+)店', html)
                            if match:
                                other_count = int(match.group(1))

                            ad_count = 1 + other_count
                            detail_page.close()

                            print(f'  ✓ 找到(第{page_num+1}页)，広告数: {ad_count}')
                            break

                    if ad_count:
                        break
                except:
                    continue

            if ad_count:
                break

        if ad_count:
            # 更新Notion
            url = f'https://api.notion.com/v1/pages/{prop["page_id"]}'
            data = {'properties': {'広告数': {'number': ad_count}}}
            response = requests.patch(url, headers=notion_headers, json=data, timeout=30)
            if response.status_code == 200:
                print(f'  ✓ 已更新Notion')
            else:
                print(f'  ✗ 更新失败')
        else:
            print(f'  ✗ 未找到匹配物件(检查了{page_num+1}页)')

        time.sleep(2)

    browser.close()
    playwright.stop()
    print('\n完成')

if __name__ == "__main__":
    main()
