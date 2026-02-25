"""
批量预测物件反响数并更新到Notion (V2 - 两阶段筛选)
1. 第一阶段：从搜索结果快速提取基本信息，预测得分
2. 第二阶段：得分>3分时，进入详细页面获取更多信息，重新预测
3. 将结果写入Notion数据库
"""
import os
import sys
import time
import json
import pickle
import requests
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import sys

# 强制刷新输出
sys.stdout.reconfigure(line_buffering=True)

load_dotenv()

# REINS配置
REINS_URL = "https://system.reins.jp/login/main/KG/GKG001200"
REINS_USERNAME = os.getenv("REINS_USERNAME")
REINS_PASSWORD = os.getenv("REINS_PASSWORD")

# Notion配置
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
NOTION_DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

# 筛选阈值
SCORE_THRESHOLD = 3.0

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

    def update_page(self, page_id, properties):
        """更新页面属性"""
        url = f"{self.base_url}/pages/{page_id}"
        data = {"properties": properties}
        response = requests.patch(url, headers=self.headers, json=data)
        return response.json()


class ReinsScraper:
    """REINS物件爬虫 - 两阶段筛选版"""

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

            print("登录成功")
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

    def search_bukken_basic(self, bukken_number):
        """第一阶段：从搜索结果提取基本信息"""
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

            # 提取基本数据
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

            # 提取区 (东京23区)
            tokyo_wards = ['千代田区', '中央区', '港区', '新宿区', '文京区', '台東区', '墨田区',
                          '江東区', '品川区', '目黒区', '大田区', '世田谷区', '渋谷区', '中野区',
                          '杉並区', '豊島区', '北区', '荒川区', '板橋区', '練馬区', '足立区',
                          '葛飾区', '江戸川区']
            for ward in tokyo_wards:
                if ward in text:
                    data['city'] = ward
                    break

            return data

        except Exception as e:
            print(f"  基本搜索失败: {e}")
            return None

    def get_detail_info(self):
        """第二阶段：点击详细页面获取更多信息"""
        try:
            # 查找并点击详细按钮/链接
            detail_btn = self.page.locator('a:has-text("詳細"), button:has-text("詳細")').first
            if detail_btn.count() == 0:
                # 尝试其他选择器
                detail_btn = self.page.locator('text=詳細').first

            if detail_btn.count() == 0:
                # 尝试点击物件行
                detail_btn = self.page.locator('tr.clickable, tr[onclick]').first

            if detail_btn.count() > 0:
                detail_btn.click()
                time.sleep(2)

                # 提取详细信息
                text = self.page.locator('body').inner_text()
                detail_data = {}

                # 提取完整所在地 (必须包含都道府県)
                m = re.search(r'所在地[：:\s]*(東京都[^\n\t]+)', text)
                if not m:
                    m = re.search(r'所在地[：:\s]*([^\n\t]*[都道府県][^\n\t]+)', text)
                if m:
                    address = m.group(1).strip()
                    # 确保地址合理（至少包含区/市）
                    if re.search(r'[区市町村]', address):
                        detail_data['address'] = address
                        # 提取区
                        m2 = re.search(r'([\u4e00-\u9fa5]+区)', address)
                        if m2:
                            detail_data['city'] = m2.group(1)

                # 提取楼层
                m = re.search(r'(\d+)\s*階[/／](\d+)\s*階建', text)
                if m:
                    detail_data['floor'] = int(m.group(1))
                    detail_data['total_floors'] = int(m.group(2))
                else:
                    m = re.search(r'所在階[：:\s]*(\d+)', text)
                    if m:
                        detail_data['floor'] = int(m.group(1))

                # 提取朝向
                m = re.search(r'方位[：:\s]*([東西南北]+)', text)
                if not m:
                    m = re.search(r'向き[：:\s]*([東西南北]+)', text)
                if m:
                    detail_data['direction'] = m.group(1)

                # 提取間取り（更详细）
                m = re.search(r'間取り[：:\s]*([^\n\s]+)', text)
                if m:
                    detail_data['floor_plan'] = m.group(1).strip()

                # 提取管理费
                m = re.search(r'管理費[：:\s]*(\d+(?:,\d+)?)\s*円', text)
                if m:
                    detail_data['management_fee'] = int(m.group(1).replace(',', ''))

                # 提取敷金
                m = re.search(r'敷金[：:\s]*(\d+(?:\.\d+)?)\s*[ヶヵ]?月?', text)
                if m:
                    detail_data['deposit'] = m.group(1)

                # 提取礼金
                m = re.search(r'礼金[：:\s]*(\d+(?:\.\d+)?)\s*[ヶヵ]?月?', text)
                if m:
                    detail_data['key_money'] = m.group(1)

                # 提取建物构造
                m = re.search(r'構造[：:\s]*([^\n\s]+)', text)
                if m:
                    detail_data['structure'] = m.group(1).strip()

                # 返回搜索页面
                self.page.go_back()
                time.sleep(1)
                self.page.go_back()
                time.sleep(1)

                return detail_data

            return {}

        except Exception as e:
            print(f"  获取详细信息失败: {e}")
            # 尝试返回
            try:
                self.page.go_back()
                time.sleep(1)
            except:
                pass
            return {}

    def go_back_to_search(self):
        """返回搜索页面"""
        try:
            self.page.go_back()
            time.sleep(1)
        except:
            pass


