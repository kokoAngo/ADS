"""
物件评估API - 预测物件的反響潜力

使用方法:
    from api.property_evaluator import PropertyEvaluator

    evaluator = PropertyEvaluator()
    result = evaluator.evaluate({
        "rent": 75000,
        "area_sqm": 22,
        "walk_minutes": 8,
        "built_year": 2010,
        "floor_plan": "1DK",
        "property_type": "マンション",
        "area_name": "新宿区",
        "railway_line": "東京メトロ東西線"
    })
"""

import os
import pickle
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List, Dict, Any


@dataclass
class PropertyInput:
    """物件输入数据结构"""
    rent: int                          # 賃料（円）必须
    area_sqm: float                    # 面積（㎡）必须
    walk_minutes: int                  # 駅徒歩（分）必须
    built_year: int                    # 築年 必须
    floor_plan: Optional[str] = None   # 間取り（1K, 1DK, 2LDK等）
    property_type: Optional[str] = None  # 物件類型（マンション, アパート, 一戸建て）
    area_name: Optional[str] = None    # 区域名（新宿区, 渋谷区等）
    railway_line: Optional[str] = None # 沿線名


@dataclass
class EvaluationResult:
    """评估结果"""
    predicted_response: float   # 预测反響数 (0-10)
    score: int                  # 综合评分 (0-100)
    rating: str                 # 评级 (S/A/B/C/D)
    high_response_probability: float  # 高反響概率 (%)
    strengths: List[str]        # 优势
    weaknesses: List[str]       # 劣势
    recommendations: List[str]  # 建议
    details: Dict[str, Any]     # 详细分析


