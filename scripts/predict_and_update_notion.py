"""
批量预测物件反响数并更新到Notion
1. 从REINS抓取物件数据
2. 使用XGBoost回归模型预测反响数
3. 将预测结果写入Notion数据库
"""
import os
import sys
import time
import json
import pickle
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import pandas as pd
import numpy as np

load_dotenv()

# REINS配置
REINS_URL = "https://system.reins.jp/login/main/KG/GKG001200"
REINS_USERNAME = os.getenv("REINS_USERNAME")
REINS_PASSWORD = os.getenv("REINS_PASSWORD")

# Notion配置
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
NOTION_DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

# 加载模型
with open("models/xgboost_regressor.pkl", "rb") as f:
    model = pickle.load(f)

with open("models/model_config.json", "r") as f:
    config = json.load(f)


class NotionClient:
    """Notion API客户端"""

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        self.base_url = "https://api.notion.com/v1"

    def get_database_pages(self, database_id, start_cursor=None):
        """获取数据库中的所有页面"""
        url = f"{self.base_url}/databases/{database_id}/query"
        data = {"page_size": 100}
        if start_cursor:
            data["start_cursor"] = start_cursor

        response = requests.post(url, headers=self.headers, json=data)
        return response.json()

    def get_all_pages(self, database_id):
        """获取所有页面（处理分页）"""
        all_pages = []
        start_cursor = None

        while True:
            result = self.get_database_pages(database_id, start_cursor)
            if "results" not in result:
                print(f"API错误: {result}")
                break

            all_pages.extend(result["results"])

            if not result.get("has_more"):
                break
            start_cursor = result.get("next_cursor")

        return all_pages

    def update_page(self, page_id, properties):
        """更新页面属性"""
        url = f"{self.base_url}/pages/{page_id}"
        data = {"properties": properties}

        response = requests.patch(url, headers=self.headers, json=data)
        return response.json()


class ReinsScraper:
    """REINS物件爬虫"""

    def __init__(self, headless=True):
        self.headless = headless
        self.browser = None
        self.page = None

    def start(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            locale='ja-JP',
        )
        self.page = self.context.new_page()
        print("浏览器启动")

    def stop(self):
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright'):
            self.playwright.stop()
        print("浏览器关闭")

    def login(self):
        print("登录REINS...")
        self.page.goto(REINS_URL, wait_until='networkidle')
        time.sleep(2)

        username_input = self.page.query_selector('input[type="text"]')
        password_input = self.page.query_selector('input[type="password"]')

        if username_input and password_input:
            username_input.fill(REINS_USERNAME)
            time.sleep(0.3)
            password_input.fill(REINS_PASSWORD)
            time.sleep(0.3)

            # 勾选同意复选框
            labels = self.page.query_selector_all('label')
            for label in labels:
                try:
                    text = label.inner_text()
                    if '遵守' in text:
                        label.click()
                        time.sleep(0.3)
                        break
                except:
                    pass

            login_btn = self.page.query_selector('button:has-text("ログイン")')
            if login_btn:
                login_btn.click(force=True)
                time.sleep(3)

            print(f"登录成功")
            return True
        return False

    def goto_bukken_search(self):
        print("进入物件番号検索...")
        bukken_link = self.page.locator('text=物件番号検索').first
        if bukken_link.count() > 0:
            bukken_link.click()
            time.sleep(2)
            return True
        return False

    def search_bukken(self, bukken_number):
        """搜索单个物件"""
        import re

        try:
            inputs = self.page.query_selector_all('input[type="text"]')
            for inp in inputs:
                inp.fill('')

            if inputs:
                inputs[0].fill(str(bukken_number))
                time.sleep(0.3)

            search_btn = self.page.locator('button:has-text("検索")').first
            if search_btn.count() > 0:
                search_btn.click()
                time.sleep(2)

            # 提取数据
            data = {'bukken_number': str(bukken_number)}
            text = self.page.locator('body').inner_text()

            # 提取租金
            m = re.search(r'賃料[：:\s]*(\d+(?:,\d+)?)\s*円', text)
            if m:
                data['rent'] = int(m.group(1).replace(',', ''))
            else:
                m = re.search(r'(\d+(?:\.\d+)?)\s*万円', text)
                if m:
                    data['rent'] = int(float(m.group(1)) * 10000)

            # 提取面積
            m = re.search(r'専有面積[：:\s]*([\d.]+)\s*[㎡m]', text)
            if not m:
                m = re.search(r'([\d.]+)\s*㎡', text)
            if m:
                data['area_sqm'] = float(m.group(1))

            # 提取築年
            m = re.search(r'築年月[：:\s]*(\d{4})', text)
            if not m:
                m = re.search(r'(\d{4})年', text)
            if m:
                data['built_year'] = int(m.group(1))

            # 提取徒歩分数
            m = re.search(r'徒歩\s*(\d+)\s*分', text)
            if m:
                data['walk_minutes'] = int(m.group(1))

            # 提取間取り
            m = re.search(r'([1-9][SLKDR]+)', text)
            if m:
                data['floor_plan'] = m.group(1)

            # 提取所在地（区）
            m = re.search(r'([\u4e00-\u9fa5]+区)', text)
            if m:
                data['city'] = m.group(1)

            # 返回搜索页
            self.page.go_back()
            time.sleep(1)

            return data

        except Exception as e:
            print(f"  搜索失败: {e}")
            return None