# 朝向编码
DIRECTION_MAP = {
    '南': 3, '南東': 2, '南西': 2,
    '東': 1, '西': 1,
    '北東': 0, '北西': 0, '北': 0
}


def prepare_features(data):
    """准备模型特征 (v4 - 支持扩展特征)"""
    rent = data.get('rent', 80000)
    area_sqm = data.get('area_sqm', 25)
    built_year = data.get('built_year', 2010)
    walk_minutes = data.get('walk_minutes', 10)
    city = data.get('city', '')
    floor_plan = data.get('floor_plan', '1K')

    # 新增特征
    management_fee = data.get('management_fee', 0)
    deposit = data.get('deposit', 1.0)
    key_money = data.get('key_money', 1.0)
    floor = data.get('floor', 5)
    direction = data.get('direction', '')

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

    rent_per_sqm = rent / area_sqm if area_sqm > 0 else 0
    age = 2025 - built_year

    # 总租金 (賃料 + 管理費)
    total_rent = rent + management_fee

    # 朝向编码
    direction_encoded = DIRECTION_MAP.get(direction, 1)

    # 零礼金/零敷金标志
    zero_deposit = 1 if deposit == 0 else 0
    zero_key_money = 1 if key_money == 0 else 0

    # 区域热度三档
    high_heat_areas = config.get('high_heat_areas', [
        '江戸川区', '新宿区', '品川区', '目黒区', '中野区', '豊島区', '渋谷区'
    ])
    mid_heat_areas = config.get('mid_heat_areas', [
        '大田区', '北区', '世田谷区', '板橋区'
    ])

    if city in high_heat_areas:
        heat_level = 2
    elif city in mid_heat_areas:
        heat_level = 1
    else:
        heat_level = 0

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

    # 特征列表 (12个原有特征 - 与v1模型兼容)
    features = [
        rent, area_sqm, built_year, walk_minutes,
        city_encoded, heat_level, rent_per_sqm, age,
        walk_level, plan_type, rent_level, area_level
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
    print("REINS物件反响数预测 V2 - 两阶段筛选")
    print(f"阈值: 得分>{SCORE_THRESHOLD}分时进入详细页面")
    print("=" * 60)

    # 初始化
    notion = NotionClient(NOTION_API_KEY)

    # 读取未评分物件映射
    print("\n读取未评分物件列表...")
    bukken_map = {}
    with open('data/unscored_pages_new.txt', 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '|' in line:
                parts = line.split('|')
                bukken_number = parts[0]
                page_id = parts[1]
                if bukken_number:
                    bukken_map[bukken_number] = page_id

    bukken_list = list(bukken_map.items())[:200]
    print(f"未评分物件: {len(bukken_map)} 个, 本次处理: {len(bukken_list)} 个")

    # 启动爬虫
    scraper = ReinsScraper(headless=False)
    results = []
    detail_count = 0

    try:
        scraper.start()
        if not scraper.login():
            print("登录失败")
            return

        if not scraper.goto_bukken_search():
            print("进入物件番号検索失败")
            return

        success_count = 0
        for i, (bukken_number, page_id) in enumerate(bukken_list):
            print(f"\n[{i+1}/{len(bukken_list)}] 处理: {bukken_number}")

            # 第一阶段：快速提取基本信息
            data = scraper.search_bukken_basic(bukken_number)

            if data and data.get('rent'):
                # 初步预测
                score1 = predict_response(data)
                print(f"  初步: ¥{data.get('rent'):,}, {data.get('area_sqm')}㎡ → 得分: {score1:.1f}")

                # 第二阶段：得分超过阈值时获取详细信息
                if score1 > SCORE_THRESHOLD:
                    print(f"  → 得分>{SCORE_THRESHOLD}，进入详细页面...")
                    detail_data = scraper.get_detail_info()

                    if detail_data:
                        data.update(detail_data)
                        score2 = predict_response(data)
                        print(f"  详细: {data.get('city', '')} {data.get('floor_plan', '')} → 新得分: {score2:.1f}")
                        data['predicted_response'] = score2
                        detail_count += 1
                    else:
                        data['predicted_response'] = score1
                        scraper.go_back_to_search()
                else:
                    data['predicted_response'] = score1
                    scraper.go_back_to_search()

                # 更新Notion
                try:
                    update_props = {
                        "予測_反響数": {"number": data['predicted_response']}
                    }

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
                    if data.get('city') or data.get('address'):
                        address = data.get('address', data.get('city', ''))
                        update_props["所在地"] = {"rich_text": [{"text": {"content": address}}]}

                    # 新增字段: 管理費, 楼層, 敷金, 礼金, 朝向
                    if data.get('management_fee'):
                        # 管理費从円转换为万円
                        update_props["管理費(万)"] = {"number": round(data['management_fee'] / 10000, 2)}
                    if data.get('floor'):
                        update_props["建物_所在階"] = {"number": data['floor']}
                    if data.get('total_floors'):
                        update_props["建物_地上階層"] = {"number": data['total_floors']}
                    if data.get('deposit'):
                        # 敷金已经是月数
                        deposit_val = float(data['deposit']) if isinstance(data['deposit'], str) else data['deposit']
                        update_props["敷金(ヶ月)"] = {"number": deposit_val}
                    if data.get('key_money'):
                        # 礼金已经是月数
                        key_money_val = float(data['key_money']) if isinstance(data['key_money'], str) else data['key_money']
                        update_props["礼金(ヶ月)"] = {"number": key_money_val}
                    if data.get('direction'):
                        update_props["建物_バルコニー方向"] = {"rich_text": [{"text": {"content": data['direction']}}]}

                    result = notion.update_page(page_id, update_props)
                    if "id" in result:
                        print(f"  ✓ Notion更新成功")
                        success_count += 1
                    else:
                        print(f"  ✗ Notion更新失败")
                except Exception as e:
                    print(f"  ✗ Notion更新异常: {e}")

                results.append(data)
            else:
                print(f"  ✗ 未能获取物件数据，标记为-1")
                # 更新Notion，标记物件不存在
                try:
                    update_props = {
                        "予測_view数": {"number": -1}  # -1表示物件不存在
                    }
                    result = notion.update_page(page_id, update_props)
                    if "id" in result:
                        print(f"  ✓ 已标记为-1")
                    else:
                        print(f"  ✗ 标记失败")
                except Exception as e:
                    print(f"  ✗ 标记异常: {e}")
                scraper.go_back_to_search()

            time.sleep(0.5)

        print(f"\n{'='*60}")
        print(f"完成!")
        print(f"成功更新: {success_count}/{len(bukken_list)} 个物件")
        print(f"进入详细页面: {detail_count} 个 (得分>{SCORE_THRESHOLD})")
        print(f"快速跳过: {success_count - detail_count} 个 (得分≤{SCORE_THRESHOLD})")

        if results:
            df = pd.DataFrame(results)
            df.to_csv('data/notion_predictions_v2.csv', index=False, encoding='utf-8-sig')
            print(f"结果已保存: data/notion_predictions_v2.csv")

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        scraper.stop()


if __name__ == "__main__":
    main()
