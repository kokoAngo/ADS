"""
批量预测view数
获取没有予測_view数的物件，预测并写入Notion
"""
import os
import sys
import json
import pickle
import requests
import re
import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

notion_headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}

# 加载view预测模型
with open("models/xgboost_regressor.pkl", "rb") as f:
    model = pickle.load(f)

with open("models/model_config.json", "r") as f:
    config = json.load(f)


def fetch_unscored_properties():
    """获取没有予測_view数的物件"""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {
            "page_size": 100,
            "filter": {
                "property": "予測_view数",
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
    }

    for jp_name, en_name in text_fields.items():
        if jp_name in props:
            prop = props[jp_name]
            if prop["type"] == "rich_text" and prop["rich_text"]:
                data[en_name] = prop["rich_text"][0]["plain_text"]

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
    """准备模型特征"""
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

    # 特征列表 (与模型训练时一致)
    features = [
        rent, area_sqm, built_year, walk_minutes,
        city_encoded, heat_level, rent_per_sqm, age,
        walk_level, plan_type, rent_level, area_level
    ]

    return features


def predict_view(data):
    """预测view数"""
    features = prepare_features(data)
    X = np.array([features])
    prediction = model.predict(X)[0]
    # 使用整数运算避免浮点精度问题
    result = int(round(max(0, prediction) * 10)) / 10
    return result


def update_notion(page_id, view_count):
    """更新Notion的予測_view数"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
        "properties": {
            "予測_view数": {"number": view_count}
        }
    }
    response = requests.patch(url, headers=notion_headers, json=payload, timeout=30)
    return response.status_code == 200


def main():
    print("=" * 60)
    print("批量预测view数")
    print("=" * 60)

    # 获取未评分物件
    print("\n1. 获取没有予測_view数的物件...")
    pages = fetch_unscored_properties()
    print(f"   获取到 {len(pages)} 个物件")

    if not pages:
        print("没有需要预测的物件")
        return

    # 预测并更新
    print("\n2. 预测view数并更新Notion...")
    success_count = 0
    high_score_count = 0

    for i, page in enumerate(pages):
        data = extract_property_data(page)
        reins_id = data.get("reins_id", "Unknown")

        # 检查必要字段
        if not data.get("rent") or not data.get("area_sqm"):
            print(f"[{i+1}/{len(pages)}] {reins_id}: 缺少必要字段，跳过")
            continue

        # 预测
        view_pred = predict_view(data)

        # 标记高分物件
        marker = "★" if view_pred >= 7 else ""
        print(f"[{i+1}/{len(pages)}] {reins_id}: view={view_pred:.1f} {marker}")

        if view_pred >= 7:
            high_score_count += 1

        # 更新Notion
        if update_notion(data["page_id"], view_pred):
            success_count += 1
        else:
            print(f"   ✗ 更新失败")

    print(f"\n{'='*60}")
    print(f"完成!")
    print(f"成功更新: {success_count}/{len(pages)} 个物件")
    print(f"高分物件 (>=7分): {high_score_count} 个")
    print("=" * 60)


if __name__ == "__main__":
    main()