class PropertyEvaluator:
    """物件评估器"""

    # 高反響区域
    HIGH_RESPONSE_AREAS = ['江戸川区', '新宿区', '品川区', '目黒区', '中野区', '豊島区', '渋谷区']
    LOW_RESPONSE_AREAS = ['府中市', '八王子市', '中央区', '千代田区', '調布市']

    # 高反響沿線
    HIGH_RESPONSE_LINES = ['東京メトロ東西線', '京急本線', '東急目黒線', 'ＪＲ総武線', '都営新宿線', 'ＪＲ京浜東北線']

    # 高反響間取り
    HIGH_RESPONSE_PLANS = ['1DK', '2DK', '2K', '1LDK']
    LOW_RESPONSE_PLANS = ['1K']  # 供給過多

    def __init__(self, model_path: str = None):
        """初始化评估器"""
        if model_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(base_dir, 'data', 'property_scorer_v2.pkl')

        self.model = None
        self.le_type = None
        self.le_plan = None

        if os.path.exists(model_path):
            with open(model_path, 'rb') as f:
                data = pickle.load(f)
                self.model = data['model']
                self.le_type = data['le_type']
                self.le_plan = data['le_plan']

    def evaluate(self, property_data: dict) -> dict:
        """
        评估物件

        Args:
            property_data: dict 包含以下字段
                - rent: int, 賃料（円）【必须】
                - area_sqm: float, 面積（㎡）【必须】
                - walk_minutes: int, 駅徒歩（分）【必须】
                - built_year: int, 築年【必须】
                - floor_plan: str, 間取り（可选）
                - property_type: str, 物件類型（可选）
                - area_name: str, 区域名（可选）
                - railway_line: str, 沿線名（可选）

        Returns:
            dict: {
                "predicted_response": float,  # 预测反響数
                "score": int,                 # 综合评分 0-100
                "rating": str,                # 评级 S/A/B/C/D
                "high_response_probability": float,  # 高反響概率%
                "is_recommended": bool,       # 是否推荐
                "strengths": [...],           # 优势列表
                "weaknesses": [...],          # 劣势列表
                "recommendations": [...],     # 建议列表
                "details": {...}              # 详细分析
            }
        """
        # 验证必须字段
        required = ['rent', 'area_sqm', 'walk_minutes', 'built_year']
        for field in required:
            if field not in property_data:
                raise ValueError(f"缺少必须字段: {field}")

        # 提取数据
        rent = property_data['rent']
        area_sqm = property_data['area_sqm']
        walk_minutes = property_data['walk_minutes']
        built_year = property_data['built_year']
        floor_plan = property_data.get('floor_plan', '')
        property_type = property_data.get('property_type', 'マンション')
        area_name = property_data.get('area_name', '')
        railway_line = property_data.get('railway_line', '')

        # 规则评分
        rule_score, strengths, weaknesses, recommendations = self._rule_based_score(
            rent, area_sqm, walk_minutes, built_year,
            floor_plan, property_type, area_name, railway_line
        )

        # ML预测
        ml_response = self._ml_predict(rent, area_sqm, walk_minutes, built_year,
                                        floor_plan, property_type)

        # 综合评分 (规则60% + ML40%)
        combined_score = int(rule_score * 0.6 + ml_response * 10 * 0.4)
        combined_score = max(0, min(100, combined_score))

        # 评级
        if combined_score >= 80:
            rating = 'S'
        elif combined_score >= 65:
            rating = 'A'
        elif combined_score >= 50:
            rating = 'B'
        elif combined_score >= 35:
            rating = 'C'
        else:
            rating = 'D'

        # 高反響概率估算
        high_prob = self._estimate_high_response_probability(
            rent, area_sqm, walk_minutes, built_year,
            floor_plan, area_name, railway_line
        )

        return {
            "predicted_response": round(ml_response, 2),
            "score": combined_score,
            "rating": rating,
            "high_response_probability": round(high_prob, 1),
            "is_recommended": rating in ['S', 'A'],
            "strengths": strengths,
            "weaknesses": weaknesses,
            "recommendations": recommendations,
            "details": {
                "rent_analysis": self._analyze_rent(rent),
                "area_analysis": self._analyze_area(area_sqm),
                "location_analysis": self._analyze_location(walk_minutes, area_name, railway_line),
                "age_analysis": self._analyze_age(built_year),
                "layout_analysis": self._analyze_layout(floor_plan),
                "ml_score": round(ml_response, 2),
                "rule_score": rule_score,
            }
        }

    def _ml_predict(self, rent, area_sqm, walk_minutes, built_year,
                    floor_plan, property_type) -> float:
        """ML模型预测"""
        if self.model is None:
            return 2.5  # 默认中等值

        # 编码
        try:
            pt_enc = self.le_type.transform([property_type or 'マンション'])[0]
        except:
            pt_enc = 0

        try:
            fp_enc = self.le_plan.transform([floor_plan or '1K'])[0]
        except:
            fp_enc = 0

        X = pd.DataFrame([{
            'rent': rent,
            'area_sqm': area_sqm,
            'walk_minutes': walk_minutes,
            'built_year': built_year,
            'property_type_enc': pt_enc,
            'floor_plan_enc': fp_enc,
        }])

        pred = self.model.predict(X)[0]
        return max(0, min(10, pred))

    def _rule_based_score(self, rent, area_sqm, walk_minutes, built_year,
                          floor_plan, property_type, area_name, railway_line):
        """基于规则的评分"""
        score = 50  # 基础分
        strengths = []
        weaknesses = []
        recommendations = []

        rent_man = rent / 10000

        # 賃料评分 (最重要)
        if 6 <= rent_man <= 10:
            score += 15
            strengths.append(f"賃料 ¥{rent:,} 在最佳区间(6-10万)")
        elif 4 <= rent_man < 6:
            score += 8
            strengths.append(f"賃料 ¥{rent:,} 较低有竞争力")
        elif 10 < rent_man <= 15:
            score -= 5
            weaknesses.append(f"賃料 ¥{rent:,} 偏高")
            recommendations.append("考虑降低租金至10万以下")
        elif rent_man > 15:
            score -= 15
            weaknesses.append(f"賃料 ¥{rent:,} 过高，反響会很低")
            recommendations.append("高价物件反響率仅4.8%，建议重新定价")

        # 面積评分
        if area_sqm <= 15:
            score += 10
            strengths.append(f"紧凑户型{area_sqm}㎡，单身租客首选")
        elif 30 <= area_sqm <= 40:
            score += 10
            strengths.append(f"舒适面積{area_sqm}㎡，适合情侣/DINKS")
        elif 15 < area_sqm < 20:
            score += 5
        elif 25 <= area_sqm < 30:
            score -= 3
            weaknesses.append(f"面積{area_sqm}㎡略显中途半端")
        elif area_sqm > 50:
            score -= 8
            weaknesses.append(f"大户型{area_sqm}㎡目标客群有限")

        # 駅徒歩评分
        if walk_minutes <= 5:
            score += 10
            strengths.append(f"駅近{walk_minutes}分，交通便利")
        elif walk_minutes <= 10:
            score += 7
            strengths.append(f"徒歩{walk_minutes}分在可接受范围")
        elif walk_minutes <= 15:
            score -= 5
            weaknesses.append(f"徒歩{walk_minutes}分稍远")
            recommendations.append("駅徒歩10分以内物件反響更好")
        else:
            score -= 12
            weaknesses.append(f"徒歩{walk_minutes}分太远")
            recommendations.append("徒歩15分以上高反響率仅6%")

        # 築年评分
        if 2010 <= built_year < 2020:
            score += 12
            strengths.append(f"2010年代物件高反響率最高(21.3%)")
        elif built_year < 1980:
            score += 5
            strengths.append(f"レトロ物件有特定客群")
        elif 2000 <= built_year < 2010:
            score += 3
        elif built_year >= 2020:
            score -= 5
            weaknesses.append(f"新築物件租金偏高，反響率仅8.6%")
            recommendations.append("新築反響不如2010年代物件")

        # 間取り评分
        if floor_plan:
            if floor_plan in ['1DK', '2DK']:
                score += 10
                strengths.append(f"{floor_plan}是高反響間取り(19%+)")
            elif floor_plan == '2K':
                score += 8
                strengths.append(f"{floor_plan}反響率17.6%")
            elif floor_plan == '1LDK':
                score += 5
            elif floor_plan == '1K':
                score -= 5
                weaknesses.append("1K供給過多，競爭激烈(反響率9.8%)")
                recommendations.append("1DK/2DK比1K反響率高近一倍")

        # 区域评分
        if area_name:
            if area_name in self.HIGH_RESPONSE_AREAS:
                score += 12
                strengths.append(f"{area_name}是高反響区域")
            elif area_name in self.LOW_RESPONSE_AREAS:
                score -= 10
                weaknesses.append(f"{area_name}反響率较低")
                recommendations.append(f"考虑{', '.join(self.HIGH_RESPONSE_AREAS[:3])}等区域")

        # 沿線评分
        if railway_line:
            if any(line in railway_line for line in self.HIGH_RESPONSE_LINES):
                score += 8
                strengths.append(f"{railway_line}是高反響沿線")

        return max(0, min(100, score)), strengths, weaknesses, recommendations

    def _estimate_high_response_probability(self, rent, area_sqm, walk_minutes,
                                            built_year, floor_plan, area_name, railway_line):
        """估算高反響概率"""
        # 基于数据分析的概率估算
        prob = 12.6  # 基础概率(数据中高反響比例)

        rent_man = rent / 10000

        # 賃料影响
        if 8 <= rent_man <= 10:
            prob += 6
        elif 6 <= rent_man < 8:
            prob += 3.5
        elif rent_man > 15:
            prob -= 8

        # 面積影响
        if area_sqm <= 15 or 30 <= area_sqm <= 40:
            prob += 6

        # 駅徒歩影响
        if walk_minutes <= 10:
            prob += 2
        elif walk_minutes > 15:
            prob -= 6

        # 間取り影响
        if floor_plan in ['1DK', '2DK']:
            prob += 7
        elif floor_plan == '1K':
            prob -= 3

        # 区域影响
        if area_name == '江戸川区':
            prob += 20
        elif area_name in ['新宿区', '品川区']:
            prob += 15
        elif area_name in self.HIGH_RESPONSE_AREAS:
            prob += 8
        elif area_name in self.LOW_RESPONSE_AREAS:
            prob -= 10

        # 築年影响
        if 2010 <= built_year < 2020:
            prob += 8
        elif built_year >= 2020:
            prob -= 4

        return max(0, min(95, prob))

    def _analyze_rent(self, rent):
        rent_man = rent / 10000
        if rent_man <= 4:
            return {"level": "很低", "status": "good", "note": "价格竞争力强"}
        elif rent_man <= 6:
            return {"level": "较低", "status": "good", "note": "性价比高"}
        elif rent_man <= 8:
            return {"level": "适中", "status": "best", "note": "最佳区间"}
        elif rent_man <= 10:
            return {"level": "适中偏高", "status": "best", "note": "高反響率区间"}
        elif rent_man <= 15:
            return {"level": "偏高", "status": "warning", "note": "可能影响反響"}
        else:
            return {"level": "过高", "status": "bad", "note": "反響率仅4.8%"}

    def _analyze_area(self, area_sqm):
        if area_sqm <= 15:
            return {"level": "紧凑", "status": "good", "note": "单身首选，高反響18.5%"}
        elif area_sqm <= 20:
            return {"level": "小户型", "status": "ok", "note": "标准单身户型"}
        elif area_sqm <= 25:
            return {"level": "中等", "status": "ok", "note": "可单身可情侣"}
        elif area_sqm <= 30:
            return {"level": "中等偏大", "status": "warning", "note": "略显中途半端"}
        elif area_sqm <= 40:
            return {"level": "舒适", "status": "good", "note": "情侣/DINKS首选，高反響18.6%"}
        else:
            return {"level": "大户型", "status": "warning", "note": "目标客群有限"}

    def _analyze_location(self, walk_minutes, area_name, railway_line):
        result = {}

        if walk_minutes <= 5:
            result["walk"] = {"status": "best", "note": "駅近物件"}
        elif walk_minutes <= 10:
            result["walk"] = {"status": "good", "note": "徒歩圏内"}
        elif walk_minutes <= 15:
            result["walk"] = {"status": "warning", "note": "稍远"}
        else:
            result["walk"] = {"status": "bad", "note": "太远，反響率低"}

        if area_name in self.HIGH_RESPONSE_AREAS:
            result["area"] = {"status": "good", "note": f"{area_name}是优质区域"}
        elif area_name in self.LOW_RESPONSE_AREAS:
            result["area"] = {"status": "bad", "note": f"{area_name}反響率较低"}
        else:
            result["area"] = {"status": "ok", "note": "普通区域"}

        return result

    def _analyze_age(self, built_year):
        if built_year >= 2020:
            return {"level": "新築", "status": "warning", "note": "租金高导致反響低"}
        elif built_year >= 2010:
            return {"level": "準新築", "status": "best", "note": "最高反響率21.3%"}
        elif built_year >= 2000:
            return {"level": "築浅", "status": "ok", "note": "状态良好"}
        elif built_year >= 1990:
            return {"level": "築20年+", "status": "ok", "note": "注意设备老化"}
        elif built_year >= 1980:
            return {"level": "築30年+", "status": "warning", "note": "可能需要翻新"}
        else:
            return {"level": "レトロ", "status": "ok", "note": "有特定客群喜好"}

    def _analyze_layout(self, floor_plan):
        if not floor_plan:
            return {"status": "unknown", "note": "未指定"}

        if floor_plan in ['1DK', '2DK']:
            return {"status": "best", "note": f"{floor_plan}高反響率19%"}
        elif floor_plan == '2K':
            return {"status": "good", "note": "反響率17.6%"}
        elif floor_plan in ['1LDK', '2LDK']:
            return {"status": "ok", "note": "标准户型"}
        elif floor_plan == '1K':
            return {"status": "warning", "note": "供給過多，競爭激烈"}
        else:
            return {"status": "ok", "note": "其他户型"}

    def batch_evaluate(self, properties: List[dict]) -> List[dict]:
        """批量评估多个物件"""
        return [self.evaluate(p) for p in properties]

    def compare(self, properties: List[dict]) -> dict:
        """比较多个物件"""
        results = self.batch_evaluate(properties)

        # 按评分排序
        sorted_results = sorted(
            enumerate(results),
            key=lambda x: x[1]['score'],
            reverse=True
        )

        return {
            "ranking": [
                {
                    "rank": i + 1,
                    "index": idx,
                    "score": r['score'],
                    "rating": r['rating'],
                    "predicted_response": r['predicted_response'],
                }
                for i, (idx, r) in enumerate(sorted_results)
            ],
            "best": sorted_results[0][0] if sorted_results else None,
            "details": results,
        }


