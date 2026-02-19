"""
训练問合せ数预测模型
从Notion表获取训练数据，训练XGBoost回归模型
"""
import os
import sys
import json
import pickle
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
TRAINING_DATABASE_ID = "30b1c197-4dad-80ec-9ea3-d63db4c0ace9"

notion_headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}


def fetch_all_pages(database_id):
    """从Notion获取所有页面"""
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {"page_size": 100}
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

    # 训练数据库字段映射
    # 賃料: 单位是万円
    # 面積: 单位是㎡
    # 徒歩: 单位是分

    number_fields = {
        "賃料": "rent_man",  # 万円
        "面積": "area_sqm",
        "徒歩": "walk_minutes",
        "問合せ数": "inquiry_count",  # 目标变量
    }

    for jp_name, en_name in number_fields.items():
        if jp_name in props:
            prop = props[jp_name]
            if prop["type"] == "number" and prop["number"] is not None:
                data[en_name] = prop["number"]

    # 转换租金为円
    if "rent_man" in data:
        data["rent"] = data["rent_man"] * 10000

    # 提取文本字段
    text_fields = {
        "所在地": "address",
        "間取り": "floor_plan",
        "線路": "railway",
        "最寄り駅": "station",
        "物件種別": "property_type",
    }

    for jp_name, en_name in text_fields.items():
        if jp_name in props:
            prop = props[jp_name]
            if prop["type"] == "rich_text" and prop["rich_text"]:
                data[en_name] = prop["rich_text"][0]["plain_text"]
            elif prop["type"] == "title" and prop["title"]:
                data[en_name] = prop["title"][0]["plain_text"]

    # 提取区
    import re
    if "address" in data:
        m = re.search(r'([\u4e00-\u9fa5]+区)', data["address"])
        if m:
            data["city"] = m.group(1)

    # 从station提取徒歩
    if "station" in data and "walk_minutes" not in data:
        m = re.search(r'/(\d+)分', data["station"])
        if m:
            data["walk_minutes"] = int(m.group(1))

    return data


def prepare_features(df, config):
    """准备模型特征"""
    # 基础特征
    df['rent'] = pd.to_numeric(df['rent'], errors='coerce')
    df['area_sqm'] = pd.to_numeric(df['area_sqm'], errors='coerce')
    df['built_year'] = pd.to_numeric(df['built_year'], errors='coerce').fillna(2015)
    df['walk_minutes'] = pd.to_numeric(df['walk_minutes'], errors='coerce')

    # 派生特征
    df['rent_per_sqm'] = df['rent'] / df['area_sqm']
    df['age'] = 2025 - df['built_year']

    # 区域热度
    high_heat_areas = config.get('high_heat_areas', [])
    mid_heat_areas = config.get('mid_heat_areas', [])

    def get_heat_level(city):
        if pd.isna(city):
            return 0
        if city in high_heat_areas:
            return 2
        elif city in mid_heat_areas:
            return 1
        return 0

    df['heat_level'] = df['city'].apply(get_heat_level)

    # 徒步距离等级
    def get_walk_level(minutes):
        if pd.isna(minutes):
            return 1
        if minutes <= 5:
            return 1
        elif minutes <= 10:
            return 2
        return 0

    df['walk_level'] = df['walk_minutes'].apply(get_walk_level)

    # 户型等级
    high_response_plans = config.get('high_response_plans', ['1DK', '2DK', '2K', '3DK', '3K'])
    mid_response_plans = config.get('mid_response_plans', ['1LDK', '3LDK', '1K', '2LDK'])

    def get_plan_type(plan):
        if pd.isna(plan):
            return 1
        if plan in high_response_plans:
            return 2
        elif plan in mid_response_plans:
            return 1
        return 0

    df['plan_type'] = df['floor_plan'].apply(get_plan_type)

    # 租金等级
    df['rent_level'] = pd.cut(df['rent'],
                              bins=[0, 60000, 80000, 100000, 150000, np.inf],
                              labels=[0, 1, 2, 3, 4]).astype(float)

    # 面积等级
    df['area_level'] = pd.cut(df['area_sqm'],
                              bins=[0, 20, 30, 50, np.inf],
                              labels=[0, 1, 2, 3]).astype(float)

    # 区域编码
    city_mapping = config.get('city_mapping', {})
    df['city_encoded'] = df['city'].map(lambda x: city_mapping.get(x, 0) if pd.notna(x) else 0)

    return df


