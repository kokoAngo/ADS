"""
批量预测物件view数并更新到Notion (V3 - Notion数据直接评估)
1. 从Notion新着物件DB读取未评分物件的全部情报
2. 直接用物件情报进行预测（不再访问REINS）
3. 将予測_view数写回Notion
"""
import os
import sys
import json
import pickle
import requests
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

from dotenv import load_dotenv
import pandas as pd
import numpy as np

# 强制刷新输出
sys.stdout.reconfigure(line_buffering=True)

load_dotenv()

# Notion配置
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
NOTION_DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

# 加载V2模型
with open("models/xgboost_regressor_v2.pkl", "rb") as f:
    model = pickle.load(f)

with open("models/model_config_v2.json", "r", encoding='utf-8') as f:
    config = json.load(f)


class NotionClient:
    """Notion API客户端"""

    def __init__(self, api_key, database_id):
        self.api_key = api_key
        self.database_id = database_id
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        self.base_url = "https://api.notion.com/v1"

    def update_page(self, page_id, properties):
        """更新页面属性"""
        url = f"{self.base_url}/pages/{page_id}"
        data = {"properties": properties}
        response = requests.patch(url, headers=self.headers, json=data)
        return response.json()

    def get_unscored_properties(self, after="2026-04-09T17:01:00+09:00"):
        """获取予測_view数为空且在指定时间之后创建的物件，返回完整情报"""
        url = f"{self.base_url}/databases/{self.database_id}/query"
        all_results = []
        has_more = True
        start_cursor = None

        filter_conditions = {
            "and": [
                {"property": "予測_view数", "number": {"is_empty": True}},
                {"timestamp": "created_time", "created_time": {"after": after}}
            ]
        }

        while has_more:
            payload = {
                "page_size": 100,
                "filter": filter_conditions
            }
            if start_cursor:
                payload["start_cursor"] = start_cursor

            response = requests.post(url, headers=self.headers, json=payload, timeout=60)
            data = response.json()

            if "results" in data:
                all_results.extend(data["results"])
                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")
            else:
                print(f"Query error: {data}")
                break

        # 提取全部物件情报
        properties_list = []
        for page in all_results:
            prop_data = self._extract_property_data(page)
            if prop_data:
                properties_list.append(prop_data)

        return properties_list

    def _extract_property_data(self, page):
        """从Notion页面提取物件数据并转换为模型所需格式"""
        props = page["properties"]
        data = {"page_id": page["id"]}

        # REINS_ID (title字段)
        if "REINS_ID" in props and props["REINS_ID"].get("title"):
            data["bukken_number"] = props["REINS_ID"]["title"][0]["plain_text"]

        def get_text(field_name):
            if field_name in props and props[field_name].get("rich_text"):
                return props[field_name]["rich_text"][0]["plain_text"]
            return ""

        # 賃料（万円）→ rent（円）
        rent_text = get_text("賃料（万円）")
        if rent_text:
            try:
                data["rent"] = int(float(rent_text) * 10000)
            except ValueError:
                pass

        # 使用部分面積（m2）→ area_sqm
        area_text = get_text("使用部分面積（m2）")
        if area_text:
            try:
                data["area_sqm"] = float(area_text)
            except ValueError:
                pass

        # 築年月 → built_year（取前4位）
        chiku_text = get_text("築年月")
        if chiku_text and len(chiku_text) >= 4:
            try:
                data["built_year"] = int(chiku_text[:4])
            except ValueError:
                pass

        # 徒歩(分) → walk_minutes
        walk_text = get_text("徒歩(分)")
        if walk_text:
            try:
                data["walk_minutes"] = int(float(walk_text))
            except ValueError:
                pass

        # 間取 → floor_plan
        madori = get_text("間取")
        if madori:
            data["floor_plan"] = madori

        # 所在地 → city（提取区名）
        address = get_text("所在地")
        if address:
            data["address"] = address
            tokyo_wards = ['千代田区', '中央区', '港区', '新宿区', '文京区', '台東区', '墨田区',
                          '江東区', '品川区', '目黒区', '大田区', '世田谷区', '渋谷区', '中野区',
                          '杉並区', '豊島区', '北区', '荒川区', '板橋区', '練馬区', '足立区',
                          '葛飾区', '江戸川区']
            for ward in tokyo_wards:
                if ward in address:
                    data["city"] = ward
                    break

        # 物件種目 → property_type
        shubetsu = get_text("物件種目")
        if shubetsu:
            data["property_type"] = shubetsu

        # 管理費（円）/ 共益費（円）→ management_fee
        mgmt_text = get_text("管理費（円）")
        kyoueki_text = get_text("共益費（円）")
        mgmt_fee = 0
        if mgmt_text:
            try:
                mgmt_fee += int(float(mgmt_text.replace(",", "")))
            except ValueError:
                pass
        if kyoueki_text:
            try:
                mgmt_fee += int(float(kyoueki_text.replace(",", "")))
            except ValueError:
                pass
        if mgmt_fee > 0:
            data["management_fee"] = mgmt_fee

        # 敷金/保証金 → deposit（月数）
        shikikin_text = get_text("敷金/保証金")
        if shikikin_text:
            data["deposit"] = self._parse_months(shikikin_text)

        # 礼金/権利金 → key_money（月数）
        reikin_text = get_text("礼金/権利金")
        if reikin_text:
            data["key_money"] = self._parse_months(reikin_text)

        # 商号 → management_company
        company = get_text("商号")
        if company:
            data["management_company"] = company

        # 沿線駅 → railway, station
        ensen = get_text("沿線駅")
        if ensen:
            # 格式: "小田急線　東北沢" or "大江戸線　蔵前"
            parts = re.split(r'[\s　]+', ensen, maxsplit=1)
            if len(parts) >= 2:
                data["railway"] = parts[0]
                data["station"] = parts[1]

        return data if data.get("bukken_number") else None

    @staticmethod
    def _parse_months(text):
        """解析敷金/礼金文本，提取月数。例: '1ヶ月/-' → 1.0, 'なし/-' → 0.0, '22.3万円/なし' → 按万円换算"""
        if not text:
            return 0.0
        # 取斜杠前的部分（敷金/保証金 → 取敷金部分）
        first = text.split("/")[0].strip()
        if "なし" in first or first == "-" or first == "ー":
            return 0.0
        # "1ヶ月" or "2ヶ月"
        m = re.search(r'([\d.]+)\s*[ヶヵか]?月', first)
        if m:
            return float(m.group(1))
        # "22.3万円" — 万円表記の場合は月数として使えないので0扱い
        if "万" in first:
            return 0.0
        # 純粋な数字
        m = re.search(r'([\d.]+)', first)
        if m:
            return float(m.group(1))
        return 0.0




