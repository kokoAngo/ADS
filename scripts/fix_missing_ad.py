"""修复缺失広告数的物件"""
import os
import sys
import time
import re
import math
import json
import requests
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()
NOTION_API_KEY = os.getenv('NOTION_API_KEY')

notion_headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}

RAILWAY_NAME_MAP = {
    '総武中央線': 'ＪＲ総武線',
    '山手線': '山手線',
    '西武新宿線': '西武新宿線',
    '都営新宿線': '都営新宿線',
}

RAILWAY_STATIONS = {
    '山手線': ['大崎', '五反田', '目黒', '恵比寿', '渋谷', '原宿', '代々木', '新宿', '新大久保', '高田馬場',
               '目白', '池袋', '大塚', '巣鴨', '駒込', '田端', '西日暮里', '日暮里', '鶯谷', '上野'],
    '西武新宿線': ['西武新宿', '高田馬場', '下落合', '中井', '新井薬師前', '沼袋', '野方'],
    '都営新宿線': ['新宿', '新宿三丁目', '曙橋', '市ヶ谷', '九段下', '神保町'],
}

def get_neighboring_stations(railway, station):
    for line_name, stations in RAILWAY_STATIONS.items():
        if railway.replace('線', '') in line_name.replace('線', ''):
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
    os.chdir(r"D:\Fango Ads")

    with open('data/missing_ad_props.json', 'r', encoding='utf-8') as f:
        properties = json.load(f)

    print(f'处理 {len(properties)} 个缺失広告数的物件')

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(viewport={'width': 1920, 'height': 1080}, locale='ja-JP')
    page = context.new_page()

    for i, prop in enumerate(properties):
        print(f'\n[{i+1}/{len(properties)}] {prop["reins_id"]}')
        print(f'  租金: {prop["rent"]}円, 面积: {prop["area_sqm"]}㎡')

        railway = prop['railway']
        station = prop['station']
        rent = prop['rent']
        area = prop['area_sqm']
        walk = prop.get('walk_minutes', 10)

        price_upper = get_price_upper_limit(rent)
        walk_tier = get_walk_tier(walk)
        area_tier = get_area_tier(area)
        neighboring_stations = get_neighboring_stations(railway, station)

        print(f'  条件: {railway} {neighboring_stations}, ≤{price_upper}万, ≤{walk_tier}分')

        # 搜索
        page.goto('https://suumo.jp/chintai/tokyo/')
        time.sleep(2)
        page.click('a:has-text("沿線・駅から探す")')
        time.sleep(2)

        suumo_railway = RAILWAY_NAME_MAP.get(railway, railway)
        page.click(f'label:has-text("{suumo_railway}")')
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
