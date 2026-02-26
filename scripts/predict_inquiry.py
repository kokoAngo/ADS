"""
预测問合せ数（反響数）并更新到Notion
获取予測_view数 >= 7的物件，预测反響数，写入予測_反響数列
"""
import os
import sys
import json
import pickle
import requests
import re
import pandas as pd
import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
TARGET_DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

notion_headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}

# 加载模型
with open("models/inquiry_model.pkl", "rb") as f:
    model = pickle.load(f)

with open("models/inquiry_model_config.json", "r") as f:
    config = json.load(f)


def fetch_high_score_properties(min_view=7):
    """获取予測_view数 >= min_view的物件"""
    url = f"https://api.notion.com/v1/databases/{TARGET_DATABASE_ID}/query"
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {
            "page_size": 100,
            "filter": {
                "property": "予測_view数",
                "number": {
                    "greater_than_or_equal_to": min_view
                }
            }
        }
        if start_cursor:
            payload["start_cursor"] = start_cursor

        response = requests.post(url, headers=notion_headers, json=payload, timeout=30)
        data = response.json()

        if "results" in data:
            all_results.extend(data["results"])
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        else:
            print(f"Error: {data}")
            break

    return all_results


def fetch_properties_without_inquiry():
    """获取没有予測_反響数的物件"""
    url = f"https://api.notion.com/v1/databases/{TARGET_DATABASE_ID}/query"
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {
            "page_size": 100,
            "filter": {
                "property": "予測_反響数",
                "number": {
                    "is_empty": True
                }
            }
        }
        if start_cursor:
            payload["start_cursor"] = start_cursor

        response = requests.post(url, headers=notion_headers, json=payload, timeout=30)
        data = response.json()

        if "results" in data:
            all_results.extend(data["results"])
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        else:
            print(f"Error: {data}")
            break

    return all_results


def extract_property_data(page):
    """从Notion页面提取物件数据"""
    props = page.get("properties", {})
    data = {"page_id": page["id"]}

    # 提取数字字段
    number_fields = {
        "賃料": "rent",
        "専有面積": "area_sqm",
        "築年": "built_year",
        "徒歩分数": "walk_minutes",
        "予測_view数": "predicted_view",
        "広告数": "ad_count",
    }

    for jp_name, en_name in number_fields.items():
        if jp_name in props:
            prop = props[jp_name]
            if prop["type"] == "number" and prop["number"] is not None:
                data[en_name] = prop["number"]

    # 提取文本字段
    text_fields = {
        "所在地": "address",
        "間取り": "floor_plan",
        "交通1_沿線名": "railway",
        "交通1_駅名": "station",
    }

    for jp_name, en_name in text_fields.items():
        if jp_name in props:
            prop = props[jp_name]
            if prop["type"] == "rich_text" and prop["rich_text"]:
                data[en_name] = prop["rich_text"][0]["plain_text"]
            elif prop["type"] == "title" and prop["title"]:
                data[en_name] = prop["title"][0]["plain_text"]

    # 提取REINS_ID
    if "REINS_ID" in props:
        prop = props["REINS_ID"]
        if prop["type"] == "title" and prop["title"]:
            data["reins_id"] = prop["title"][0]["plain_text"]

    # 提取区
    if "address" in data:
        m = re.search(r'([\u4e00-\u9fa5]+区)', data["address"])
        if m:
            data["city"] = m.group(1)

    return data


