"""分析物件100138011179在SUUMO上的搜索情况"""
import os
import sys
import time
import re
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

from playwright.sync_api import sync_playwright

# 物件信息
PROPERTY = {
    "reins_id": "100138011179",
    "rent": 441000,  # 44.1万円
    "area_sqm": 54.33,
    "walk_minutes": 3,
    "railway": "半蔵門線",
    "station": "半蔵門"
}

def main():
    print("=" * 70)
    print("物件分析报告: 100138011179")
    print("=" * 70)
    print(f"\n【物件基本信息】")
    print(f"  賃料: {PROPERTY['rent']:,}円 ({PROPERTY['rent']/10000:.1f}万円)")
    print(f"  面積: {PROPERTY['area_sqm']}㎡")
    print(f"  沿線: {PROPERTY['railway']} {PROPERTY['station']}駅 徒歩{PROPERTY['walk_minutes']}分")

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="ja-JP")
    page = context.new_page()

    rent_man = PROPERTY['rent'] / 10000

    try:
        # 搜索SUUMO
        print(f"\n【SUUMO搜索】")
        page.goto("https://suumo.jp/chintai/tokyo/", timeout=60000)
        time.sleep(2)

        # 选择沿线
        page.click('a:has-text("沿線・駅から探す")')
        time.sleep(2)

        page.click('label:has-text("半蔵門線")')
        time.sleep(1)

        # 选择车站 (半蔵門及相邻站)
        stations = ["永田町", "半蔵門", "九段下"]
        selected = 0
        for st in stations:
            try:
                page.click(f'label:has-text("{st}")')
                selected += 1
                time.sleep(0.3)
            except:
                pass
        print(f"  选中车站: {', '.join(stations)}")

        # 搜索
        page.click('a:has-text("この条件で検索する")')
        time.sleep(3)

        # 设置条件 - 租金下限40万 上限45万
        try:
            # 租金下限
            cb = page.locator('select[name="cb"]').first
            if cb.count() > 0:
                opts = cb.locator('option').all()
                for opt in opts:
                    if "40万" in opt.inner_text():
                        cb.select_option(label=opt.inner_text())
                        print(f"  租金下限: 40万円")
                        break

            # 租金上限
            ct = page.locator('select[name="ct"]').first
            if ct.count() > 0:
                opts = ct.locator('option').all()
                for opt in opts:
                    if "45万" in opt.inner_text():
                        ct.select_option(label=opt.inner_text())
                        print(f"  租金上限: 45万円")
                        break
        except:
            pass

        # 面积下限50㎡
        try:
            mb = page.locator('select[name="mb"]').first
            if mb.count() > 0:
                opts = mb.locator('option').all()
                for opt in opts:
                    if "50m" in opt.inner_text():
                        mb.select_option(label=opt.inner_text())
                        print(f"  面積下限: 50㎡")
                        break
        except:
            pass

        # 徒步上限5分
        try:
            et = page.locator('select[name="et"]').first
            if et.count() > 0:
                opts = et.locator('option').all()
                for opt in opts:
                    if "5分" in opt.inner_text():
                        et.select_option(label=opt.inner_text())
                        print(f"  徒歩上限: 5分")
                        break
        except:
            pass

        page.click('a:has-text("検索する")')
        time.sleep(3)

        # 获取搜索结果
        result_elem = page.locator('.paginate_set-hit').first
        if result_elem.count() > 0:
            result_text = result_elem.inner_text()
            print(f"  搜索结果: {result_text}")

        # 分析搜索结果中的物件
        print(f"\n【搜索结果分析】")
        print(f"  目标物件: {rent_man:.1f}万円, {PROPERTY['area_sqm']}㎡")
        print(f"  匹配条件: 租金±0.15万, 面積±2㎡")
        print()

        all_properties = []

        # 检查前3页
        for page_num in range(3):
            if page_num > 0:
                next_btn = page.locator('a:has-text("次へ")').first
                if next_btn.count() > 0:
                    next_btn.click()
                    time.sleep(2)
                else:
                    break

            casettes = page.locator('.cassetteitem').all()
            print(f"  第{page_num+1}页物件数: {len(casettes)}")

            for casette in casettes:
                try:
                    # 获取租金
                    rent_elem = casette.locator('.cassetteitem_price--rent').first
                    if rent_elem.count() == 0:
                        continue
                    rent_text = rent_elem.inner_text()

                    rent_match = re.search(r'(\d+(?:\.\d+)?)\s*万', rent_text)
                    if not rent_match:
                        continue

                    casette_rent = float(rent_match.group(1))

                    # 获取面积
                    area_elem = casette.locator('.cassetteitem_menseki').first
                    casette_area = 0
                    if area_elem.count() > 0:
                        area_text = area_elem.inner_text()
                        area_match = re.search(r'(\d+(?:\.\d+)?)', area_text)
                        if area_match:
                            casette_area = float(area_match.group(1))

                    # 获取建物名
                    title_elem = casette.locator('.cassetteitem_content-title').first
                    title = title_elem.inner_text() if title_elem.count() > 0 else "不明"

                    all_properties.append({
                        "title": title,
                        "rent": casette_rent,
                        "area": casette_area
                    })

                except Exception as e:
                    continue

        # 分析所有物件
        exact_matches = []
        near_matches = []
        similar_rent = []

        for p in all_properties:
            rent_diff = abs(p['rent'] - rent_man)
            area_diff = abs(p['area'] - PROPERTY['area_sqm'])

            if rent_diff <= 0.15 and area_diff <= 2:
                p['rent_diff'] = rent_diff
                p['area_diff'] = area_diff
                exact_matches.append(p)
            elif rent_diff <= 1 and area_diff <= 5:
                p['rent_diff'] = rent_diff
                p['area_diff'] = area_diff
                near_matches.append(p)

            if 43 <= p['rent'] <= 45:
                similar_rent.append(p)

        print(f"\n  总共检查物件数: {len(all_properties)}")

        print(f"\n【匹配结果】")
        print(f"  精确匹配 (租金±0.15万, 面積±2㎡): {len(exact_matches)}个")
        for m in exact_matches:
            print(f"    - {m['title'][:30]}: {m['rent']}万円, {m['area']}㎡")

        print(f"\n  近似匹配 (租金±1万, 面積±5㎡): {len(near_matches)}个")
        for m in near_matches[:10]:
            print(f"    - {m['title'][:30]}: {m['rent']}万円, {m['area']}㎡ (差: {m['rent_diff']:.2f}万/{m['area_diff']:.1f}㎡)")

        print(f"\n  相似租金范围(43-45万): {len(similar_rent)}个")
        for m in similar_rent[:10]:
            print(f"    - {m['title'][:35]}: {m['rent']}万円, {m['area']}㎡")

        # 结论
        print(f"\n{'='*70}")
        print("【分析结论】")
        if len(exact_matches) == 0:
            if len(near_matches) > 0:
                print("  ✗ 未找到精确匹配，但有近似物件")
                print("  可能原因:")
                print("    1. SUUMO上的租金/面积与REINS数据略有差异")
                print("    2. 同一栋楼多个房间，规格略有不同")
                print("    3. 物件可能已下架或更新")
            else:
                print("  ✗ 未找到匹配物件")
                print("  可能原因:")
                print("    1. 物件未在SUUMO上发布")
                print("    2. 搜索条件不够宽松")
        else:
            print(f"  ✓ 找到{len(exact_matches)}个精确匹配")

        print("=" * 70)

    finally:
        browser.close()
        playwright.stop()

if __name__ == "__main__":
    main()
