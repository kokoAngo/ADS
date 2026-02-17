"""
预测 新着物件DB_新宿区 表中的物件
将预测结果写入 予測_view数 列
"""
import os
import sys
import time
import json
import pickle
import requests
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import numpy as np

sys.stdout.reconfigure(line_buffering=True)
load_dotenv()

# REINS配置
REINS_URL = "https://system.reins.jp/login/main/KG/GKG001200"
REINS_USERNAME = os.getenv("REINS_USERNAME")
REINS_PASSWORD = os.getenv("REINS_PASSWORD")

# Notion配置
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

# 加载模型
with open("models/xgboost_regressor.pkl", "rb") as f:
    model = pickle.load(f)

with open("models/model_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}


def prepare_features(data):
    rent = data.get("rent", 80000)
    area_sqm = data.get("area_sqm", 25)
    built_year = data.get("built_year", 2016)
    walk_minutes = data.get("walk_minutes", 10)
    city = data.get("city", "新宿区")
    floor_plan = data.get("floor_plan", "1K")

    rent_per_sqm = rent / area_sqm if area_sqm > 0 else 0
    age = 2026 - built_year

    high_heat = config.get("high_heat_areas", [])
    mid_heat = config.get("mid_heat_areas", [])
    heat_level = 2 if city in high_heat else (1 if city in mid_heat else 0)

    walk_level = 1 if walk_minutes <= 5 else (2 if walk_minutes <= 10 else 0)

    high_plans = config.get("high_response_plans", [])
    mid_plans = config.get("mid_response_plans", [])
    plan_type = 2 if floor_plan in high_plans else (1 if floor_plan in mid_plans else 0)

    rent_level = 0 if rent < 50000 else (1 if rent < 80000 else (2 if rent < 120000 else 3))
    area_level = 0 if area_sqm < 20 else (1 if area_sqm < 30 else (2 if area_sqm < 50 else 3))

    city_map = config.get("city_mapping", {})
    city_encoded = city_map.get(city, 0)

    return [rent, area_sqm, built_year, walk_minutes, city_encoded, heat_level,
            rent_per_sqm, age, walk_level, plan_type, rent_level, area_level]


def update_notion(page_id, data, score):
    """更新Notion - 只写入予測_view数列，不修改其他列"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    props = {}

    # 只写入予測_view数，不修改其他已有数据
    props["予測_view数"] = {"number": score}

    response = requests.patch(url, headers=headers, json={"properties": props})
    result = response.json()
    if "id" not in result:
        print(f"  API错误: {result.get('message', result)}")
    return "id" in result


def main():
    # 读取物件列表
    print("读取物件列表...")
    bukken_list = []
    with open('data/unscored_pages_db.txt', 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '|' in line:
                parts = line.split('|')
                bukken_number = parts[0]
                page_id = parts[1]
                if bukken_number:
                    bukken_list.append((bukken_number, page_id))

    print(f"共 {len(bukken_list)} 个物件")

    # 启动浏览器
    print("启动浏览器...")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="ja-JP")
    page = context.new_page()

    try:
        # 登录REINS
        print("登录REINS...")
        page.goto(REINS_URL, wait_until="networkidle")
        time.sleep(2)

        username_input = page.query_selector('input[type="text"]')
        password_input = page.query_selector('input[type="password"]')

        if username_input and password_input:
            username_input.fill(REINS_USERNAME)
            time.sleep(0.3)
            password_input.fill(REINS_PASSWORD)
            time.sleep(0.3)

            for label in page.query_selector_all("label"):
                if "遵守" in label.inner_text():
                    label.click()
                    break

            login_btn = page.query_selector('button:has-text("ログイン")')
            if login_btn:
                login_btn.click()
                time.sleep(3)

        print("登录成功")

        # 进入物件番号検索
        bukken_link = page.locator("text=物件番号検索").first
        if bukken_link.count() > 0:
            bukken_link.click()
            time.sleep(2)
        print("进入物件番号検索")

        # 处理每个物件
        success_count = 0
        for i, (bukken_no, page_id) in enumerate(bukken_list):
            print(f"\n[{i+1}/{len(bukken_list)}] {bukken_no}")

            try:
                # 清空并输入物件番号
                inputs = page.query_selector_all('input[type="text"]')
                for inp in inputs:
                    inp.fill("")
                if inputs:
                    inputs[0].fill(str(bukken_no))
                    time.sleep(0.3)

                # 点击搜索
                search_btn = page.locator('button:has-text("検索")').first
                if search_btn.count() > 0:
                    search_btn.click()
                    time.sleep(2)

                # 提取数据
                text = page.locator("body").inner_text()
                data = {"city": "新宿区"}

                # 賃料
                m = re.search(r'賃料[：:\s]*(\d+(?:,\d+)?)\s*円', text)
                if m:
                    data["rent"] = int(m.group(1).replace(",", ""))
                else:
                    m = re.search(r'(\d+(?:\.\d+)?)\s*万円', text)
                    if m:
                        data["rent"] = int(float(m.group(1)) * 10000)

                # 面積
                m = re.search(r'専有面積[：:\s]*([\d.]+)\s*[㎡m]', text)
                if not m:
                    m = re.search(r'([\d.]+)\s*㎡', text)
                if m:
                    data["area_sqm"] = float(m.group(1))

                # 築年
                m = re.search(r'築年月[：:\s]*(\d{4})', text)
                if not m:
                    m = re.search(r'(\d{4})年', text)
                if m:
                    data["built_year"] = int(m.group(1))

                # 徒歩
                m = re.search(r'徒歩\s*(\d+)\s*分', text)
                if m:
                    data["walk_minutes"] = int(m.group(1))

                # 間取り
                m = re.search(r'([1-9][SLKDR]+)', text)
                if m:
                    data["floor_plan"] = m.group(1)

                # 所在地
                m = re.search(r'東京都[^区市]*([ぁ-んァ-ン一-龥]+[区市])', text)
                if m:
                    data["city"] = m.group(1)

                if data.get("rent") and data.get("area_sqm"):
                    # 预测
                    features = prepare_features(data)
                    score = float(model.predict(np.array([features]))[0])
                    score = round(max(0, score), 1)

                    # 更新Notion
                    if update_notion(page_id, data, score):
                        print(f'  ¥{data["rent"]:,}, {data["area_sqm"]}㎡, {data.get("city","")} → 得分: {score}')
                        success_count += 1
                    else:
                        print(f"  Notion更新失败")
                else:
                    print(f"  未能获取物件数据")

                # 返回搜索页
                page.go_back()
                time.sleep(1)

            except Exception as e:
                print(f"  错误: {e}")

            time.sleep(0.5)

        print(f"\n完成! 成功更新 {success_count}/{len(bukken_list)} 个物件")

    finally:
        browser.close()
        playwright.stop()
        print("浏览器关闭")


if __name__ == "__main__":
    main()
