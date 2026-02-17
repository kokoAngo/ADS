"""
抽样爬取 - 每个区随机爬取5%的物件用于分析
"""
import os
import sys
import re
import random
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.scraper import SummoScraper
import pandas as pd


class SampleScraper(SummoScraper):
    """抽样爬虫 - 每个区爬取5%样本"""

    def __init__(self, sample_ratio=0.05):
        super().__init__(headless=False)
        self.sample_ratio = sample_ratio
        self.all_data = []

    def scrape_all_areas_sample(self):
        """爬取所有区的5%样本"""
        areas = self.get_tokyo_areas()
        print(f"\n共 {len(areas)} 个区域，每区抽取 {self.sample_ratio*100:.0f}%")

        for idx, area in enumerate(areas):
            print(f"\n[{idx+1}/{len(areas)}] {area}")

            # 每个区重新导航
            if idx > 0:
                if not self.navigate_to_property_search():
                    print(f"  导航失败，跳过")
                    continue

            # 进入该区
            if not self.search_area(area):
                print(f"  无法进入，跳过")
                continue

            # 获取总数并计算抽样数
            total = self._get_area_total()
            sample_count = max(10, int(total * self.sample_ratio))  # 最少10个
            print(f"  总数: {total}, 抽取: {sample_count}")

            # 爬取样本
            props = self._scrape_random_sample(area, sample_count, total)
            self.all_data.extend(props)
            print(f"  实际获取: {len(props)}, 累计: {len(self.all_data)}")

        print(f"\n爬取完成，共 {len(self.all_data)} 个物件")

    def _get_area_total(self):
        """获取当前区的物件总数"""
        for frame in self.page.frames:
            try:
                # 查找类似 "1-50件/1234件" 的文本
                text = frame.inner_text()
                match = re.search(r'/\s*(\d+)\s*件', text)
                if match:
                    return int(match.group(1))
            except:
                continue
        return 200  # 默认值

    def _scrape_random_sample(self, area, sample_count, total):
        """随机抽样爬取"""
        props = []
        pages_total = (total + 49) // 50  # 总页数

        if pages_total <= 1:
            # 只有一页，直接爬
            return self._scrape_page(area)

        # 随机选择要爬的页码
        pages_to_scrape = min(pages_total, (sample_count + 49) // 50 + 1)
        selected_pages = sorted(random.sample(range(1, pages_total + 1),
                                             min(pages_to_scrape, pages_total)))

        current_page = 1
        for target_page in selected_pages:
            # 跳转到目标页
            while current_page < target_page:
                if self._has_next_page():
                    self._goto_next_page()
                    self._random_delay(1, 2)
                    current_page += 1
                else:
                    break

            # 爬取当前页
            page_props = self._scrape_page(area)
            props.extend(page_props)

            if len(props) >= sample_count:
                break

        return props[:sample_count]

    def _scrape_page(self, area):
        """爬取当前页所有物件"""
        props = []
        for frame in self.page.frames:
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
                        data = self._extract_data(row, area)
                        if data:
                            props.append(data)
                    break
                if props:
                    break
            except:
                continue
        return props

    def _extract_data(self, row, area):
        """提取行数据"""
        try:
            text = row.inner_text()
            if len(text) < 10:
                return None

            data = {
                'area_name': area,
                'address_prefecture': '東京都',
                'address_city': area,
            }

            # 推定反響数
            if '10件以上' in text:
                data['estimated_response'] = 10.0
            else:
                m = re.search(r'([\d.]+)\s*件[/／]月', text)
                if m:
                    data['estimated_response'] = float(m.group(1))
                else:
                    data['estimated_response'] = 0

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

            return data
        except:
            return None

    def save(self):
        """保存数据"""
        df = pd.DataFrame(self.all_data)
        df.to_csv('data/sample_properties.csv', index=False, encoding='utf-8-sig')
        print(f"\n保存到: data/sample_properties.csv")
        print(f"反響数分布:")
        print(df['estimated_response'].value_counts().sort_index().head(20))


def main():
    os.chdir(r"D:\Fango Ads")
    scraper = SampleScraper(sample_ratio=0.05)

    try:
        scraper.start()
        if scraper.login():
            if scraper.navigate_to_property_search():
                scraper.scrape_all_areas_sample()
                scraper.save()
    except KeyboardInterrupt:
        print("\n用户中断，保存已爬取的数据...")
        scraper.save()
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        scraper.save()
    finally:
        scraper.stop()


if __name__ == "__main__":
    main()
