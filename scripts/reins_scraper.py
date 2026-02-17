"""
REINS 物件搜索爬虫
根据物件番号从REINS获取物件详情
"""
import os
import sys
import time
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

REINS_URL = "https://system.reins.jp/login/main/KG/GKG001200"
REINS_USERNAME = os.getenv("REINS_USERNAME")
REINS_PASSWORD = os.getenv("REINS_PASSWORD")


class ReinsScraper:
    def __init__(self, headless=False):
        self.headless = headless
        self.browser = None
        self.page = None

    def start(self):
        """启动浏览器"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            locale='ja-JP',
        )
        self.page = self.context.new_page()
        print("浏览器启动")

    def stop(self):
        """关闭浏览器"""
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright'):
            self.playwright.stop()
        print("浏览器关闭")

    def login(self):
        """登录REINS"""
        print(f"登录REINS...")
        self.page.goto(REINS_URL, wait_until='networkidle')
        time.sleep(2)

        # 输入用户名密码
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

            # 点击登录
            login_btn = self.page.query_selector('button:has-text("ログイン")')
            if login_btn:
                login_btn.click(force=True)
                time.sleep(3)

            print(f"登录成功，当前URL: {self.page.url}")
            return True
        return False

    def goto_bukken_search(self):
        """进入物件番号検索页面"""
        print("进入物件番号検索...")
        bukken_link = self.page.locator('text=物件番号検索').first
        if bukken_link.count() > 0:
            bukken_link.click()
            time.sleep(2)
            print(f"当前URL: {self.page.url}")
            return True
        return False

    def search_bukken(self, bukken_number):
        """搜索物件番号并获取详情"""
        try:
            # 找到输入框并输入物件番号
            # 物件番号输入框通常是第一个text输入框
            inputs = self.page.query_selector_all('input[type="text"]')

            # 清空并输入物件番号
            for inp in inputs:
                placeholder = inp.get_attribute('placeholder') or ''
                # 找物件番号输入框
                inp.fill('')

            # 假设第一个输入框是物件番号
            if inputs:
                inputs[0].fill(bukken_number)
                time.sleep(0.3)

            # 点击检索按钮
            search_btn = self.page.locator('button:has-text("検索")').first
            if search_btn.count() > 0:
                search_btn.click()
                time.sleep(2)

            # 提取搜索结果
            data = self._extract_bukken_data(bukken_number)

            # 返回搜索页面
            self.page.go_back()
            time.sleep(1)

            return data

        except Exception as e:
            print(f"  搜索 {bukken_number} 失败: {e}")
            return None

    def _extract_bukken_data(self, bukken_number):
        """从结果页面提取物件数据"""
        data = {'bukken_number': bukken_number}

        try:
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

            # 提取所在地
            m = re.search(r'所在地[：:\s]*([^\n]+)', text)
            if m:
                data['address'] = m.group(1).strip()

            # 提取楼层
            m = re.search(r'(\d+)\s*階[/／](\d+)\s*階建', text)
            if m:
                data['floor'] = int(m.group(1))
                data['total_floors'] = int(m.group(2))

            # 提取朝向
            m = re.search(r'方位[：:\s]*([東西南北]+)', text)
            if m:
                data['direction'] = m.group(1)

        except Exception as e:
            print(f"  提取数据失败: {e}")

        return data

    def scrape_multiple(self, bukken_numbers, max_count=None):
        """批量搜索物件"""
        results = []

        if max_count:
            bukken_numbers = bukken_numbers[:max_count]

        print(f"\n开始搜索 {len(bukken_numbers)} 个物件...")

        for i, num in enumerate(bukken_numbers):
            print(f"[{i+1}/{len(bukken_numbers)}] 搜索: {num}")
            data = self.search_bukken(num)
            if data:
                results.append(data)
                print(f"  租金: {data.get('rent', 'N/A')}, 面积: {data.get('area_sqm', 'N/A')}")
            time.sleep(0.5)

        return results


def main():
    os.chdir(r"D:\Fango Ads")

    # 读取物件番号
    with open('data/notion_bukken_numbers.txt', 'r') as f:
        bukken_numbers = [line.strip() for line in f if line.strip()]

    print(f"从Notion获取 {len(bukken_numbers)} 个物件番号")

    scraper = ReinsScraper(headless=False)

    try:
        scraper.start()
        if scraper.login():
            if scraper.goto_bukken_search():
                # 先测试10个
                results = scraper.scrape_multiple(bukken_numbers, max_count=10)

                if results:
                    df = pd.DataFrame(results)
                    df.to_csv('data/reins_test_results.csv', index=False, encoding='utf-8-sig')
                    print(f"\n结果已保存: data/reins_test_results.csv")
                    print(df)

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        scraper.stop()


if __name__ == "__main__":
    main()
