"""
物件评分器 - 基于机器学习预测物件的推定反響数
"""
import pandas as pd
import numpy as np
import pickle
import os
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import cross_val_score

class PropertyScorer:
    """物件评分器"""

    def __init__(self):
        self.model = None
        self.le_type = LabelEncoder()
        self.le_plan = LabelEncoder()
        self.feature_names = ['rent', 'area_sqm', 'walk_minutes', 'built_year',
                              'property_type_enc', 'floor_plan_enc']
        self.is_fitted = False

    def train(self, csv_path='data/low_response_properties.csv'):
        """训练模型"""
        print("加载训练数据...")
        df = pd.read_csv(csv_path, encoding='utf-8-sig')
        print(f"样本数: {len(df)}")

        # 准备数据
        df = df.copy()

        # 编码分类变量
        df['property_type'] = df['property_type'].fillna('unknown')
        df['floor_plan'] = df['floor_plan'].fillna('unknown')
        df['property_type_enc'] = self.le_type.fit_transform(df['property_type'])
        df['floor_plan_enc'] = self.le_plan.fit_transform(df['floor_plan'])

        # 填充缺失值
        for col in ['rent', 'area_sqm', 'walk_minutes', 'built_year']:
            if col in df.columns:
                df[col] = df[col].fillna(df[col].median())

        X = df[self.feature_names]
        y = df['estimated_response']

        # 训练模型
        print("训练模型...")
        self.model = GradientBoostingRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            random_state=42
        )
        self.model.fit(X, y)

        # 交叉验证
        scores = cross_val_score(self.model, X, y, cv=5, scoring='r2')
        print(f"交叉验证 R² 分数: {scores.mean():.4f} (+/- {scores.std():.4f})")

        # 特征重要性
        print("\n特征重要性:")
        importance = sorted(zip(self.feature_names, self.model.feature_importances_),
                          key=lambda x: -x[1])
        for name, imp in importance:
            print(f"  {name}: {imp:.4f}")

        self.is_fitted = True
        print("\n模型训练完成！")

    def predict(self, property_data: dict) -> dict:
        """
        预测单个物件的反響数

        Args:
            property_data: dict，包含以下字段:
                - rent: 賃料（円）
                - area_sqm: 面積（㎡）
                - walk_minutes: 徒歩（分）
                - built_year: 築年（年）
                - property_type: 物件类型（マンション/アパート/一戸建て）
                - floor_plan: 間取り（1K/1DK/2LDK等）

        Returns:
            dict: {
                'predicted_response': 预测反響数,
                'score': 评分(0-100),
                'rating': 评级(S/A/B/C/D),
                'analysis': 分析说明
            }
        """
        if not self.is_fitted:
            raise ValueError("模型未训练，请先调用 train() 方法")

        # 处理输入
        pt = property_data.get('property_type', 'unknown')
        fp = property_data.get('floor_plan', 'unknown')

        # 编码（处理未见过的类别）
        try:
            pt_enc = self.le_type.transform([pt])[0]
        except:
            pt_enc = 0

        try:
            fp_enc = self.le_plan.transform([fp])[0]
        except:
            fp_enc = 0

        # 构建特征
        X = pd.DataFrame([{
            'rent': property_data.get('rent', 50000),
            'area_sqm': property_data.get('area_sqm', 20),
            'walk_minutes': property_data.get('walk_minutes', 10),
            'built_year': property_data.get('built_year', 2000),
            'property_type_enc': pt_enc,
            'floor_plan_enc': fp_enc,
        }])

        # 预测
        pred = self.model.predict(X)[0]
        pred = max(0, min(10, pred))  # 限制在0-10范围

        # 转换为评分(0-100)
        score = pred * 10

        # 评级
        if score >= 80:
            rating = 'S'
        elif score >= 60:
            rating = 'A'
        elif score >= 40:
            rating = 'B'
        elif score >= 20:
            rating = 'C'
        else:
            rating = 'D'

        # 分析说明
        analysis = self._generate_analysis(property_data, pred)

        return {
            'predicted_response': round(pred, 2),
            'score': round(score, 1),
            'rating': rating,
            'analysis': analysis
        }

    def _generate_analysis(self, data, pred):
        """生成分析说明"""
        points = []

        rent = data.get('rent', 0)
        area = data.get('area_sqm', 0)
        walk = data.get('walk_minutes', 0)
        year = data.get('built_year', 0)

        # 賃料分析
        if rent < 40000:
            points.append("✓ 賃料很低，非常有竞争力")
        elif rent < 60000:
            points.append("✓ 賃料适中")
        elif rent < 100000:
            points.append("△ 賃料偏高")
        else:
            points.append("✗ 賃料较高，可能影响反響")

        # 面積分析
        if area < 15:
            points.append("✓ 紧凑户型，单身租客首选")
        elif area < 25:
            points.append("✓ 面積适中")
        elif area < 40:
            points.append("△ 面積较大，目标客群较窄")
        else:
            points.append("✗ 大户型，租客群体有限")

        # 徒歩分析
        if walk <= 5:
            points.append("✓ 駅近，交通便利")
        elif walk <= 10:
            points.append("✓ 徒歩距离可接受")
        elif walk <= 15:
            points.append("△ 徒歩距离稍远")
        else:
            points.append("✗ 离车站较远")

        # 築年分析
        if year >= 2010:
            points.append("✓ 新築/准新築")
        elif year >= 2000:
            points.append("✓ 築浅物件")
        elif year >= 1990:
            points.append("△ 築年较久")
        else:
            points.append("✗ 老旧物件")

        return "\n".join(points)

    def save(self, path='data/property_scorer.pkl'):
        """保存模型"""
        with open(path, 'wb') as f:
            pickle.dump({
                'model': self.model,
                'le_type': self.le_type,
                'le_plan': self.le_plan,
                'feature_names': self.feature_names,
            }, f)
        print(f"模型已保存到: {path}")

    def load(self, path='data/property_scorer.pkl'):
        """加载模型"""
        with open(path, 'rb') as f:
            data = pickle.load(f)
            self.model = data['model']
            self.le_type = data['le_type']
            self.le_plan = data['le_plan']
            self.feature_names = data['feature_names']
            self.is_fitted = True
        print(f"模型已加载: {path}")


def demo():
    """演示用法"""
    import os
    os.chdir(r"D:\Fango Ads")

    # 训练模型
    scorer = PropertyScorer()
    scorer.train()
    scorer.save()

    print("\n" + "="*60)
    print("测试评分")
    print("="*60)

    # 测试几个物件
    test_cases = [
        {
            "name": "便宜小户型",
            "rent": 35000,
            "area_sqm": 12,
            "walk_minutes": 8,
            "built_year": 1995,
            "property_type": "アパート",
            "floor_plan": "1K"
        },
        {
            "name": "高级大户型",
            "rent": 150000,
            "area_sqm": 55,
            "walk_minutes": 5,
            "built_year": 2020,
            "property_type": "マンション",
            "floor_plan": "2LDK"
        },
        {
            "name": "普通物件",
            "rent": 70000,
            "area_sqm": 25,
            "walk_minutes": 10,
            "built_year": 2005,
            "property_type": "マンション",
            "floor_plan": "1DK"
        }
    ]

    for case in test_cases:
        name = case.pop("name")
        result = scorer.predict(case)
        print(f"\n【{name}】")
        print(f"  预测反響数: {result['predicted_response']} 件/月")
        print(f"  评分: {result['score']} / 100")
        print(f"  评级: {result['rating']}")
        print(f"  分析:")
        for line in result['analysis'].split('\n'):
            print(f"    {line}")


if __name__ == "__main__":
    demo()
