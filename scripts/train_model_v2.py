"""
训练view数预测模型 V2 - 使用新爬取的详细数据
特征: 賃料, 管理費, 敷金, 礼金, 面積, 築年, 徒歩, 区域等
"""
import os
import sys
import json
import pickle
import sqlite3
import pandas as pd
import numpy as np
from xgboost import XGBRegressor
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")


def load_training_data():
    """从数据库加载训练数据"""
    conn = sqlite3.connect('data/properties.db')

    df = pd.read_sql('''
        SELECT
            rent, management_fee, deposit, key_money,
            area_sqm, floor_plan, property_type,
            built_year, railway_line, station, walk_minutes,
            area_name, estimated_response
        FROM properties
        WHERE estimated_response IS NOT NULL
          AND rent IS NOT NULL
          AND area_sqm IS NOT NULL
    ''', conn)

    conn.close()

    print(f"从数据库加载: {len(df)} 条")

    # 添加city字段 (兼容旧配置)
    df['city'] = df['area_name']

    # 确保数值字段是数值类型
    df['deposit'] = pd.to_numeric(df['deposit'], errors='coerce').fillna(1.0)
    df['key_money'] = pd.to_numeric(df['key_money'], errors='coerce').fillna(1.0)
    df['management_fee'] = pd.to_numeric(df['management_fee'], errors='coerce').fillna(0)

    # 计算总租金 (賃料 + 管理費)
    df['total_rent'] = df['rent'] + df['management_fee']

    return df


def prepare_features(df, config):
    """准备模型特征"""
    features = pd.DataFrame()

    # 基础特征
    features['rent'] = df['rent']
    features['area_sqm'] = df['area_sqm']
    features['built_year'] = df['built_year'].fillna(2000)
    features['walk_minutes'] = df['walk_minutes'].fillna(10)

    # 管理費
    features['management_fee'] = df['management_fee'].fillna(0)

    # 总租金
    features['total_rent'] = df['total_rent']

    # 计算派生特征
    features['rent_per_sqm'] = features['rent'] / features['area_sqm'].replace(0, 1)
    features['total_rent_per_sqm'] = features['total_rent'] / features['area_sqm'].replace(0, 1)
    features['age'] = 2026 - features['built_year']

    # 敷金/礼金处理 (可能是月数或金额)
    # 如果值>100，认为是金额(円)，转换为月数
    def normalize_deposit(val, rent):
        if pd.isna(val):
            return 1.0
        if val > 100:  # 金额格式，转换为月数
            return val / rent if rent > 0 else 1.0
        return val  # 月数格式

    features['deposit'] = df.apply(lambda r: normalize_deposit(r['deposit'], r['rent']), axis=1)
    features['key_money'] = df.apply(lambda r: normalize_deposit(r['key_money'], r['rent']), axis=1)

    # 是否零礼金/零敷金 (租客友好)
    features['zero_deposit'] = (features['deposit'] == 0).astype(int)
    features['zero_key_money'] = (features['key_money'] == 0).astype(int)

    # 初期費用总额 (月数)
    features['initial_cost'] = features['deposit'] + features['key_money']

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
        if pd.isna(w):
            return 1
        if w <= 5:
            return 2
        elif w <= 10:
            return 1
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

    # 建物类型编码
    def get_building_type(t):
        if pd.isna(t):
            return 0
        if 'マンション' in str(t):
            return 2
        elif 'アパート' in str(t):
            return 1
        return 0

    features['building_type'] = df['property_type'].apply(get_building_type)

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

    return features


def main():
    print("=" * 60)
    print("训练推定反響数预测模型 V2")
    print("=" * 60)

    # 加载配置
    with open('models/model_config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 加载数据
    df = load_training_data()
    print(f"总数据量: {len(df)}")

    # 数据统计
    print(f"\n目标变量分布:")
    print(f"  min: {df['estimated_response'].min()}")
    print(f"  max: {df['estimated_response'].max()}")
    print(f"  mean: {df['estimated_response'].mean():.2f}")

    # 准备特征
    features = prepare_features(df, config)
    target = df['estimated_response']

    # 特征列表 (V2: 19个特征)
    feature_cols = [
        # 基础特征
        'rent', 'area_sqm', 'built_year', 'walk_minutes', 'management_fee',
        # 费用相关
        'total_rent', 'deposit', 'key_money', 'initial_cost',
        'zero_deposit', 'zero_key_money',
        # 派生特征
        'rent_per_sqm', 'total_rent_per_sqm', 'age',
        # 分类编码
        'city_encoded', 'heat_level', 'walk_level',
        'plan_type', 'building_type', 'rent_level', 'area_level'
    ]

    X = features[feature_cols]
    y = target

    print(f"\n特征数量: {len(feature_cols)}")

    # 分割数据
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    print(f"训练集: {len(X_train)}, 测试集: {len(X_test)}")

    # 训练模型
    print(f"\n训练XGBoost模型...")
    model = XGBRegressor(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=3,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=-1
    )

    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    # 评估
    y_pred_train = model.predict(X_train)
    y_pred_test = model.predict(X_test)

    train_mae = mean_absolute_error(y_train, y_pred_train)
    test_mae = mean_absolute_error(y_test, y_pred_test)
    test_rmse = np.sqrt(mean_squared_error(y_test, y_pred_test))
    test_r2 = r2_score(y_test, y_pred_test)

    print(f"\n=== 模型评估 ===")
    print(f"训练集 MAE: {train_mae:.4f}")
    print(f"测试集 MAE: {test_mae:.4f}")
    print(f"测试集 RMSE: {test_rmse:.4f}")
    print(f"测试集 R²: {test_r2:.4f}")

    # 交叉验证
    cv_scores = cross_val_score(model, X, y, cv=5, scoring='neg_mean_absolute_error')
    cv_mae = -cv_scores.mean()
    print(f"5折交叉验证 MAE: {cv_mae:.4f} (+/- {cv_scores.std():.4f})")

    # 特征重要性
    importance = dict(zip(feature_cols, model.feature_importances_))
    sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)

    print(f"\n=== 特征重要性 Top 10 ===")
    for feat, imp in sorted_imp[:10]:
        print(f"  {feat}: {imp:.4f}")

    # 保存模型
    with open('models/xgboost_regressor_v2.pkl', 'wb') as f:
        pickle.dump(model, f)

    # 更新配置
    config_v2 = config.copy()
    config_v2['model_name'] = 'XGBoost Property Response Model V2'
    config_v2['version'] = '5.0'
    config_v2['feature_cols'] = feature_cols
    config_v2['training_samples'] = len(df)
    config_v2['metrics'] = {
        'regressor': {
            'train_mae': round(train_mae, 4),
            'test_mae': round(test_mae, 4),
            'test_rmse': round(test_rmse, 4),
            'test_r2': round(test_r2, 4),
            'cv_mae': round(cv_mae, 4)
        }
    }
    config_v2['feature_importance'] = {k: round(float(v), 4) for k, v in sorted_imp}

    with open('models/model_config_v2.json', 'w', encoding='utf-8') as f:
        json.dump(config_v2, f, ensure_ascii=False, indent=2)

    print(f"\n=== 模型已保存 ===")
    print(f"  models/xgboost_regressor_v2.pkl")
    print(f"  models/model_config_v2.json")


if __name__ == "__main__":
    main()
