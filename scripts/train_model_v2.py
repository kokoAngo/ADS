"""
训练view数预测模型 V2 - 支持扩展特征
新增特征: 管理費, 敷金, 礼金, 楼層, 朝向
"""
import os
import sys
import json
import pickle
import pandas as pd
import numpy as np
from xgboost import XGBRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

# 朝向编码
DIRECTION_MAP = {
    '南': 3, '南東': 2, '南西': 2,
    '東': 1, '西': 1,
    '北東': 0, '北西': 0, '北': 0
}


def load_training_data():
    """加载训练数据 - 从CSV和Notion合并"""
    # 加载原有CSV数据
    df_csv = pd.read_csv('data/training_data_v3.csv')
    print(f"CSV数据: {len(df_csv)} 条")

    # 提取城市名
    if 'city' not in df_csv.columns and 'address_city' in df_csv.columns:
        df_csv['city'] = df_csv['address_city']

    # CSV数据没有新字段,设置默认值
    df_csv['management_fee'] = 0
    df_csv['total_rent'] = df_csv['rent']
    df_csv['deposit'] = 1.0
    df_csv['key_money'] = 1.0
    df_csv['floor'] = 5
    df_csv['direction_encoded'] = 1

    return df_csv


def prepare_features(df, config):
    """准备模型特征"""
    features = pd.DataFrame()

    # 基础特征
    features['rent'] = df['rent']
    features['area_sqm'] = df['area_sqm']
    features['built_year'] = df['built_year'].fillna(2010)
    features['walk_minutes'] = df['walk_minutes'].fillna(10)

    # 计算派生特征
    features['rent_per_sqm'] = features['rent'] / features['area_sqm'].replace(0, 1)
    features['age'] = 2025 - features['built_year']

    # 区域热度编码
    high_heat = config.get('high_heat_areas', [])
    mid_heat = config.get('mid_heat_areas', [])

    def get_heat_level(city):
        if city in high_heat:
            return 2
        elif city in mid_heat:
            return 1
        return 0

    features['heat_level'] = df['city'].fillna('').apply(get_heat_level)

    # 徒步距离等级
    def get_walk_level(w):
        if w <= 5:
            return 1
        elif w <= 10:
            return 2
        return 0

    features['walk_level'] = features['walk_minutes'].apply(get_walk_level)

    # 户型等级
    high_plans = config.get('high_response_plans', [])
    mid_plans = config.get('mid_response_plans', [])

    def get_plan_type(p):
        if pd.isna(p):
            return 0
        if p in high_plans:
            return 2
        elif p in mid_plans:
            return 1
        return 0

    features['plan_type'] = df['floor_plan'].apply(get_plan_type)

    # 租金等级
    def get_rent_level(r):
        if r < 60000:
            return 0
        elif r < 80000:
            return 1
        elif r < 100000:
            return 2
        elif r < 150000:
            return 3
        return 4

    features['rent_level'] = features['rent'].apply(get_rent_level)

    # 面积等级
    def get_area_level(a):
        if a < 20:
            return 0
        elif a < 30:
            return 1
        elif a < 50:
            return 2
        return 3

    features['area_level'] = features['area_sqm'].apply(get_area_level)

    # 城市编码
    city_mapping = config.get('city_mapping', {})
    features['city_encoded'] = df['city'].fillna('').apply(lambda x: city_mapping.get(x, 0))

    # 新增特征 (V2)
    features['total_rent'] = df.get('total_rent', df['rent'])
    features['deposit'] = df.get('deposit', 1.0).fillna(1.0)
    features['key_money'] = df.get('key_money', 1.0).fillna(1.0)
    features['floor'] = df.get('floor', 5).fillna(5)
    features['direction_encoded'] = df.get('direction_encoded', 1).fillna(1)

    # 是否零礼金/零敷金 (租客友好)
    features['zero_deposit'] = (features['deposit'] == 0).astype(int)
    features['zero_key_money'] = (features['key_money'] == 0).astype(int)

    return features


def main():
    print("=" * 60)
    print("训练view数预测模型 V2")
    print("=" * 60)

    # 加载配置
    with open('models/model_config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 加载数据
    df = load_training_data()
    print(f"总数据量: {len(df)}")

    # 准备特征
    features = prepare_features(df, config)
    target = df['estimated_response']

    # 特征列表 (V2增加了5个新特征)
    feature_cols = [
        # 原有12个特征
        'rent', 'area_sqm', 'built_year', 'walk_minutes',
        'city_encoded', 'heat_level', 'rent_per_sqm', 'age',
        'walk_level', 'plan_type', 'rent_level', 'area_level',
        # V2新增6个特征
        'total_rent', 'deposit', 'key_money', 'floor',
        'direction_encoded', 'zero_deposit', 'zero_key_money'
    ]

    X = features[feature_cols]
    y = target

    print(f"特征数量: {len(feature_cols)}")
    print(f"特征列表: {feature_cols}")

    # 分割数据
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    print(f"\n训练集: {len(X_train)}, 测试集: {len(X_test)}")

    # 训练模型
    model = XGBRegressor(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42
    )

    model.fit(X_train, y_train)

    # 评估
    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    print(f"\n模型评估:")
    print(f"  MAE: {mae:.4f}")
    print(f"  R²: {r2:.4f}")

    # 特征重要性
    importance = dict(zip(feature_cols, model.feature_importances_))
    sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)

    print(f"\n特征重要性:")
    for feat, imp in sorted_imp:
        print(f"  {feat}: {imp:.4f}")

    # 保存模型
    with open('models/xgboost_regressor_v2.pkl', 'wb') as f:
        pickle.dump(model, f)

    # 更新配置
    config_v2 = config.copy()
    config_v2['version'] = '5.0'
    config_v2['feature_cols'] = feature_cols
    config_v2['training_samples'] = len(df)
    config_v2['metrics'] = {
        'regressor': {
            'mae': round(mae, 4),
            'r2': round(r2, 4)
        }
    }
    config_v2['feature_importance'] = {k: round(float(v), 4) for k, v in importance.items()}
    config_v2['direction_map'] = DIRECTION_MAP

    with open('models/model_config_v2.json', 'w', encoding='utf-8') as f:
        json.dump(config_v2, f, ensure_ascii=False, indent=2)

    print(f"\n模型已保存:")
    print(f"  models/xgboost_regressor_v2.pkl")
    print(f"  models/model_config_v2.json")


if __name__ == "__main__":
    main()