def prepare_features(data):
    """准备模型特征 (V2 - 21个特征，与train_model_v2.py一致)"""
    rent = data.get('rent', 80000)
    area_sqm = data.get('area_sqm', 25)
    built_year = data.get('built_year', 2010)
    walk_minutes = data.get('walk_minutes', 10)
    city = data.get('city', '')
    floor_plan = data.get('floor_plan', '1K')
    property_type = data.get('property_type', '')

    # 费用相关特征
    management_fee = data.get('management_fee', 0)
    deposit = data.get('deposit', 1.0)
    key_money = data.get('key_money', 1.0)

    # 转换类型
    if isinstance(deposit, str):
        try:
            deposit = float(deposit)
        except:
            deposit = 1.0
    if isinstance(key_money, str):
        try:
            key_money = float(key_money)
        except:
            key_money = 1.0

    # 派生特征
    total_rent = rent + management_fee
    rent_per_sqm = rent / area_sqm if area_sqm > 0 else 0
    total_rent_per_sqm = total_rent / area_sqm if area_sqm > 0 else 0
    age = 2026 - built_year

    # 零礼金/零敷金标志
    zero_deposit = 1 if deposit == 0 else 0
    zero_key_money = 1 if key_money == 0 else 0

    # 初期费用
    initial_cost = deposit + key_money

    # 区域热度三档
    high_heat_areas = config.get('high_heat_areas', [])
    mid_heat_areas = config.get('mid_heat_areas', [])

    if city in high_heat_areas:
        heat_level = 2
    elif city in mid_heat_areas:
        heat_level = 1
    else:
        heat_level = 0

    # 徒步距离三档
    if walk_minutes <= 5:
        walk_level = 2
    elif walk_minutes <= 10:
        walk_level = 1
    else:
        walk_level = 0

    # 户型三档
    high_response_plans = config.get('high_response_plans', [])
    mid_response_plans = config.get('mid_response_plans', [])

    if floor_plan in high_response_plans:
        plan_type = 2
    elif floor_plan in mid_response_plans:
        plan_type = 1
    else:
        plan_type = 0

    # 建物类型编码
    if 'マンション' in str(property_type):
        building_type = 2
    elif 'アパート' in str(property_type):
        building_type = 1
    else:
        building_type = 0

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

    # 特征列表 (21个特征 - V2模型格式)
    # 顺序必须与model_config_v2.json中的feature_cols一致
    features = [
        rent, area_sqm, built_year, walk_minutes, management_fee,
        total_rent, deposit, key_money, initial_cost,
        zero_deposit, zero_key_money,
        rent_per_sqm, total_rent_per_sqm, age,
        city_encoded, heat_level, walk_level,
        plan_type, building_type, rent_level, area_level
    ]

    return features


