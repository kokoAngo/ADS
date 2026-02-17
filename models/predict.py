"""
房产人气预测模型
使用训练好的XGBoost模型预测房产是否为高反響物件
"""
import joblib
import json
import numpy as np
import pandas as pd
from pathlib import Path

# 模型目录
MODEL_DIR = Path(__file__).parent


def load_model():
    """加载模型和配置"""
    model = joblib.load(MODEL_DIR / 'xgboost_model.pkl')
    encoders = joblib.load(MODEL_DIR / 'label_encoders.pkl')
    with open(MODEL_DIR / 'model_config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    return model, encoders, config


def prepare_features(property_data: dict, encoders: dict, config: dict) -> pd.DataFrame:
    """
    准备特征数据 (v3 - 数据驱动分档)

    Args:
        property_data: 房产信息字典，包含:
            - rent: 租金（円）
            - area_sqm: 面积（㎡）
            - built_year: 建造年份
            - walk_minutes: 徒歩分钟数
            - address_city: 区/市名称
            - floor_plan: 间取り (如 "1K", "2LDK")
            - property_type: 物件类型
    """
    df = pd.DataFrame([property_data])

    # 基础特征
    df['rent'] = pd.to_numeric(df['rent'], errors='coerce')
    df['area_sqm'] = pd.to_numeric(df['area_sqm'], errors='coerce')
    df['built_year'] = pd.to_numeric(df['built_year'], errors='coerce')
    df['walk_minutes'] = pd.to_numeric(df['walk_minutes'], errors='coerce')

    # 派生特征
    df['rent_per_sqm'] = df['rent'] / df['area_sqm']  # ㎡単価
    df['age'] = 2026 - df['built_year']

    # 区域热度三档
    high_heat_areas = config.get('high_heat_areas', [])
    mid_heat_areas = config.get('mid_heat_areas', [])

    def get_heat_level(city):
        if city in high_heat_areas:
            return 2
        elif city in mid_heat_areas:
            return 1
        else:
            return 0

    df['heat_level'] = df['address_city'].apply(get_heat_level)

    # 徒步距离三档 (基于数据分析: 6-10分最佳)
    def get_walk_level(minutes):
        if pd.isna(minutes):
            return 1
        if minutes <= 5:
            return 1  # 近
        elif minutes <= 10:
            return 2  # 中 (最佳)
        else:
            return 0  # 远

    df['walk_level'] = df['walk_minutes'].apply(get_walk_level)

    # 户型三档 (基于反响数据)
    high_response_plans = config.get('high_response_plans', ['1DK', '2DK', '2K', '3DK', '3K'])
    mid_response_plans = config.get('mid_response_plans', ['1LDK', '3LDK', '1K', '2LDK'])

    def get_plan_type(plan):
        if pd.isna(plan):
            return 1
        if plan in high_response_plans:
            return 2  # 高反响户型
        elif plan in mid_response_plans:
            return 1  # 中反响户型
        else:
            return 0  # 其他

    df['plan_type'] = df['floor_plan'].apply(get_plan_type)

    df['rent_level'] = pd.cut(df['rent'], bins=[0, 50000, 80000, 120000, np.inf],
                              labels=[0, 1, 2, 3]).astype(float)
    df['area_level'] = pd.cut(df['area_sqm'], bins=[0, 20, 30, 50, np.inf],
                              labels=[0, 1, 2, 3]).astype(float)

    # 固定的区域编码映射（与训练数据一致）
    city_mapping = config.get('city_mapping', {
        '千代田区': 0, '中央区': 1, '港区': 2, '新宿区': 3, '文京区': 4,
        '台東区': 5, '墨田区': 6, '江東区': 7, '品川区': 8, '目黒区': 9,
        '大田区': 10, '世田谷区': 11, '渋谷区': 12, '中野区': 13, '杉並区': 14,
        '豊島区': 15, '北区': 16, '荒川区': 17, '板橋区': 18, '練馬区': 19,
        '足立区': 20, '葛飾区': 21, '江戸川区': 22,
        '八王子市': 23, '立川市': 24, '三鷹市': 25, '府中市': 26,
        '調布市': 27, '町田市': 28, '日野市': 29, '国分寺市': 30, '小金井市': 31
    })
    city = df['address_city'].fillna('Unknown').iloc[0]
    df['city_encoded'] = city_mapping.get(city, 0)

    return df[config['feature_cols']]


def predict(property_data: dict) -> dict:
    """
    预测房产是否为高反響物件

    Args:
        property_data: 房产信息字典

    Returns:
        预测结果字典，包含:
            - is_high_response: 是否高反響 (True/False)
            - probability: 高反響概率 (0-1)
            - confidence: 置信度描述
    """
    model, encoders, config = load_model()
    features = prepare_features(property_data, encoders, config)

    # 处理缺失值
    features = features.fillna(0)

    # 预测
    prob = model.predict_proba(features)[0][1]
    is_high = prob >= 0.5

    # 置信度描述
    if prob >= 0.8:
        confidence = "很高"
    elif prob >= 0.6:
        confidence = "较高"
    elif prob >= 0.4:
        confidence = "中等"
    elif prob >= 0.2:
        confidence = "较低"
    else:
        confidence = "很低"

    return {
        'is_high_response': bool(is_high),
        'probability': round(prob, 3),
        'confidence': confidence,
        'threshold': config['threshold']
    }


def predict_batch(properties_df: pd.DataFrame) -> pd.DataFrame:
    """
    批量预测

    Args:
        properties_df: 包含房产信息的DataFrame

    Returns:
        添加了预测结果的DataFrame
    """
    model, encoders, config = load_model()

    results = []
    for _, row in properties_df.iterrows():
        result = predict(row.to_dict())
        results.append(result)

    result_df = pd.DataFrame(results)
    return pd.concat([properties_df.reset_index(drop=True), result_df], axis=1)


# 使用示例
if __name__ == '__main__':
    # 示例房产数据
    sample_property = {
        'rent': 75000,
        'area_sqm': 25,
        'built_year': 2015,
        'walk_minutes': 5,
        'address_city': '新宿区',
        'floor_plan': '1K',
        'property_type': 'マンション'
    }

    result = predict(sample_property)

    print("===== 房产人气预测 =====")
    print(f"输入: {sample_property}")
    print(f"\n预测结果:")
    print(f"  高反響物件: {'是' if result['is_high_response'] else '否'}")
    print(f"  概率: {result['probability']:.1%}")
    print(f"  置信度: {result['confidence']}")