# 便捷函数
def evaluate_property(property_data: dict) -> dict:
    """快速评估单个物件"""
    evaluator = PropertyEvaluator()
    return evaluator.evaluate(property_data)


def is_high_response_property(property_data: dict, threshold: float = 50) -> bool:
    """判断是否为高反響物件"""
    result = evaluate_property(property_data)
    return result['score'] >= threshold


if __name__ == "__main__":
    # 测试示例
    evaluator = PropertyEvaluator()

    test_properties = [
        {
            "name": "理想物件",
            "rent": 75000,
            "area_sqm": 22,
            "walk_minutes": 6,
            "built_year": 2015,
            "floor_plan": "1DK",
            "property_type": "マンション",
            "area_name": "新宿区",
            "railway_line": "東京メトロ東西線"
        },
        {
            "name": "普通物件",
            "rent": 85000,
            "area_sqm": 25,
            "walk_minutes": 12,
            "built_year": 2005,
            "floor_plan": "1K",
            "property_type": "マンション",
            "area_name": "杉並区",
        },
        {
            "name": "高价物件",
            "rent": 180000,
            "area_sqm": 45,
            "walk_minutes": 5,
            "built_year": 2022,
            "floor_plan": "1LDK",
            "property_type": "マンション",
            "area_name": "千代田区",
        },
    ]

    print("="*70)
    print("物件评估API测试")
    print("="*70)

    for prop in test_properties:
        name = prop.pop("name")
        result = evaluator.evaluate(prop)

        print(f"\n【{name}】")
        print(f"  評分: {result['score']}/100 ({result['rating']})")
        print(f"  予測反響: {result['predicted_response']} 件/月")
        print(f"  高反響確率: {result['high_response_probability']}%")
        print(f"  推薦: {'✅ はい' if result['is_recommended'] else '❌ いいえ'}")

        if result['strengths']:
            print(f"  強み:")
            for s in result['strengths']:
                print(f"    ✓ {s}")

        if result['weaknesses']:
            print(f"  弱み:")
            for w in result['weaknesses']:
                print(f"    ✗ {w}")

        if result['recommendations']:
            print(f"  提案:")
            for r in result['recommendations']:
                print(f"    → {r}")
