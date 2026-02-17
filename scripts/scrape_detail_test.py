"""
测试脚本：访问物件详细页面抓取数据
抓取50条数据，记录时间，分析内容
"""
import os
import sys
import re
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.scraper import SummoScraper
import pandas as pd


class DetailScraper(SummoScraper):
    """访问详细页面的爬虫"""

    def __init__(self):
        super().__init__(headless=False)
        self.all_data = []
        self.timing = {
            'list_page': [],      # 列表页抓取时间
            'detail_page': [],    # 详细页抓取时间
            'navigation': [],     # 导航时间
        }

    def scrape_with_details(self, max_count=50):
        """抓取物件数据（包括详细页面）"""
        areas = self.get_tokyo_areas()[:3]  # 只测试前3个区

        print(f"\n测试抓取 {max_count} 条数据（访问详细页面）")
        print(f"测试区域: {areas}")

        start_time = time.time()

        for idx, area in enumerate(areas):
            if len(self.all_data) >= max_count:
                break

            print(f"\n[{idx+1}/{len(areas)}] {area}")

            # 导航计时
            nav_start = time.time()
            if idx > 0:
                if not self.navigate_to_property_search():
                    print(f"  导航失败，跳过")
                    continue

            if not self.search_area(area):
                print(f"  无法进入，跳过")
                continue
            self.timing['navigation'].append(time.time() - nav_start)

            # 抓取列表页并访问详细页
            props = self._scrape_with_detail_pages(area, max_count - len(self.all_data))
            self.all_data.extend(props)

            print(f"  获取: {len(props)} 条, 累计: {len(self.all_data)} 条")

        total_time = time.time() - start_time
        self._print_timing_report(total_time)

    def _scrape_with_detail_pages(self, area, remaining):
        """抓取列表页并访问每个物件的详细页面"""
        props = []
        row_idx = 0

        while len(props) < remaining:
            # 每次循环重新获取main frame和表格
            main_frame = None
            for frame in self.page.frames:
                if frame.name == 'main':
                    main_frame = frame
                    break

            if not main_frame:
                print("  未找到main frame")
                break

            try:
                # 找物件表格
                tables = main_frame.query_selector_all('table')
                target_table = None
                for table in tables:
                    rows = table.query_selector_all('tr')
                    if len(rows) < 2:
                        continue
                    header = rows[0].inner_text() if rows[0] else ""
                    if '賃料' in header or '推定反響' in header:
                        target_table = table
                        break

                if not target_table:
                    print("  未找到物件表格")
                    break

                rows = target_table.query_selector_all('tr')
                total_rows = len(rows) - 1  # 减去表头

                if row_idx == 0:
                    print(f"  找到物件表格，共 {total_rows} 行")

                if row_idx >= total_rows:
                    print(f"  已抓完本区所有 {total_rows} 条")
                    break

                # 获取当前行
                row = rows[row_idx + 1]  # +1跳过表头

                # 1. 从列表页提取基本数据
                list_start = time.time()
                data = self._extract_list_data(row, area)
                self.timing['list_page'].append(time.time() - list_start)

                if not data:
                    row_idx += 1
                    continue

                # 2. 点击进入详细页面
                detail_start = time.time()
                detail_data = self._scrape_detail_page(main_frame, row, row_idx)
                self.timing['detail_page'].append(time.time() - detail_start)

                if detail_data:
                    data.update(detail_data)

                props.append(data)
                floor_info = data.get('floor', 'N/A')
                dir_info = data.get('direction', 'N/A')
                print(f"    [{len(props)}/{remaining}] 租金:{data.get('rent', 'N/A')} "
                      f"楼层:{floor_info} 朝向:{dir_info}")

                row_idx += 1

            except Exception as e:
                print(f"    行{row_idx}处理错误: {e}")
                row_idx += 1
                continue

        return props

    def _extract_list_data(self, row, area):
        """从列表行提取基本数据"""
        try:
            text = row.inner_text()
            if len(text) < 10:
                return None

            data = {
                'area_name': area,
                'address_city': area,
                'scraped_at': datetime.now().isoformat(),
                'raw_text': text[:200],  # 保存原始文本用于分析
            }

            # 推定反響数
            if '10件以上' in text:
                data['estimated_response'] = 10.0
            else:
                m = re.search(r'([\d.]+)\s*件[/／]月', text)
                if m:
                    data['estimated_response'] = float(m.group(1))

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

    def _scrape_detail_page(self, frame, row, row_idx):
        """点击进入详细页面并抓取额外数据"""
        try:
            # 查找詳細链接（JavaScript调用）
            links = row.query_selector_all('a')
            detail_link = None

            for link in links:
                text = link.inner_text().strip()
                if '詳細' in text:
                    detail_link = link
                    break

            if not detail_link:
                return None

            # 点击詳細链接（在同一frame中加载）
            self._random_delay(0.2, 0.4)
            detail_link.click()
            self._random_delay(1.5, 2.0)

            # 抓取详细页面数据（从main frame）
            detail_data = self._extract_detail_data()

            # 返回列表页（点击返回按钮或浏览器后退）
            self._go_back_from_detail()

            return detail_data

        except Exception as e:
            # 出错时尝试返回
            try:
                self._go_back_from_detail()
            except:
                pass
            return None

    def _go_back_from_detail(self):
        """从详细页返回列表页"""
        try:
            # 尝试找返回按钮
            for frame in self.page.frames:
                try:
                    back_btn = frame.query_selector('a:has-text("戻る")')
                    if back_btn:
                        back_btn.click()
                        self._random_delay(1, 1.5)
                        return
                    # 或者找一覧按钮
                    list_btn = frame.query_selector('a:has-text("一覧")')
                    if list_btn:
                        list_btn.click()
                        self._random_delay(1, 1.5)
                        return
                except:
                    continue
            # 如果没找到，用浏览器后退
            self.page.go_back()
            self._random_delay(1, 1.5)
        except:
            pass

    def _extract_detail_data(self):
        """从详细页面提取数据"""
        detail = {}

        try:
            # 在main frame中查找
            for frame in self.page.frames:
                if frame.name != 'main':
                    continue
                try:
                    body = frame.locator('body')
                    if body.count() == 0:
                        continue
                    text = body.inner_text()

                    # 楼层信息: "階/階建	4階/11階建"
                    m = re.search(r'(\d+)階[/／](\d+)階建', text)
                    if m:
                        detail['floor'] = int(m.group(1))
                        detail['total_floors'] = int(m.group(2))

                    # 朝向信息: "方位	南西"
                    m = re.search(r'方位\s+([東西南北]+)', text)
                    if m:
                        detail['direction'] = m.group(1)

                    # 构造: "構造/総戸数	鉄骨鉄筋"
                    m = re.search(r'構造[/／]総戸数\s+([^\s\n]+)', text)
                    if m:
                        detail['structure'] = m.group(1)

                    # 间取详情: "間取り	洋5.5"
                    m = re.search(r'間取り\s+([^\n]+)', text)
                    if m:
                        detail['room_detail'] = m.group(1).strip()

                    # 设备/设施
                    facilities = []
                    facility_keywords = [
                        'エアコン', 'バストイレ別', '室内洗濯機', 'オートロック',
                        '宅配ボックス', 'フローリング', '追い焚き', '浴室乾燥機',
                        'インターネット', 'ペット可', '駐車場', 'エレベーター',
                        '2階以上', '角部屋', '南向き', '都市ガス',
                    ]
                    for keyword in facility_keywords:
                        if keyword in text:
                            facilities.append(keyword)
                    if facilities:
                        detail['facilities'] = ','.join(facilities)

                    # 保存部分原始文本用于分析
                    detail['detail_raw'] = text[:300]

                    if detail:
                        break

                except Exception as e:
                    continue

        except Exception as e:
            print(f"    详细页面解析错误: {e}")

        return detail if detail else None

    def _print_timing_report(self, total_time):
        """打印时间报告"""
        print(f"\n{'='*50}")
        print(f"抓取完成！共 {len(self.all_data)} 条数据")
        print(f"{'='*50}")

        print(f"\n时间统计:")
        print(f"  总耗时: {total_time:.1f} 秒")
        print(f"  平均每条: {total_time/max(len(self.all_data),1):.2f} 秒")

        if self.timing['list_page']:
            avg_list = sum(self.timing['list_page']) / len(self.timing['list_page'])
            print(f"  列表页提取: {avg_list*1000:.0f} 毫秒/条")

        if self.timing['detail_page']:
            avg_detail = sum(self.timing['detail_page']) / len(self.timing['detail_page'])
            print(f"  详细页抓取: {avg_detail:.2f} 秒/条")

        if self.timing['navigation']:
            avg_nav = sum(self.timing['navigation']) / len(self.timing['navigation'])
            print(f"  区域导航: {avg_nav:.1f} 秒/次")

        # 推算10000条时间
        if len(self.all_data) > 0:
            time_per_item = total_time / len(self.all_data)
            time_10000 = time_per_item * 10000
            print(f"\n推算抓取10000条:")
            print(f"  预计耗时: {time_10000/3600:.1f} 小时")
            print(f"  = {time_10000/60:.0f} 分钟")

    def save(self):
        """保存数据"""
        if not self.all_data:
            print("没有数据可保存")
            return

        df = pd.DataFrame(self.all_data)

        # 保存完整数据
        df.to_csv('data/detail_test_50.csv', index=False, encoding='utf-8-sig')
        print(f"\n数据已保存: data/detail_test_50.csv")

        # 分析抓取到的字段
        print(f"\n抓取到的字段统计:")
        print(f"{'字段':<20} {'有效数':>8} {'有效率':>10}")
        print("-" * 40)
        for col in df.columns:
            if col in ['raw_text', 'detail_raw', 'scraped_at']:
                continue
            valid = df[col].notna().sum()
            rate = valid / len(df) * 100
            print(f"{col:<20} {valid:>8} {rate:>9.1f}%")


def main():
    os.chdir(r"D:\Fango Ads")
    scraper = DetailScraper()

    try:
        scraper.start()
        if scraper.login():
            if scraper.navigate_to_property_search():
                scraper.scrape_with_details(max_count=50)
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
