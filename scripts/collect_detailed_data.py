"""
收集物件数据 - 从列表页提取完整信息
字段: 賃料, 管理費, 敷金, 礼金, 面積, 間取り, 築年月, 沿線/駅, 徒歩, 物件名, 建物種別
基于现有SummoScraper扩展，高效爬取（无需点击详情页）
"""
import os
import sys
import time
import re
import random
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(r"D:\Fango Ads")

from scraper.scraper import SummoScraper
import pandas as pd

# 目标数量
TARGET_COUNT = 5000

# 东京23区 (优先高热度区)
AREAS = [
    "新宿区", "渋谷区", "品川区", "目黒区", "中野区", "豊島区", "江戸川区",  # 高热度
    "大田区", "北区", "世田谷区", "板橋区",  # 中热度
    "千代田区", "中央区", "港区", "文京区", "台東区", "墨田区", "江東区",
    "杉並区", "荒川区", "練馬区", "足立区", "葛飾区"
]


class DetailedDataCollector(SummoScraper):
    """收集详细物件数据 - 包括管理費、敷金、礼金、楼層、朝向"""

    def __init__(self, target_count=TARGET_COUNT):
        super().__init__(headless=False)
        self.target_count = target_count
        self.all_data = []
        self.checkpoint_file = "data/detailed_properties_checkpoint.csv"
        self.output_file = "data/detailed_properties.csv"

    def scrape_property_with_detail(self, area_name: str):
        """爬取物件列表并获取详细信息"""
        properties = []
        try:
            self._random_delay(1, 2)
            frames = self.page.frames

            # 在所有frame中查找物件表格
            for frame in frames:
                try:
                    tables = frame.query_selector_all('table')
                    if not tables:
                        continue

                    for table in tables:
                        rows = table.query_selector_all('tr')
                        if not rows or len(rows) < 2:
                            continue

                        header_row = rows[0]
                        header_text = header_row.inner_text() if header_row else ""

                        # 判断是否是物件列表表格
                        if not any(kw in header_text for kw in ['推定反響', '賃料', '物件']):
                            continue

                        print(f"    找到 {len(rows)-1} 个物件行")

                        # 遍历数据行
                        for row_idx, row in enumerate(rows[1:], start=1):
                            if len(self.all_data) >= self.target_count:
                                return properties

                            try:
                                row_text = row.inner_text()
                                if len(row_text) < 20:
                                    continue

                                # 必须有万円才是有效物件行
                                if '万円' not in row_text:
                                    continue

                                # 提取基本信息（包括管理費、敷金、礼金等，都从列表页提取）
                                prop = self._extract_basic_info(row_text, area_name)
                                if not prop:
                                    continue

                                # 保存所有有基本信息的物件
                                properties.append(prop)
                                self.all_data.append(prop)

                                if len(self.all_data) % 20 == 0:
                                    print(f"      进度: {len(self.all_data)}/{self.target_count}")
                                    self._save_checkpoint()

                            except Exception as e:
                                continue

                        if properties:
                            break
                    if properties:
                        break
                except Exception as e:
                    continue

        except Exception as e:
            print(f"爬取物件列表失败: {e}")

        return properties

    def _extract_basic_info(self, row_text, area_name):
        """从行文本提取基本信息（包括管理費、敷金、礼金）"""
        prop = {
            'area_name': area_name,
            'address_city': area_name,
            'scraped_at': datetime.now().isoformat()
        }

        # 推定反響数 (可选)
        if '10件以上' in row_text:
            prop['estimated_response'] = 10.0
        else:
            m = re.search(r'([\d.]+)\s*件[/／]月', row_text)
            if m:
                prop['estimated_response'] = float(m.group(1))

        # 賃料 (必须)
        m = re.search(r'(\d+(?:\.\d+)?)\s*万円', row_text)
        if m:
            prop['rent'] = int(float(m.group(1)) * 10000)
        else:
            return None  # 没有租金则跳过

        # 管理費 - 在賃料(万円)后面的下一行
        m = re.search(r'万円\s*[\n\r]+(\d+)円?', row_text)
        if m:
            prop['management_fee'] = int(m.group(1))
        else:
            # 也可能是―表示无管理费
            m = re.search(r'万円\s*[\n\r]+―', row_text)
            if m:
                prop['management_fee'] = 0

        # 礼金/敷金 - 在管理費后面
        # 格式1: 万円\n管理費\t礼金(月数)\n敷金(月数)  例: ―\t1ヶ月\n1ヶ月
        # 格式2: 万円\n管理費\t礼金(金額)\n敷金(金額)  例: 2000円\t8.5万円\n8.5万円
        # 匹配: 月数(Xヶ月)、金额(X万円)、无(―)
        m = re.search(r'万円\s*[\n\r]+[―\d]+(?:円)?\s+([\d.]+ヶ月|[\d.]+万円|―)\s*[\n\r]+([\d.]+ヶ月|[\d.]+万円|―)', row_text)
        if m:
            # 礼金
            key_money = m.group(1)
            if key_money == '―':
                prop['key_money'] = 0
                prop['key_money_type'] = 'none'
            elif 'ヶ月' in key_money:
                km = re.search(r'([\d.]+)', key_money)
                if km:
                    prop['key_money'] = float(km.group(1))
                    prop['key_money_type'] = 'month'
            elif '万円' in key_money:
                km = re.search(r'([\d.]+)', key_money)
                if km:
                    prop['key_money'] = float(km.group(1)) * 10000  # 转换为円
                    prop['key_money_type'] = 'yen'

            # 敷金
            deposit = m.group(2)
            if deposit == '―':
                prop['deposit'] = 0
                prop['deposit_type'] = 'none'
            elif 'ヶ月' in deposit:
                dm = re.search(r'([\d.]+)', deposit)
                if dm:
                    prop['deposit'] = float(dm.group(1))
                    prop['deposit_type'] = 'month'
            elif '万円' in deposit:
                dm = re.search(r'([\d.]+)', deposit)
                if dm:
                    prop['deposit'] = float(dm.group(1)) * 10000  # 转换为円
                    prop['deposit_type'] = 'yen'

        # 面積
        m = re.search(r'([\d.]+)\s*㎡', row_text)
        if m:
            prop['area_sqm'] = float(m.group(1))

        # 間取り (支持ワンルーム)
        m = re.search(r'(\d[SLKDR]+|ワンルーム)', row_text)
        if m:
            prop['floor_plan'] = m.group(1)

        # 建物種別
        for btype in ['マンション', 'アパート', '一戸建て', 'テラスハウス']:
            if btype in row_text:
                prop['property_type'] = btype
                break

        # 築年月
        m = re.search(r"'(\d{2})/(\d{1,2})", row_text)
        if m:
            y = int(m.group(1))
            prop['built_year'] = (1900 + y) if y > 50 else (2000 + y)
            prop['built_month'] = int(m.group(2))

        # 沿線/駅
        m = re.search(r'([^\s]+線)[/／]([^\s\n]+)', row_text)
        if m:
            prop['railway_line'] = m.group(1)
            prop['station'] = m.group(2).replace('駅', '')

        # 徒歩
        m = re.search(r'(\d+)\s*分', row_text)
        if m:
            prop['walk_minutes'] = int(m.group(1))

        # 物件名・号室
        # 策略1: 先匹配・分隔格式 (最常见)
        m = re.search(r'([^\n\t]+)・(\d+号室)', row_text)
        if m:
            prop['property_name'] = m.group(1).strip()
            prop['room_number'] = m.group(2)
        else:
            # 策略2: 匹配空格分隔格式，但必须包含建筑关键词
            building_keywords = ['マンション', 'ハイツ', 'コーポ', 'ハウス', '荘', 'ビル',
                               'アパート', 'レジデンス', 'パレス', 'メゾン', 'コート',
                               'ガーデン', 'プラザ', 'タワー', 'ヴィラ', 'シャトー', 'グランド']
            m = re.search(r'([^\n\t]+?)[　\s]+(\d+号室)', row_text)
            if m:
                name = m.group(1).strip()
                if any(kw in name for kw in building_keywords):
                    prop['property_name'] = name
                    prop['room_number'] = m.group(2)

        return prop

    def _save_checkpoint(self):
        """保存检查点"""
        if not self.all_data:
            return

        df = pd.DataFrame(self.all_data)
        df.to_csv(self.checkpoint_file, index=False, encoding='utf-8-sig')
        print(f"    [检查点] 已保存 {len(df)} 件")

    def collect_all(self):
        """收集所有区域的数据"""
        print(f"\n目标: {self.target_count} 件物件")
        print(f"区域数: {len(AREAS)}")

        per_area = (self.target_count // len(AREAS)) + 10
        print(f"每区目标: ~{per_area} 件\n")

        for area_idx, area in enumerate(AREAS):
            if len(self.all_data) >= self.target_count:
                print(f"\n已达到目标 {self.target_count} 件")
                break

            print(f"\n[{area_idx+1}/{len(AREAS)}] {area} (累计: {len(self.all_data)})")

            # 重新导航
            if area_idx > 0:
                if not self.navigate_to_property_search():
                    print(f"  导航失败，跳过")
                    continue

            # 进入该区
            if not self.search_area(area):
                print(f"  无法进入，跳过")
                continue

            # 按反响数排序
            self.filter_by_response_count()

            # 爬取多页
            area_collected = 0
            page_num = 0
            max_pages = 5  # 每区最多5页

            while area_collected < per_area and page_num < max_pages:
                page_num += 1
                print(f"  第{page_num}页...")

                properties = self.scrape_property_with_detail(area)

                if not properties:
                    print(f"    无物件")
                    break

                area_collected += len(properties)
                print(f"    获取: {len(properties)} 件")

                # 翻页
                if area_collected < per_area and self._has_next_page():
                    if not self._goto_next_page():
                        break
                else:
                    break

            print(f"  本区总计: {area_collected} 件")
            self._save_checkpoint()

        print(f"\n收集完成! 总计: {len(self.all_data)} 件")

    def save(self):
        """保存最终数据"""
        if not self.all_data:
            print("无数据可保存")
            return

        df = pd.DataFrame(self.all_data)

        # 如果文件存在,追加并去重
        if os.path.exists(self.output_file):
            df_existing = pd.read_csv(self.output_file)
            df = pd.concat([df_existing, df], ignore_index=True)
            df = df.drop_duplicates(
                subset=['area_name', 'rent', 'area_sqm', 'floor_plan'],
                keep='last'
            )

        df.to_csv(self.output_file, index=False, encoding='utf-8-sig')
        print(f"\n已保存到: {self.output_file}")
        print(f"总记录数: {len(df)}")

        # 统计字段覆盖率
        print(f"\n字段覆盖率:")
        for col in ['management_fee', 'deposit', 'key_money', 'property_type', 'property_name']:
            if col in df.columns:
                non_null = df[col].notna().sum()
                pct = non_null / len(df) * 100
                print(f"  {col}: {non_null}/{len(df)} ({pct:.1f}%)")


def main():
    print("=" * 60)
    print("收集物件数据 (賃料, 管理費, 敷金, 礼金, 間取り, 築年等)")
    print("=" * 60)

    collector = DetailedDataCollector(target_count=TARGET_COUNT)

    try:
        collector.start()

        if not collector.login():
            print("登录失败")
            return

        if not collector.navigate_to_property_search():
            print("导航失败")
            return

        collector.collect_all()
        collector.save()

    except KeyboardInterrupt:
        print("\n\n用户中断,保存已收集数据...")
        collector.save()
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        collector.save()
    finally:
        collector.stop()


if __name__ == "__main__":
    main()