def prepare_features(data):
    """准备模型特征 (v3 - 数据驱动分档)"""
    # 基本特征
    rent = data.get('rent', 80000)
    area_sqm = data.get('area_sqm', 25)
    built_year = data.get('built_year', 2010)
    walk_minutes = data.get('walk_minutes', 10)
    city = data.get('city', '')
    floor_plan = data.get('floor_plan', '1K')

    # 计算特征
    rent_per_sqm = rent / area_sqm if area_sqm > 0 else 0
    age = 2026 - built_year

    # 区域热度三档
    high_heat_areas = config.get('high_heat_areas', [
        '江戸川区', '新宿区', '品川区', '目黒区', '中野区', '豊島区', '渋谷区'
    ])
    mid_heat_areas = config.get('mid_heat_areas', [
        '大田区', '北区', '世田谷区', '板橋区'
    ])

    if city in high_heat_areas:
        heat_level = 2  # 高热度
    elif city in mid_heat_areas:
        heat_level = 1  # 中热度
    else:
        heat_level = 0  # 低热度

    # 徒步距离三档 (基于数据分析: 6-10分最佳)
    if walk_minutes <= 5:
        walk_level = 1  # 近
    elif walk_minutes <= 10:
        walk_level = 2  # 中 (最佳)
    else:
        walk_level = 0  # 远

    # 户型三档 (基于反响数据)
    high_response_plans = config.get('high_response_plans', ['1DK', '2DK', '2K', '3DK', '3K'])
    mid_response_plans = config.get('mid_response_plans', ['1LDK', '3LDK', '1K', '2LDK'])

    if floor_plan in high_response_plans:
        plan_type = 2  # 高反响户型
    elif floor_plan in mid_response_plans:
        plan_type = 1  # 中反响户型
    else:
        plan_type = 0  # 其他

    # 租金等级
    if rent < 50000:
        rent_level = 0
    elif rent < 80000:
        rent_level = 1
    elif rent < 120000:
        rent_level = 2
    else:
        rent_level = 3

    # 面积等级
    if area_sqm < 20:
        area_level = 0
    elif area_sqm < 30:
        area_level = 1
    elif area_sqm < 50:
        area_level = 2
    else:
        area_level = 3

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
    city_encoded = city_mapping.get(city, 0)

    # v3特征顺序: 与model_config.json中feature_cols一致
    features = [
        rent,           # rent
        area_sqm,       # area_sqm
        built_year,     # built_year
        walk_minutes,   # walk_minutes
        city_encoded,   # city_encoded
        heat_level,     # heat_level
        rent_per_sqm,   # rent_per_sqm
        age,            # age
        walk_level,     # walk_level
        plan_type,      # plan_type
        rent_level,     # rent_level
        area_level      # area_level
    ]

    return features


