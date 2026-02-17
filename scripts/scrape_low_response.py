"""
爬取低反響数物件作为对比样本
复用原scraper的导航逻辑，但不过滤反響数
"""
import os
import sys
import re
import time
import random
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.scraper import SummoScraper


class LowResponseScraper(SummoScraper):
    """继承原scraper，修改数据提取逻辑"""

    def __init__(self):
        super().__init__(headless=False)
        self.all_properties = []

    def scrape_setagaya_all(self, target_count=200):
        """爬取世田谷区所有物件（不限制反響数）"""
        print(f"\n目标: 爬取世田谷区约 {target_count} 个物件")

        # 点击世田谷区
        if not self.search_area("世田谷区"):
            print("无法进入世田谷区")
            return

        page_num = 0
        while len(self.all_properties) < target_count and page_num < 10:
            page_num += 1
            print(f"\n第 {page_num} 页...")

            # 爬取当前页
            props = self._scrape_current_page()
            self.all_properties.extend(props)
            print(f"  本页: {len(props)} 个, 累计: {len(self.all_properties)} 个")

            # 翻页
            if len(self.all_properties) < target_count:
                if not self._has_next_page() or not self._goto_next_page():
                    print("  没有更多页面")
                    break
                self._random_delay(2, 3)

        print(f"\n爬取完成: {len(self.all_properties)} 个物件")

    def _scrape_current_page(self):
        """爬取当前页面所有物件（不过滤反響数）"""
        properties = []
        frames = self.page.frames

        for frame in frames:
            try:
                tables = frame.query_selector_all('table')
                for table in tables:
                    rows = table.query_selector_all('tr')
                    if len(rows) < 2:
                        continue

                    header = rows[0].inner_text() if rows[0] else ""
                    if '賃料' not in header and '推定反響' not in header:
                        continue

                    for row in rows[1:]:
                        data = self._extract_row_data(row)
                        if data:
                            properties.append(data)

                    if properties:
                        break
                if properties:
                    break
            except:
                continue

        return properties

    def _extract_row_data(self, row):
        """提取行数据 - 不过滤反響数"""
        try:
            text = row.inner_text()
            if len(text) < 10:
                return None

            data = {
                'area_name': '世田谷区',
                'address_prefecture': '東京都',
                'address_city': '世田谷区',
            }

            # 推定反響数 - 保留所有值
            if '10件以上' in text:
                data['estimated_response'] = 10.0
            else:
                m = re.search(r'([\d.]+)\s*件[/／]月', text)
                if m:
                    data['estimated_response'] = float(m.group(1))
                else:
                    data['estimated_response'] = 0  # 没有反響数也保留

            # 賃料
            m = re.search(r'(\d+(?:\.\d+)?)\s*万円', text)
            if m:
                data['rent'] = int(float(m.group(1)) * 10000)

            # 面積
            m = re.search(r'([\d.]+)\s*㎡', text)
            if m:
                data['area_sqm'] = float(m.group(1))

            # 間取り
            m = re.search(r'([1-9][SLKDR]+)', text)
            if m:
                data['floor_plan'] = m.group(1)

            # 築年
            m = re.search(r"'(\d{2})/", text)
            if m:
                y = int(m.group(1))
                data['built_year'] = (1900 + y) if y > 50 else (2000 + y)

            # 沿線/駅
            m = re.search(r'([^\s]+線)[/／]([^\s\n]+)', text)
            if m:
                data['railway_line'] = m.group(1)
                data['station'] = m.group(2).replace('駅', '')

            # 徒歩
            m = re.search(r'(\d+)\s*分', text)
            if m:
                data['walk_minutes'] = int(m.group(1))

            # 物件类型
            for pt in ['マンション', 'アパート', '一戸建て']:
                if pt in text:
                    data['property_type'] = pt
                    break

            data['raw_data'] = text[:200]
            return data
        except:
            return None

    def save_csv(self):
        """保存CSV"""
        import pandas as pd
        df = pd.DataFrame(self.all_properties)
        df.to_csv('data/low_response_properties.csv', index=False, encoding='utf-8-sig')
        print(f"\n保存到: data/low_response_properties.csv")
        print(f"\n反響数分布:")
        print(df['estimated_response'].value_counts().sort_index())


def main():
    os.chdir(r"D:\Fango Ads")
    scraper = LowResponseScraper()

    try:
        scraper.start()
        if scraper.login():
            if scraper.navigate_to_property_search():
                scraper.scrape_setagaya_all(200)
                scraper.save_csv()
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        scraper.stop()


if __name__ == "__main__":
    main()