def main():
    print("=" * 60)
    print("训练問合せ数预测模型")
    print("=" * 60)

    # 加载配置
    with open("models/model_config.json", "r") as f:
        config = json.load(f)

    # 获取训练数据
    print("\n1. 从Notion获取训练数据...")
    pages = fetch_all_pages(TRAINING_DATABASE_ID)
    print(f"   获取到 {len(pages)} 条记录")

    # 提取数据
    print("\n2. 提取物件数据...")
    records = []
    for page in pages:
        data = extract_property_data(page)
        records.append(data)

    df = pd.DataFrame(records)
    print(f"   总记录: {len(df)}")

    # 过滤有問合せ数的记录
    df_valid = df[df['inquiry_count'].notna() & (df['inquiry_count'] >= 0)].copy()
    print(f"   有問合せ数的记录: {len(df_valid)}")

    # 过滤必要字段 (不包括built_year，训练数据可能没有)
    required_fields = ['rent', 'area_sqm', 'walk_minutes']
    for field in required_fields:
        if field in df_valid.columns:
            df_valid = df_valid[df_valid[field].notna()]
    print(f"   完整数据记录: {len(df_valid)}")

    # 填充缺失的built_year
    if 'built_year' not in df_valid.columns:
        df_valid['built_year'] = 2015  # 默认值

    if len(df_valid) < 10:
        print("训练数据不足，无法训练模型")
        # 保存原始数据以便查看
        df.to_csv('data/inquiry_training_raw.csv', index=False, encoding='utf-8-sig')
        print("原始数据已保存到 data/inquiry_training_raw.csv")
        return

    # 准备特征
    print("\n3. 准备特征...")
    df_valid = prepare_features(df_valid, config)

    # 特征列 (不包括ad_count，训练数据没有)
    feature_cols = [
        'rent', 'area_sqm', 'built_year', 'walk_minutes',
        'city_encoded', 'heat_level', 'rent_per_sqm', 'age',
        'walk_level', 'plan_type', 'rent_level', 'area_level'
    ]

    X = df_valid[feature_cols].fillna(0)
    y = df_valid['inquiry_count']

    print(f"   特征矩阵: {X.shape}")
    print(f"   目标变量范围: {y.min():.1f} - {y.max():.1f}, 均值: {y.mean():.2f}")

    # 训练模型
    print("\n4. 训练XGBoost模型...")
    from sklearn.model_selection import train_test_split
    from xgboost import XGBRegressor
    from sklearn.metrics import mean_absolute_error, r2_score

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = XGBRegressor(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        random_state=42
    )
    model.fit(X_train, y_train)

    # 评估
    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    print(f"   MAE: {mae:.2f}")
    print(f"   R²: {r2:.3f}")

    # 特征重要性
    print("\n5. 特征重要性:")
    importance = dict(zip(feature_cols, model.feature_importances_))
    for feat, imp in sorted(importance.items(), key=lambda x: -x[1])[:5]:
        print(f"   {feat}: {imp:.3f}")

    # 保存模型
    print("\n6. 保存模型...")
    with open("models/inquiry_model.pkl", "wb") as f:
        pickle.dump(model, f)

    # 保存配置
    inquiry_config = {
        "model_name": "XGBoost Inquiry Prediction Model",
        "version": "1.0",
        "training_samples": len(df_valid),
        "feature_cols": feature_cols,
        "metrics": {
            "mae": round(float(mae), 2),
            "r2": round(float(r2), 3)
        },
        "feature_importance": {k: round(float(v), 4) for k, v in importance.items()},
        **{k: v for k, v in config.items() if k in ['high_heat_areas', 'mid_heat_areas',
                                                      'high_response_plans', 'mid_response_plans',
                                                      'city_mapping']}
    }

    with open("models/inquiry_model_config.json", "w", encoding='utf-8') as f:
        json.dump(inquiry_config, f, ensure_ascii=False, indent=2)

    print("   模型已保存: models/inquiry_model.pkl")
    print("   配置已保存: models/inquiry_model_config.json")

    # 保存训练数据
    df_valid.to_csv('data/inquiry_training_data.csv', index=False, encoding='utf-8-sig')
    print(f"   训练数据已保存: data/inquiry_training_data.csv")

    print("\n" + "=" * 60)
    print("训练完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