def predict_response(data):
    """预测反响数"""
    features = prepare_features(data)
    X = np.array([features])
    prediction = model.predict(X)[0]
    return float(round(max(0, prediction), 1))


def main():
    print("=" * 60)
    print("物件view数预测 V3 - Notion数据直接评估")
    print("=" * 60)

    # 初始化
    notion = NotionClient(NOTION_API_KEY, NOTION_DATABASE_ID)

    # 从Notion查询未评分物件（含全部情报）
    print("\n从Notion查询未评分物件...")
    properties = notion.get_unscored_properties()
    print(f"未评分物件: {len(properties)} 个")

    if not properties:
        print("\n没有需要处理的物件，退出")
        return

    results = []
    success_count = 0
    skip_count = 0

    for i, data in enumerate(properties):
        bukken_number = data.get("bukken_number", "?")
        page_id = data["page_id"]
        print(f"\n[{i+1}/{len(properties)}] {bukken_number}")

        if not data.get("rent"):
            print(f"  缺少賃料，跳过")
            skip_count += 1
            continue

        # 预测
        score = predict_response(data)
        print(f"  ¥{data['rent']:,} {data.get('area_sqm', '?')}㎡ {data.get('city', '')} {data.get('floor_plan', '')} → {score:.1f}")

        # 更新Notion
        try:
            update_props = {
                "予測_view数": {"number": score}
            }
            result = notion.update_page(page_id, update_props)
            if "id" in result:
                print(f"  → Notion更新成功")
                success_count += 1
            else:
                print(f"  ✗ Notion更新失败")
        except Exception as e:
            print(f"  ✗ Notion更新异常: {e}")

        data["predicted_response"] = score
        results.append(data)

    print(f"\n{'='*60}")
    print(f"完成!")
    print(f"成功更新: {success_count}/{len(properties)} 个")
    if skip_count:
        print(f"跳过（缺少賃料）: {skip_count} 个")

    high_score = [r for r in results if r.get("predicted_response", 0) >= 7.0]
    print(f"予測view >= 7: {len(high_score)} 个")

    if results:
        df = pd.DataFrame(results)
        df.to_csv('data/notion_predictions_v2.csv', index=False, encoding='utf-8-sig')
        print(f"结果已保存: data/notion_predictions_v2.csv")

        new_ids = [r.get('bukken_number') for r in results if r.get('bukken_number')]
        cache_file = 'data/new_properties_cache.txt'
        with open(cache_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(new_ids))
        print(f"新物件缓存已保存: {cache_file} ({len(new_ids)}个)")


if __name__ == "__main__":
    main()
