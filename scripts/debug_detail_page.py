"""
调试脚本：检查详细页面结构
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.scraper import SummoScraper


class DebugScraper(SummoScraper):
    """调试用爬虫"""

    def debug_detail_page(self):
        """调试详细页面"""
        print("\n===== 调试详细页面结构 =====")

        # 找到物件表格
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

                    print(f"找到物件表格，frame: {frame.name}")

                    # 只看第一行数据
                    row = rows[1]
                    print(f"\n第一行内容:")
                    print(row.inner_text()[:300])

                    # 查找所有链接
                    links = row.query_selector_all('a')
                    print(f"\n找到 {len(links)} 个链接:")
                    for i, link in enumerate(links):
                        href = link.get_attribute('href') or ''
                        text = link.inner_text().strip()
                        onclick = link.get_attribute('onclick') or ''
                        target = link.get_attribute('target') or ''
                        print(f"  [{i}] text='{text}' href='{href[:50]}...' onclick='{onclick[:50]}' target='{target}'")

                    # 尝试点击"詳細"链接
                    detail_link = None
                    for link in links:
                        text = link.inner_text().strip()
                        if '詳細' in text:
                            detail_link = link
                            break

                    if detail_link:
                        print(f"\n点击詳細链接...")

                        # 保存点击前的状态
                        self.page.screenshot(path="data/debug_before_click.png")

                        # 记录当前frame数量
                        frames_before = len(self.page.frames)

                        # 点击
                        detail_link.click()
                        time.sleep(3)

                        # 检查点击后的状态
                        frames_after = len(self.page.frames)
                        print(f"Frame数量变化: {frames_before} -> {frames_after}")

                        # 保存点击后的截图
                        self.page.screenshot(path="data/debug_after_click.png")

                        # 检查所有frame的内容
                        print(f"\n点击后的frame内容:")
                        for i, f in enumerate(self.page.frames):
                            try:
                                # 使用locator获取body文本
                                body = f.locator('body')
                                text = body.inner_text() if body.count() > 0 else ""
                                text = text[:1000]
                                url = f.url
                                print(f"\n--- Frame {i}: {f.name} ---")
                                print(f"URL: {url[:80]}...")

                                # 查找楼层/朝向关键词
                                keywords = ['階', '向き', '方位', '所在', '構造', '設備', '専有面積', '間取']
                                found = []
                                for kw in keywords:
                                    if kw in text:
                                        found.append(kw)
                                if found:
                                    print(f"发现关键词: {found}")
                                    # 打印包含关键词的部分
                                    for kw in found:
                                        idx = text.find(kw)
                                        if idx >= 0:
                                            snippet = text[max(0,idx-20):idx+80]
                                            print(f"  '{kw}' 上下文: ...{snippet}...")

                                # 打印前500字符预览
                                if text and len(text) > 100:
                                    print(f"\n内容预览:\n{text[:500]}...")

                            except Exception as e:
                                print(f"  Frame {i} 读取失败: {e}")

                        # 保存详细页面HTML
                        try:
                            # 找到main frame
                            for f in self.page.frames:
                                if f.name == 'main':
                                    html = f.locator('body').inner_html()
                                    with open("data/debug_detail_page.html", "w", encoding="utf-8") as file:
                                        file.write(html)
                                    print(f"\n详细页面HTML已保存: data/debug_detail_page.html")
                                    break
                        except Exception as e:
                            print(f"保存HTML失败: {e}")

                    return

            except Exception as e:
                print(f"Frame处理错误: {e}")
                continue


def main():
    os.chdir(r"D:\Fango Ads")
    scraper = DebugScraper(headless=False)

    try:
        scraper.start()
        if scraper.login():
            print("登录成功")
            if scraper.navigate_to_property_search():
                print("导航成功")
                # 进入第一个区
                areas = scraper.get_tokyo_areas()
                if areas:
                    print(f"进入: {areas[0]}")
                    if scraper.search_area(areas[0]):
                        scraper.debug_detail_page()

                        # 等待一下
                        print("\n调试完成")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        scraper.stop()


if __name__ == "__main__":
    main()