def prepare_features(data):
    """准备预测特征"""
    rent = data.get('rent', 80000)
    area_sqm = data.get('area_sqm', 25)
    built_year = data.get('built_year', 2015)
    walk_minutes = data.get('walk_minutes', 10)
    city = data.get('city', '')
    floor_plan = data.get('floor_plan', '1K')

    rent_per_sqm = rent / area_sqm if area_sqm > 0 else 0
    age = 2025 - built_year

    # 区域热度
    high_heat_areas = config.get('high_heat_areas', [])
    mid_heat_areas = config.get('mid_heat_areas', [])

    if city in high_heat_areas:
        heat_level = 2
    elif city in mid_heat_areas:
        heat_level = 1
    else:
        heat_level = 0

    # 徒步距离等级
    if walk_minutes <= 5:
        walk_level = 1
    elif walk_minutes <= 10:
        walk_level = 2
    else:
        walk_level = 0

    # 户型等级
    high_response_plans = config.get('high_response_plans', ['1DK', '2DK', '2K', '3DK', '3K'])
    mid_response_plans = config.get('mid_response_plans', ['1LDK', '3LDK', '1K', '2LDK'])

    if floor_plan in high_response_plans:
        plan_type = 2
    elif floor_plan in mid_response_plans:
        plan_type = 1
    else:
        plan_type = 0

    # 租金等级
    if rent < 60000:
        rent_level = 0
    elif rent < 80000:
        rent_level = 1
    elif rent < 100000:
        rent_level = 2
    elif rent < 150000:
        rent_level = 3
    else:
        rent_level = 4

    # 面积等级
    if area_sqm < 20:
        area_level = 0
    elif area_sqm < 30:
        area_level = 1
    elif area_sqm < 50:
        area_level = 2
    else:
        area_level = 3

    # 区域编码
    city_mapping = config.get('city_mapping', {})
    city_encoded = city_mapping.get(city, 0)

    # 特征列表
    features = [
        rent, area_sqm, built_year, walk_minutes,
        city_encoded, heat_level, rent_per_sqm, age,
        walk_level, plan_type, rent_level, area_level
    ]

    return features


def predict_inquiry(data):
    """预测反響数"""
    features = prepare_features(data)
    X = np.array([features])
    prediction = model.predict(X)[0]
    # 使用整数运算避免浮点精度问题
    result = int(round(max(1, prediction) * 10)) / 10
    return result


def update_notion_inquiry(page_id, inquiry_count, max_retries=3):
    """更新Notion的予測_反響数"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    data = {
        "properties": {
            "予測_反響数": {"number": inquiry_count}
        }
    }
    for attempt in range(max_retries):
        try:
            response = requests.patch(url, headers=notion_headers, json=data, timeout=60)
            return response.status_code == 200
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                print(f"   超时，重试 {attempt + 2}/{max_retries}...")
                import time
                time.sleep(2)
            else:
                print(f"   超时，已重试{max_retries}次")
                return False
    return False


def main():
    print("=" * 60)
    print("預測問合せ数（反響数）- JDS")
    print("=" * 60)

    # 获取没有予測_反響数的物件
    print("\n1. 获取没有予測_反響数的物件...")
    pages = fetch_properties_without_inquiry()
    print(f"   获取到 {len(pages)} 个物件")

    if not pages:
        print("没有符合条件的物件")
        return

    # 提取数据并预测
    print("\n2. 预测反響数并更新Notion...")
    success_count = 0
    results = []

    for i, page in enumerate(pages):
        data = extract_property_data(page)
        reins_id = data.get("reins_id", "Unknown")
        predicted_view = data.get("predicted_view", 0)

        # 预测
        inquiry_pred = predict_inquiry(data)

        print(f"[{i+1}/{len(pages)}] {reins_id}: view={predicted_view:.1f}, 反響予測={inquiry_pred:.1f}")

        # 更新Notion
        if update_notion_inquiry(data["page_id"], inquiry_pred):
            success_count += 1
            print(f"   ✓ 更新成功")
        else:
            print(f"   ✗ 更新失败")

        data["predicted_inquiry"] = inquiry_pred
        results.append(data)

    print(f"\n{'='*60}")
    print(f"完成! 成功更新 {success_count}/{len(pages)} 个物件")
    print("=" * 60)

    # 保存结果
    if results:
        df = pd.DataFrame(results)
        df.to_csv('data/inquiry_predictions.csv', index=False, encoding='utf-8-sig')
        print(f"结果已保存: data/inquiry_predictions.csv")


if __name__ == "__main__":
    main()
