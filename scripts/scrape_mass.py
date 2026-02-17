"""
大规模爬取 - 目标10000条，不限制反響数
"""
import os
import sys
import re
import random
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.scraper import SummoScraper
import pandas as pd


class MassScraper(SummoScraper):
    """大规模爬虫"""

    def __init__(self, target_count=10000):
        super().__init__(headless=False)
        self.target_count = target_count
        self.all_data = []

    def scrape_all(self):
        """爬取所有区域直到达到目标数量"""
        # 东京23区 + 主要市
        areas = [
            "千代田区", "中央区", "港区", "新宿区", "文京区",
            "台東区", "墨田区", "江東区", "品川区", "目黒区",
            "大田区", "世田谷区", "渋谷区", "中野区", "杉並区",
            "豊島区", "北区", "荒川区", "板橋区", "練馬区",
            "足立区", "葛飾区", "江戸川区",
            "八王子市", "立川市", "武蔵野市", "三鷹市", "府中市",
            "調布市", "町田市", "小金井市", "日野市", "国分寺市",
        ]

        print(f"目标: {self.target_count} 条记录")
        print(f"区域数: {len(areas)}")

        # 计算每区需要爬取的数量
        per_area = (self.target_count // len(areas)) + 50  # 多爬一点余量
        print(f"每区目标: ~{per_area} 条\n")

        for idx, area in enumerate(areas):
            if len(self.all_data) >= self.target_count:
                print(f"\n已达到目标 {self.target_count} 条，停止爬取")
                break

            print(f"\n[{idx+1}/{len(areas)}] {area} (累计: {len(self.all_data)})")

            # 重新导航
            if idx > 0:
                if not self.navigate_to_property_search():
                    print(f"  导航失败，跳过")
                    continue

            # 进入该区
            if not self.search_area(area):
                print(f"  无法进入，跳过")
                continue

            # 爬取该区数据
            area_data = self._scrape_area(area, per_area)
            self.all_data.extend(area_data)
            print(f"  本区获取: {len(area_data)}, 总计: {len(self.all_data)}")

            # 每5个区保存一次（防止中断丢失数据）
            if (idx + 1) % 5 == 0:
                self._save_checkpoint()

        print(f"\n爬取完成！总计: {len(self.all_data)} 条")

    def _scrape_area(self, area, max_count):
        """爬取单个区域"""
        data = []
        page = 0
        max_pages = (max_count // 50) + 2

        while len(data) < max_count and page < max_pages:
            page += 1
            page_data = self._scrape_page(area)

            if not page_data:
                break

            data.extend(page_data)
            print(f"    第{page}页: {len(page_data)}条")

            # 翻页
            if len(data) < max_count:
                if not self._has_next_page():
                    break
                if not self._goto_next_page():
                    break
                self._random_delay(1.5, 2.5)

        return data[:max_count]

    def _scrape_page(self, area):
        """爬取当前页"""
        data = []
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
                        item = self._extract(row, area)
                        if item:
                            data.append(item)
                    break
                if data:
                    break
            except:
                continue
        return data

    def _extract(self, row, area):
        """提取数据"""
        try:
            text = row.inner_text()
            if len(text) < 10:
                return None

            item = {
                'area_name': area,
                'address_city': area,
                'scraped_at': datetime.now().isoformat(),
            }

            # 推定反響数
            if '10件以上' in text:
                item['estimated_response'] = 10.0
            else:
                m = re.search(r'([\d.]+)\s*件[/／]月', text)
                item['estimated_response'] = float(m.group(1)) if m else 0

            # 賃料
            m = re.search(r'(\d+(?:\.\d+)?)\s*万円', text)
            if m:
                item['rent'] = int(float(m.group(1)) * 10000)

            # 面積
            m = re.search(r'([\d.]+)\s*㎡', text)
            if m:
                item['area_sqm'] = float(m.group(1))

            # 間取り
            m = re.search(r'([1-9][SLKDR]+)', text)
            if m:
                item['floor_plan'] = m.group(1)

            # 築年
            m = re.search(r"'(\d{2})/", text)
            if m:
                y = int(m.group(1))
                item['built_year'] = (1900 + y) if y > 50 else (2000 + y)

            # 沿線/駅
            m = re.search(r'([^\s]+線)[/／]([^\s\n]+)', text)
            if m:
                item['railway_line'] = m.group(1)
                item['station'] = m.group(2).replace('駅', '')

            # 徒歩
            m = re.search(r'(\d+)\s*分', text)
            if m:
                item['walk_minutes'] = int(m.group(1))

            # 物件类型
            for pt in ['マンション', 'アパート', '一戸建て']:
                if pt in text:
                    item['property_type'] = pt
                    break

            return item
        except:
            return None

    def _save_checkpoint(self):
        """保存检查点（追加模式）"""
        df_new = pd.DataFrame(self.all_data)
        checkpoint_path = 'data/mass_properties_checkpoint.csv'

        # 追加到现有检查点
        if os.path.exists(checkpoint_path):
            df_existing = pd.read_csv(checkpoint_path)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
        else:
            df_combined = df_new

        df_combined.to_csv(checkpoint_path, index=False, encoding='utf-8-sig')
        print(f"  [检查点] 本次 {len(df_new)} 条, 累计 {len(df_combined)} 条")

    def save(self):
        """保存最终数据（追加模式，不覆盖旧数据）"""
        df_new = pd.DataFrame(self.all_data)
        output_path = 'data/mass_properties.csv'

        # 如果文件存在，追加数据
        if os.path.exists(output_path):
            df_existing = pd.read_csv(output_path)
            print(f"\n现有数据: {len(df_existing)} 条")
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)

            # 去重（基于关键字段）
            before_dedup = len(df_combined)
            df_combined = df_combined.drop_duplicates(
                subset=['area_name', 'rent', 'area_sqm', 'built_year', 'floor_plan'],
                keep='last'
            )
            if before_dedup > len(df_combined):
                print(f"去重: {before_dedup} -> {len(df_combined)} 条")
        else:
            df_combined = df_new

        df_combined.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"\n已保存到: {output_path}")
        print(f"本次新增: {len(df_new)} 条")
        print(f"总记录数: {len(df_combined)} 条")
        print(f"\n反響数分布:")
        print(df_combined['estimated_response'].describe())
        print(f"\n各区数据量:")
        print(df_combined['area_name'].value_counts())


def main():
    os.chdir(r"D:\Fango Ads")
    scraper = MassScraper(target_count=10000)

    try:
        scraper.start()
        if scraper.login():
            if scraper.navigate_to_property_search():
                scraper.scrape_all()
                scraper.save()
    except KeyboardInterrupt:
        print("\n\n用户中断，保存已爬取的数据...")
        scraper.save()
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        print("\n保存已爬取的数据...")
        scraper.save()
    finally:
        scraper.stop()


if __name__ == "__main__":
    main()