def predict_response(data):
    """预测反响数"""
    features = prepare_features(data)
    X = np.array([features])
    prediction = model.predict(X)[0]
    # 转换为Python原生float类型，避免JSON序列化错误
    return float(round(max(0, prediction), 1))


def main():
    print("=" * 50)
    print("REINS物件反响数预测 → Notion更新")
    print("=" * 50)

    # 初始化Notion客户端
    notion = NotionClient(NOTION_API_KEY)

    # 从文件读取物件番号和page_id映射
    print("\n读取Notion物件映射...")
    bukken_map = {}
    with open('data/notion_pages.txt', 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '|' in line:
                parts = line.split('|')
                bukken_number = parts[0]
                page_id = parts[1]
                if bukken_number:
                    bukken_map[bukken_number] = page_id

    print(f"共 {len(bukken_map)} 个物件")

    # 限制300个
    bukken_list = list(bukken_map.items())[:300]
    print(f"将处理 {len(bukken_list)} 个物件")

    # 启动REINS爬虫
    scraper = ReinsScraper(headless=False)
    results = []

    try:
        scraper.start()
        if not scraper.login():
            print("登录失败")
            return

        if not scraper.goto_bukken_search():
            print("进入物件番号検索失败")
            return

        # 批量处理
        success_count = 0
        for i, (bukken_number, page_id) in enumerate(bukken_list):
            print(f"\n[{i+1}/{len(bukken_list)}] 处理: {bukken_number}")

            # 抓取数据
            data = scraper.search_bukken(bukken_number)

            if data and data.get('rent'):
                # 预测反响数
                predicted = predict_response(data)
                print(f"  租金: {data.get('rent'):,}円, 面积: {data.get('area_sqm')}㎡, 预测反响: {predicted}")

                # 更新Notion（填写所有字段）
                try:
                    update_props = {
                        "予測反響数": {"number": predicted}
                    }

                    # 添加物件详细信息
                    if data.get('rent'):
                        update_props["賃料"] = {"number": data['rent']}
                    if data.get('area_sqm'):
                        update_props["専有面積"] = {"number": data['area_sqm']}
                    if data.get('built_year'):
                        update_props["築年"] = {"number": data['built_year']}
                    if data.get('walk_minutes'):
                        update_props["徒歩分数"] = {"number": data['walk_minutes']}
                    if data.get('floor_plan'):
                        update_props["間取り"] = {"rich_text": [{"text": {"content": data['floor_plan']}}]}
                    if data.get('city'):
                        update_props["所在地"] = {"rich_text": [{"text": {"content": data['city']}}]}

                    result = notion.update_page(page_id, update_props)
                    if "id" in result:
                        print(f"  ✓ Notion更新成功")
                        success_count += 1
                    else:
                        print(f"  ✗ Notion更新失败: {result.get('message', 'Unknown error')}")
                except Exception as e:
                    print(f"  ✗ Notion更新异常: {e}")

                results.append({
                    'bukken_number': bukken_number,
                    'rent': data.get('rent'),
                    'area_sqm': data.get('area_sqm'),
                    'predicted_response': predicted,
                    'page_id': page_id
                })
            else:
                print(f"  ✗ 未能获取物件数据")

            # 控制速率
            time.sleep(0.5)

        print(f"\n{'='*50}")
        print(f"完成! 成功更新 {success_count}/{len(bukken_list)} 个物件")

        # 保存结果
        if results:
            df = pd.DataFrame(results)
            df.to_csv('data/notion_predictions.csv', index=False, encoding='utf-8-sig')
            print(f"结果已保存: data/notion_predictions.csv")

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        scraper.stop()


if __name__ == "__main__":
    main()
