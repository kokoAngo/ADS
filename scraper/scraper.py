"""
Summo入稿数据抓取脚本
使用Playwright模拟浏览器操作，抓取东京各区推定反響数>=10的物件数据
包含反反爬虫策略
"""
import os
import sys
import json
import re
import time
import random
from datetime import datetime
from typing import List, Dict, Optional

from playwright.sync_api import sync_playwright, Page, Browser
from tqdm import tqdm

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import SUMMO_USERNAME, SUMMO_PASSWORD, BASE_URL, MIN_RESPONSE_COUNT
from database.models import Property, get_session, init_db, get_engine


# 真实浏览器User-Agent列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


class SummoScraper:
    """Summo入稿爬虫类（带反反爬虫策略）"""

    def __init__(self, headless: bool = False):
        """
        初始化爬虫
        Args:
            headless: 是否使用无头模式
        """
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.session = None

    def _random_delay(self, min_sec: float = 0.5, max_sec: float = 2.0):
        """随机延迟，模拟人类操作"""
        delay = random.uniform(min_sec, max_sec)
        time.sleep(delay)

    def _human_type(self, element, text: str):
        """模拟人类打字速度"""
        for char in text:
            element.type(char, delay=random.randint(50, 150))
            if random.random() < 0.1:  # 10%概率短暂停顿
                time.sleep(random.uniform(0.1, 0.3))

    def _move_mouse_randomly(self):
        """随机移动鼠标"""
        if self.page:
            try:
                x = random.randint(100, 800)
                y = random.randint(100, 600)
                self.page.mouse.move(x, y)
            except:
                pass

    def _click_in_frames(self, text: str, frames=None) -> bool:
        """
        在所有frame中查找并点击包含指定文字的链接
        支持: inner text, title属性, alt属性
        Args:
            text: 要查找的文字
            frames: frame列表，如果为None则使用当前页面的frames
        Returns:
            是否成功点击
        """
        if frames is None:
            frames = self.page.frames

        for frame in frames:
            try:
                # 方法1: 通过title属性查找（SUUMO菜单使用title）
                elem = frame.query_selector(f'a[title*="{text}"]')
                if elem:
                    print(f"  在frame {frame.name} 中找到(title): {text}")
                    self._random_delay(0.5, 1)
                    try:
                        elem.click()
                        self._random_delay(2, 3)
                        return True
                    except Exception as click_err:
                        print(f"  点击失败: {click_err}")

                # 方法2: 通过inner text精确查找
                links = frame.query_selector_all('a')
                for link in links:
                    try:
                        link_text = link.inner_text().strip()
                        link_title = link.get_attribute('title') or ''
                        # 精确匹配或包含匹配
                        if link_text == text or text in link_text or text in link_title:
                            print(f"  在frame {frame.name} 中找到链接: '{link_text}' title='{link_title}'")
                            self._random_delay(0.5, 1)
                            link.click()
                            self._random_delay(2, 3)
                            return True
                    except Exception as e:
                        continue

                # 方法3: 检查图片alt
                imgs = frame.query_selector_all(f'img[alt*="{text}"]')
                if imgs:
                    print(f"  在frame {frame.name} 中找到图片: {text}")
                    self._random_delay(0.5, 1)
                    try:
                        imgs[0].click()
                        self._random_delay(2, 3)
                        return True
                    except Exception as click_err:
                        print(f"  图片点击失败: {click_err}")

            except Exception as e:
                print(f"  frame {frame.name} 处理异常: {e}")
                continue

        print(f"  未找到: {text}")
        return False

    def start(self):
        """启动浏览器（带反检测配置）"""
        self.playwright = sync_playwright().start()

        # 选择随机User-Agent
        user_agent = random.choice(USER_AGENTS)

        # 启动浏览器，添加反检测参数
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--lang=ja-JP',
            ]
        )

        # 创建浏览器上下文，模拟真实浏览器
        context = self.browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            locale='ja-JP',
            timezone_id='Asia/Tokyo',
            # 添加更多真实浏览器特征
            extra_http_headers={
                'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            }
        )

        self.page = context.new_page()

        # 注入JavaScript隐藏自动化特征
        self.page.add_init_script("""
            // 隐藏webdriver标识
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // 隐藏自动化相关属性
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            Object.defineProperty(navigator, 'languages', {
                get: () => ['ja-JP', 'ja', 'en-US', 'en']
            });

            // 模拟Chrome浏览器
            window.chrome = {
                runtime: {}
            };

            // 隐藏Playwright特征
            delete window.__playwright;
            delete window.__pw_manual;
        """)

        # 设置超时时间
        self.page.set_default_timeout(30000)

        # 初始化数据库
        engine = init_db()
        self.session = get_session(engine)

        print(f"浏览器启动成功 (User-Agent: {user_agent[:50]}...)")

    def stop(self):
        """关闭浏览器"""
        if self.session:
            self.session.close()
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright'):
            self.playwright.stop()
        print("浏览器已关闭")

    def login(self) -> bool:
        """
        登录Summo入稿
        Returns:
            是否登录成功
        """
        try:
            print(f"正在访问: {BASE_URL}")
            self.page.goto(BASE_URL, wait_until='networkidle')
            self._random_delay(2, 4)

            # 随机移动鼠标
            self._move_mouse_randomly()

            # 等待页面加载，查找登录表单
            # SUUMO系统使用特殊的name属性: ${loginForm.loginId} 和 ${loginForm.password}
            username_input = None
            password_input = None

            # 首先尝试按name属性查找（SUUMO特有）
            try:
                username_input = self.page.query_selector('input[name*="loginId"]')
                password_input = self.page.query_selector('input[name*="password"]')
            except:
                pass

            # 如果没找到，尝试通用选择器
            if not username_input:
                login_selectors = [
                    'input[type="text"]',
                    'input[type="text"][name*="user"]',
                    'input[type="text"][name*="id"]',
                ]
                for selector in login_selectors:
                    try:
                        username_input = self.page.query_selector(selector)
                        if username_input and username_input.is_visible():
                            print(f"找到用户名输入框: {selector}")
                            break
                    except:
                        continue

            if not password_input:
                password_input = self.page.query_selector('input[type="password"]')

            if not username_input:
                print("未找到用户名输入框，可能已经登录或页面结构不同")
                self.page.screenshot(path="data/login_page.png")
                return self._check_if_logged_in()

            print(f"找到用户名输入框")

            # 模拟人类点击输入框
            self._random_delay(0.5, 1)
            username_input.click()
            self._random_delay(0.3, 0.7)

            # 清空后输入用户名
            username_input.fill('')
            self._human_type(username_input, SUMMO_USERNAME)
            self._random_delay(0.5, 1)

            if password_input:
                print(f"找到密码输入框")
                self._move_mouse_randomly()
                password_input.click()
                self._random_delay(0.3, 0.7)
                password_input.fill('')
                self._human_type(password_input, SUMMO_PASSWORD)
                self._random_delay(0.5, 1)

            # 查找并点击登录按钮
            login_button_selectors = [
                'input[type="image"]',  # 图片按钮
                'input[type="submit"]',
                'button[type="submit"]',
                'input[value*="ログイン"]',
                'button:has-text("ログイン")',
                'a:has-text("ログイン")',
                'img[alt*="ログイン"]',
            ]

            login_clicked = False
            for selector in login_button_selectors:
                try:
                    login_btn = self.page.query_selector(selector)
                    if login_btn and login_btn.is_visible():
                        print(f"找到登录按钮: {selector}")
                        self._random_delay(0.5, 1)
                        login_btn.click()
                        login_clicked = True
                        break
                except Exception as e:
                    print(f"尝试选择器 {selector} 失败: {e}")
                    continue

            if not login_clicked:
                # 尝试直接提交表单
                print("尝试提交表单...")
                try:
                    self.page.keyboard.press('Enter')
                except:
                    pass

            # 等待页面跳转
            self._random_delay(3, 5)
            return self._check_if_logged_in()

        except Exception as e:
            print(f"登录失败: {e}")
            self.page.screenshot(path="data/login_error.png")
            return False

    def _check_if_logged_in(self) -> bool:
        """检查是否已登录"""
        try:
            self.page.screenshot(path="data/current_page.png")
            print(f"当前页面URL: {self.page.url}")

            # 检查是否还在登录页面
            page_content = self.page.content().lower()
            if 'login' in self.page.url.lower() or 'ログイン' in page_content:
                # 检查是否有错误信息
                error_texts = ['エラー', 'error', '失敗', 'failed', '正しく']
                for error in error_texts:
                    if error in page_content:
                        print(f"登录可能失败，检测到错误信息")
                        return False

            return True
        except:
            return False

    def navigate_to_property_search(self) -> bool:
        """
        导航到物件搜索页面
        按照流程: 会社間流通 -> 物件を探す -> エリアから -> 東京
        注意：页面使用frameset结构，需要在正确的frame中操作
        """
        try:
            print("正在导航到物件搜索...")
            self._random_delay(1, 2)

            # 检查页面是否使用frameset
            frames = self.page.frames
            print(f"页面frame数量: {len(frames)}")
            for i, frame in enumerate(frames):
                print(f"  Frame {i}: name={frame.name}, url={frame.url[:60]}...")

            # 保存当前页面用于调试
            self.page.screenshot(path="data/step0_main.png")

            # 获取导航frame (navi)
            navi_frame = None
            for frame in frames:
                if 'navi' in frame.name.lower() or 'MNU' in frame.url:
                    navi_frame = frame
                    print(f"找到导航frame: {frame.name}")
                    break

            if not navi_frame:
                # 如果没找到命名frame，尝试使用第一个非主frame
                navi_frame = frames[1] if len(frames) > 1 else self.page.main_frame

            # 步骤1: 在导航frame中点击「会社間流通」
            # 菜单按钮HTML: <a class="menu_btn" id="menu_5" title="会社間流通">
            # 使用 title 属性或 id 来定位
            print("步骤1: 点击会社間流通...")
            clicked = False

            # 方法1: 通过title属性查找
            menu_selectors = [
                'a[title="会社間流通"]',
                'a#menu_5',
                'a.menu_btn[title*="会社間流通"]',
            ]

            for selector in menu_selectors:
                try:
                    elem = navi_frame.query_selector(selector)
                    if elem:
                        print(f"  找到菜单按钮: {selector}")
                        self._random_delay(0.5, 1)
                        elem.click()
                        clicked = True
                        self._random_delay(2, 3)
                        break
                except Exception as e:
                    print(f"  选择器 {selector} 失败: {e}")

            self.page.screenshot(path="data/step1_after_menu.png")

            if not clicked:
                print("  未能点击会社間流通菜单")
                return False

            # 会社間流通页面已经显示物件搜索和地区列表
            # 直接点击東京链接即可
            self._random_delay(2, 3)
            frames = self.page.frames

            # 步骤2: 直接点击東京（在関東区域下）
            print("步骤2: 点击東京...")
            tokyo_clicked = self._click_in_frames("東京", frames)

            if not tokyo_clicked:
                # 尝试通过href查找
                print("  尝试通过href查找東京链接...")
                for frame in frames:
                    try:
                        link = frame.query_selector('a[href*="todofukenCd=13"]')
                        if link:
                            print("  找到東京链接(href)")
                            link.click()
                            tokyo_clicked = True
                            self._random_delay(2, 3)
                            break
                    except:
                        continue

            self.page.screenshot(path="data/step2_tokyo.png")

            if not tokyo_clicked:
                print("  未能点击東京链接")
                return False

            self._random_delay(2, 3)

            self.page.screenshot(path="data/tokyo_areas.png")
            print("成功导航到东京区域选择页面")
            return True

        except Exception as e:
            print(f"导航失败: {e}")
            import traceback
            traceback.print_exc()
            self.page.screenshot(path="data/navigation_error.png")
            return False

    def get_tokyo_areas(self) -> List[str]:
        """
        从当前页面获取东京所有市郡区列表
        Returns:
            市郡区名称列表
        """
        areas = []
        try:
            frames = self.page.frames

            # 在frames中查找区域链接
            for frame in frames:
                try:
                    # 查找所有包含"区"或"市"的链接
                    links = frame.query_selector_all('a')
                    for link in links:
                        try:
                            text = link.inner_text().strip()
                            href = link.get_attribute('href') or ''
                            # 筛选区域链接（包含shiguCd参数或以区/市结尾）
                            if ('shiguCd' in href or 'todofukenCd' in href) and text:
                                if ('区' in text or '市' in text) and len(text) < 15:
                                    if text not in areas and text != '東京':
                                        areas.append(text)
                        except:
                            continue
                except:
                    continue

            # 如果从页面获取到区域，直接返回
            if areas:
                print(f"从页面获取到 {len(areas)} 个区域: {areas[:5]}...")
                return areas

            # 如果无法自动获取，使用东京23区的预设列表
            areas = [
                "千代田区", "中央区", "港区", "新宿区", "文京区",
                "台東区", "墨田区", "江東区", "品川区", "目黒区",
                "大田区", "世田谷区", "渋谷区", "中野区", "杉並区",
                "豊島区", "北区", "荒川区", "板橋区", "練馬区",
                "足立区", "葛飾区", "江戸川区",
                "八王子市", "立川市", "武蔵野市", "三鷹市", "青梅市",
                "府中市", "昭島市", "調布市", "町田市", "小金井市",
                "小平市", "日野市", "東村山市", "国分寺市", "国立市",
                "福生市", "狛江市", "東大和市", "清瀬市", "東久留米市",
                "武蔵村山市", "多摩市", "稲城市", "羽村市", "あきる野市",
                "西東京市"
            ]

            print(f"使用预设区域列表: {len(areas)} 个区域")
            return areas

        except Exception as e:
            print(f"获取区域列表失败: {e}")
            return areas

    def search_area(self, area_name: str) -> bool:
        """
        搜索指定区域的物件
        尝试多种方式选择区域：
        1. 直接点击区域链接
        2. 选择复选框 + 点击搜索按钮
        Args:
            area_name: 区域名称
        Returns:
            是否成功
        """
        try:
            self._move_mouse_randomly()
            self._random_delay(0.5, 1.5)

            frames = self.page.frames
            main_frame = None

            # 找到main frame
            for frame in frames:
                if frame.name == 'main' or 'main' in frame.url.lower():
                    main_frame = frame
                    break

            if not main_frame:
                main_frame = frames[-1] if len(frames) > 1 else self.page.main_frame

            # 方法1: 尝试找到复选框并选中
            checkbox_clicked = False
            for frame in frames:
                try:
                    # 查找包含区域名的复选框
                    checkboxes = frame.query_selector_all('input[type="checkbox"]')
                    for cb in checkboxes:
                        try:
                            # 检查复选框的label或周围文本
                            parent = cb.evaluate('el => el.parentElement ? el.parentElement.innerText : ""')
                            cb_id = cb.get_attribute('id') or ''
                            cb_value = cb.get_attribute('value') or ''

                            if area_name in str(parent) or area_name in cb_value:
                                if not cb.is_checked():
                                    cb.click()
                                    checkbox_clicked = True
                                    print(f"  选中复选框: {area_name}")
                                    break
                        except:
                            continue

                    if checkbox_clicked:
                        break

                    # 也尝试查找label
                    labels = frame.query_selector_all('label')
                    for label in labels:
                        try:
                            label_text = label.inner_text().strip()
                            if area_name in label_text:
                                label.click()
                                checkbox_clicked = True
                                print(f"  点击标签: {area_name}")
                                break
                        except:
                            continue

                    if checkbox_clicked:
                        break

                except Exception as e:
                    continue

            # 如果选中了复选框，尝试点击搜索按钮
            if checkbox_clicked:
                self._random_delay(0.5, 1)
                search_clicked = False

                for frame in frames:
                    try:
                        # 查找搜索/检索按钮
                        search_selectors = [
                            'input[type="submit"]',
                            'input[type="image"]',
                            'button[type="submit"]',
                            'a:has-text("検索")',
                            'input[value*="検索"]',
                            'img[alt*="検索"]',
                        ]

                        for selector in search_selectors:
                            try:
                                btn = frame.query_selector(selector)
                                if btn and btn.is_visible():
                                    btn.click()
                                    search_clicked = True
                                    print(f"  点击搜索按钮")
                                    self._random_delay(2, 4)
                                    break
                            except:
                                continue

                        if search_clicked:
                            break
                    except:
                        continue

                if search_clicked:
                    self.page.screenshot(path=f"data/search_{area_name}.png")
                    return True

            # 方法2: 直接点击区域链接
            clicked = self._click_in_frames(area_name, frames)

            if clicked:
                # 等待frame内容加载
                self._random_delay(2, 4)

                # 检查是否有表格出现（表示物件列表已加载）
                for frame in frames:
                    try:
                        tables = frame.query_selector_all('table')
                        if tables and len(tables) > 0:
                            rows = frame.query_selector_all('table tr')
                            if len(rows) > 2:  # 有数据行
                                print(f"  表格已加载: {len(rows)} 行")
                                break
                    except:
                        continue

                self.page.screenshot(path=f"data/search_{area_name}.png")
                return True
            else:
                print(f"  未找到区域链接: {area_name}")
                return False

        except Exception as e:
            print(f"搜索区域 {area_name} 失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def filter_by_response_count(self) -> bool:
        """
        按類似物件推定反響数排序（降序），高反響物件排在前面
        点击表头链接: <a href="BTB1B2800.action?sortItem=suiteiHankyoDesc" name="sort">類似物件推定反響数</a>
        """
        try:
            self._random_delay(0.5, 1)
            frames = self.page.frames

            # 在所有frame中查找排序链接
            for frame in frames:
                try:
                    # 方法1: 通过href查找排序链接
                    sort_link = frame.query_selector('a[href*="suiteiHankyoDesc"]')
                    if sort_link:
                        print("  点击排序: 類似物件推定反響数（降序）")
                        sort_link.click()
                        self._random_delay(2, 3)
                        return True

                    # 方法2: 通过name属性查找
                    sort_link = frame.query_selector('a[name="sort"]')
                    if sort_link:
                        link_text = sort_link.inner_text()
                        if '推定反響' in link_text:
                            print("  点击排序: 類似物件推定反響数")
                            sort_link.click()
                            self._random_delay(2, 3)
                            return True

                    # 方法3: 通过文本查找
                    links = frame.query_selector_all('a')
                    for link in links:
                        try:
                            text = link.inner_text().replace('\n', '').strip()
                            href = link.get_attribute('href') or ''
                            if '推定反響' in text and 'sort' in href.lower():
                                print(f"  点击排序链接: {text[:20]}")
                                link.click()
                                self._random_delay(2, 3)
                                return True
                        except:
                            continue

                except Exception as e:
                    continue

            print("  未找到排序链接")
            return False

        except Exception as e:
            print(f"排序失败: {e}")
            return False

    def scrape_property_list(self, area_name: str) -> List[Dict]:
        """
        抓取当前页面的物件列表
        页面结构：表格，每行是一个物件
        Args:
            area_name: 当前区域名称
        Returns:
            物件数据列表
        """
        properties = []
        try:
            self._random_delay(1, 2)
            frames = self.page.frames

            # 保存当前页面用于调试
            self.page.screenshot(path=f"data/property_list_{area_name}.png")

            # 在所有frame中查找物件表格
            for frame in frames:
                try:
                    frame_url = frame.url
                    frame_name = frame.name or "unnamed"

                    # 查找所有表格
                    tables = frame.query_selector_all('table')
                    if not tables:
                        continue

                    # 遍历每个表格
                    for table_idx, table in enumerate(tables):
                        rows = table.query_selector_all('tr')
                        if not rows or len(rows) < 2:
                            continue

                        # 检查表头是否包含物件相关的列
                        header_row = rows[0]
                        header_text = header_row.inner_text() if header_row else ""

                        # 判断是否是物件列表表格
                        is_property_table = any(kw in header_text for kw in [
                            '推定反響', '賃料', '物件', '沿線', '駅', '住所', '間取'
                        ])

                        if not is_property_table:
                            continue

                        print(f"  在frame '{frame_name}' 表格{table_idx} 中找到 {len(rows)-1} 个数据行")

                        # 遍历数据行（跳过表头）
                        for row_idx, row in enumerate(rows[1:], start=1):
                            try:
                                cells = row.query_selector_all('td')
                                if not cells or len(cells) < 3:
                                    continue

                                row_text = row.inner_text()

                                # 检查是否包含推定反響数相关关键词
                                has_response = '件/月' in row_text or '件以上' in row_text or '推定反響' in header_text

                                if not has_response:
                                    continue

                                property_data = self._extract_property_data_from_row(row, cells, area_name, row_text)
                                if property_data:
                                    response = property_data.get('estimated_response', 0)
                                    if response >= MIN_RESPONSE_COUNT:
                                        properties.append(property_data)
                                    # 如果遇到低于阈值的物件且已经按反響数排序，可以提前停止
                                    # 因为后面的物件反響数只会更低

                            except Exception as row_err:
                                continue

                        # 如果找到物件表格就跳出
                        if properties:
                            break

                    if properties:
                        break

                except Exception as frame_err:
                    continue

            print(f"找到 {len(properties)} 个物件 (反響数>={MIN_RESPONSE_COUNT})")

        except Exception as e:
            print(f"抓取物件列表失败: {e}")
            import traceback
            traceback.print_exc()

        return properties

    def _extract_property_data_from_row(self, row, cells, area_name: str, row_text: str) -> Optional[Dict]:
        """
        从表格行中提取物件数据
        不依赖固定列顺序，而是搜索包含特定关键词的单元格
        """
        try:
            data = {
                'area_name': area_name,
                'scraped_at': datetime.now(),
                'address_prefecture': '東京都',
                'address_city': area_name,
            }

            # 遍历所有单元格，根据内容提取数据
            for i, cell in enumerate(cells):
                try:
                    cell_text = cell.inner_text().strip()
                    if not cell_text:
                        continue

                    # 提取推定反響数 - 包含"件/月"或"件以上"
                    if '件/月' in cell_text or '件以上' in cell_text:
                        if '10件以上' in cell_text:
                            data['estimated_response'] = 10
                        else:
                            # 匹配数字，如 "1.79件/月" 或 "5件/月"
                            response_match = re.search(r'([\d.]+)\s*件', cell_text)
                            if response_match:
                                val = float(response_match.group(1))
                                data['estimated_response'] = int(val) if val >= 1 else 1

                    # 提取賃料 - 包含"万円"
                    elif '万円' in cell_text and 'rent' not in data:
                        rent_match = re.search(r'([\d.]+)\s*万円', cell_text)
                        if rent_match:
                            data['rent'] = int(float(rent_match.group(1)) * 10000)
                        # 管理費
                        mgmt_match = re.search(r'管理費[：:\s]*([\d,]+)\s*円', cell_text)
                        if mgmt_match:
                            data['management_fee'] = int(mgmt_match.group(1).replace(',', ''))

                    # 提取面積 - 包含"㎡"
                    elif '㎡' in cell_text and 'area_sqm' not in data:
                        area_match = re.search(r'([\d.]+)\s*㎡', cell_text)
                        if area_match:
                            data['area_sqm'] = float(area_match.group(1))
                        # 間取り
                        layout_match = re.search(r'([1-9][LKDR]+)', cell_text)
                        if layout_match:
                            data['floor_plan'] = layout_match.group(1)
                        # 築年月
                        built_match = re.search(r"'?(\d{2})/(\d{1,2})", cell_text)
                        if built_match:
                            year = int(built_match.group(1))
                            data['built_year'] = (1900 + year) if year > 50 else (2000 + year)

                    # 提取沿線/駅/住所/物件名 - 复合信息单元格
                    elif ('線' in cell_text or '駅' in cell_text) and 'railway_line' not in data:
                        lines = cell_text.split('\n')
                        for line in lines:
                            line = line.strip()
                            if not line:
                                continue
                            if '線' in line:
                                parts = line.split('/')
                                data['railway_line'] = parts[0].strip()
                                if len(parts) > 1 and '駅' in parts[1]:
                                    data['station'] = parts[1].replace('駅', '').strip()
                            elif '区' in line or '市' in line or '町' in line or '丁目' in line:
                                if 'address_detail' not in data:
                                    data['address_detail'] = line
                            elif len(line) > 2 and 'property_name' not in data:
                                # 可能是物件名
                                data['property_name'] = line[:50]

                    # 提取徒歩分数
                    elif '分' in cell_text and 'walk_minutes' not in data:
                        walk_match = re.search(r'徒歩?\s*(\d+)\s*分', cell_text)
                        if walk_match:
                            data['walk_minutes'] = int(walk_match.group(1))

                except Exception as cell_err:
                    continue

            # 如果没有找到反響数，尝试从整行文本中提取
            if 'estimated_response' not in data:
                if '10件以上' in row_text:
                    data['estimated_response'] = 10
                else:
                    response_match = re.search(r'([\d.]+)\s*件[/／]月', row_text)
                    if response_match:
                        val = float(response_match.group(1))
                        data['estimated_response'] = int(val) if val >= 1 else 1

            # 保存原始数据用于调试
            data['raw_data'] = row_text[:500]

            return data

        except Exception as e:
            print(f"    解析行数据失败: {e}")
            return None

    def _extract_property_data(self, element, area_name: str) -> Optional[Dict]:
        """
        从HTML元素中提取物件数据
        """
        try:
            html = element.inner_html()
            text = element.inner_text()

            data = {
                'area_name': area_name,
                'raw_data': html,
                'scraped_at': datetime.now(),
            }

            # 尝试解析物件名和号室
            name_match = re.search(r'物件名[：:]\s*(.+?)(?:\s|$)', text)
            if name_match:
                full_name = name_match.group(1)
                room_match = re.search(r'(\d+号室?|\d+階\d+号?)$', full_name)
                if room_match:
                    data['property_name'] = full_name[:room_match.start()].strip()
                    data['room_number'] = room_match.group(1)
                else:
                    data['property_name'] = full_name

            # 解析住所
            address_match = re.search(r'住所[：:]\s*(.+?)(?:\s|$)', text)
            if address_match:
                address = address_match.group(1)
                data['address_prefecture'] = '東京都'
                data['address_city'] = area_name
                data['address_detail'] = address

            # 解析沿線/駅
            line_match = re.search(r'沿線[：:]\s*(.+?)(?:\s|$)', text)
            if line_match:
                data['railway_line'] = line_match.group(1)

            station_match = re.search(r'駅[：:]\s*(.+?)(?:\s|$)', text)
            if station_match:
                data['station'] = station_match.group(1)

            walk_match = re.search(r'徒歩(\d+)分', text)
            if walk_match:
                data['walk_minutes'] = int(walk_match.group(1))

            # 解析間取り
            layout_match = re.search(r'([1-9][LKDR]+)', text)
            if layout_match:
                data['floor_plan'] = layout_match.group(1)

            # 解析面積
            area_match = re.search(r'(\d+\.?\d*)㎡', text)
            if area_match:
                data['area_sqm'] = float(area_match.group(1))

            # 解析賃料
            rent_match = re.search(r'賃料[：:]\s*(\d+(?:,\d+)*)\s*円', text)
            if rent_match:
                data['rent'] = int(rent_match.group(1).replace(',', ''))
            else:
                rent_match2 = re.search(r'(\d+(?:,\d+)*)\s*万円', text)
                if rent_match2:
                    data['rent'] = int(float(rent_match2.group(1).replace(',', '')) * 10000)

            # 解析推定反響数
            response_match = re.search(r'(\d+)\s*件[/／]月', text)
            if response_match:
                data['estimated_response'] = int(response_match.group(1))
            else:
                response_match2 = re.search(r'推定反響[：:]\s*(\d+)', text)
                if response_match2:
                    data['estimated_response'] = int(response_match2.group(1))

            # 解析築年
            built_match = re.search(r'築(\d+)年', text)
            if built_match:
                data['built_year'] = datetime.now().year - int(built_match.group(1))

            return data

        except Exception as e:
            print(f"解析物件数据失败: {e}")
            return None

    def save_properties(self, properties: List[Dict]):
        """保存物件数据到数据库"""
        saved_count = 0
        for prop_data in properties:
            try:
                property_obj = Property(**prop_data)
                self.session.add(property_obj)
                saved_count += 1
            except Exception as e:
                print(f"保存物件失败: {e}")
                continue

        try:
            self.session.commit()
            print(f"成功保存 {saved_count} 个物件")
        except Exception as e:
            print(f"提交数据库失败: {e}")
            self.session.rollback()

    def scrape_all_areas(self):
        """抓取东京所有区域的物件数据"""
        # 先获取区域列表
        areas = self.get_tokyo_areas()
        total_properties = 0
        area_stats = {}
        skipped_areas = []

        print(f"\n开始抓取 {len(areas)} 个区域的数据...")

        for idx, area in enumerate(tqdm(areas, desc="抓取进度")):
            print(f"\n[{idx+1}/{len(areas)}] 正在抓取: {area}")

            # 随机延迟，避免被检测
            self._random_delay(1, 2)

            area_count = 0

            # 每次都重新导航到东京区域选择页面，确保状态正确
            if idx > 0:  # 第一次已经在正确页面
                if not self.navigate_to_property_search():
                    print(f"  导航失败，跳过 {area}")
                    skipped_areas.append(area)
                    continue

            if self.search_area(area):
                # 按推定反響数排序（降序）
                self.filter_by_response_count()

                # 抓取第一页
                properties = self.scrape_property_list(area)

                if properties:
                    self.save_properties(properties)
                    area_count += len(properties)
                    print(f"  第1页: {len(properties)} 个物件")

                # 处理分页 - 继续抓取直到没有更多高反响物件
                page_num = 1
                max_pages = 20  # 最多抓取20页，防止无限循环

                while page_num < max_pages and self._has_next_page():
                    page_num += 1
                    self._random_delay(1, 2)

                    if not self._goto_next_page():
                        break

                    page_properties = self.scrape_property_list(area)
                    if page_properties:
                        self.save_properties(page_properties)
                        area_count += len(page_properties)
                        print(f"  第{page_num}页: {len(page_properties)} 个物件")
                    else:
                        # 没有找到符合条件的物件，停止翻页
                        print(f"  第{page_num}页: 无符合条件物件，停止")
                        break

                total_properties += area_count
                area_stats[area] = area_count

                if area_count > 0:
                    print(f"  {area} 共: {area_count} 个物件")
            else:
                skipped_areas.append(area)

        # 打印统计信息
        print(f"\n{'='*50}")
        print(f"抓取完成！")
        print(f"总计: {total_properties} 个物件")
        print(f"\n各区域统计:")
        for area, count in sorted(area_stats.items(), key=lambda x: -x[1]):
            if count > 0:
                print(f"  {area}: {count}")
        if skipped_areas:
            print(f"\n跳过的区域 ({len(skipped_areas)}个): {', '.join(skipped_areas[:10])}...")
        print(f"{'='*50}")

    def _has_next_page(self) -> bool:
        """检查是否有下一页 - 在frames中查找"""
        try:
            frames = self.page.frames
            for frame in frames:
                try:
                    # 查找 "次の50件" 或 "次へ" 链接
                    next_selectors = [
                        'a:has-text("次の50件")',
                        'a:has-text("次へ")',
                        'a[href*="page"]:has-text("次")',
                        '.next-page',
                        '[class*="next"]',
                    ]
                    for selector in next_selectors:
                        next_btn = frame.query_selector(selector)
                        if next_btn and next_btn.is_visible():
                            return True
                except:
                    continue
            return False
        except:
            return False

    def _goto_next_page(self) -> bool:
        """前往下一页 - 在frames中查找并点击"""
        try:
            frames = self.page.frames
            for frame in frames:
                try:
                    next_selectors = [
                        'a:has-text("次の50件")',
                        'a:has-text("次へ")',
                        'a[href*="page"]:has-text("次")',
                    ]
                    for selector in next_selectors:
                        next_btn = frame.query_selector(selector)
                        if next_btn and next_btn.is_visible():
                            self._move_mouse_randomly()
                            next_btn.click()
                            self._random_delay(2, 3)
                            return True
                except:
                    continue
            print("  未找到下一页按钮")
            return False
        except Exception as e:
            print(f"翻页失败: {e}")
            return False

    def _go_back_to_area_selection(self):
        """返回区域选择页面"""
        try:
            frames = self.page.frames

            # 在所有frame中查找返回按钮
            for frame in frames:
                try:
                    # 尝试多种返回按钮
                    back_selectors = [
                        'a:has-text("戻る")',
                        'input[value*="戻る"]',
                        'button:has-text("戻る")',
                        'a:has-text("一覧")',
                        'a:has-text("エリア")',
                        'img[alt*="戻る"]',
                    ]

                    for selector in back_selectors:
                        try:
                            btn = frame.query_selector(selector)
                            if btn and btn.is_visible():
                                btn.click()
                                self._random_delay(1, 2)
                                return
                        except:
                            continue
                except:
                    continue

            # 如果找不到返回按钮，尝试浏览器后退
            self.page.go_back()
            self._random_delay(1, 2)

        except Exception as e:
            # 最后尝试重新导航
            try:
                self.navigate_to_property_search()
            except:
                pass

    def inspect_page_structure(self):
        """检查页面结构（用于调试）"""
        try:
            self.page.screenshot(path="data/page_structure.png", full_page=True)

            html = self.page.content()
            with open("data/page_structure.html", "w", encoding="utf-8") as f:
                f.write(html)

            print("页面结构已保存到 data/page_structure.html 和 data/page_structure.png")
            print("\n页面标题:", self.page.title())
            print("当前URL:", self.page.url)

            forms = self.page.query_selector_all('form')
            print(f"\n找到 {len(forms)} 个表单")

            inputs = self.page.query_selector_all('input')
            print(f"找到 {len(inputs)} 个输入框")
            for inp in inputs[:10]:
                try:
                    inp_type = inp.get_attribute('type') or 'text'
                    inp_name = inp.get_attribute('name') or ''
                    inp_id = inp.get_attribute('id') or ''
                    print(f"  - type={inp_type}, name={inp_name}, id={inp_id}")
                except:
                    pass

            tables = self.page.query_selector_all('table')
            print(f"找到 {len(tables)} 个表格")

            links = self.page.query_selector_all('a')
            print(f"找到 {len(links)} 个链接")

        except Exception as e:
            print(f"检查页面结构失败: {e}")


def main():
    """主函数"""
    scraper = SummoScraper(headless=False)

    try:
        scraper.start()

        if not scraper.login():
            print("登录失败，请检查账号密码")
            scraper.inspect_page_structure()
            return

        scraper.inspect_page_structure()

        if not scraper.navigate_to_property_search():
            print("导航失败")
            return

        scraper.scrape_all_areas()

    except KeyboardInterrupt:
        print("\n用户中断抓取")
    except Exception as e:
        print(f"抓取过程出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        scraper.stop()


if __name__ == "__main__":
    main()
