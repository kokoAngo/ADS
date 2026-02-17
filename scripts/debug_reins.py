"""
调试脚本：探索 REINS 网站结构
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()

REINS_URL = "https://system.reins.jp/login/main/KG/GKG001200"
REINS_USERNAME = os.getenv("REINS_USERNAME")
REINS_PASSWORD = os.getenv("REINS_PASSWORD")


def debug_reins():
    """探索 REINS 网站结构"""
    print(f"REINS 用户名: {REINS_USERNAME}")
    print(f"REINS 密码: {'*' * len(REINS_PASSWORD) if REINS_PASSWORD else 'None'}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            locale='ja-JP',
        )
        page = context.new_page()

        try:
            print(f"\n访问: {REINS_URL}")
            page.goto(REINS_URL, wait_until='networkidle')
            time.sleep(3)

            # 截图
            page.screenshot(path="data/reins_login_page.png")
            print("截图已保存: data/reins_login_page.png")

            # 打印页面信息
            print(f"\n当前URL: {page.url}")
            print(f"页面标题: {page.title()}")

            # 查找输入框
            inputs = page.query_selector_all('input')
            print(f"\n找到 {len(inputs)} 个输入框:")
            for i, inp in enumerate(inputs):
                inp_type = inp.get_attribute('type') or 'text'
                inp_name = inp.get_attribute('name') or ''
                inp_id = inp.get_attribute('id') or ''
                inp_placeholder = inp.get_attribute('placeholder') or ''
                print(f"  [{i}] type={inp_type}, name={inp_name}, id={inp_id}, placeholder={inp_placeholder}")

            # 查找按钮
            buttons = page.query_selector_all('button')
            print(f"\n找到 {len(buttons)} 个按钮:")
            for i, btn in enumerate(buttons):
                btn_text = btn.inner_text().strip()
                btn_type = btn.get_attribute('type') or ''
                print(f"  [{i}] text='{btn_text}', type={btn_type}")

            # 尝试登录
            print("\n尝试登录...")

            # 查找用户名输入框
            username_input = page.query_selector('input[type="text"]') or \
                            page.query_selector('input[name*="user"]') or \
                            page.query_selector('input[name*="id"]') or \
                            page.query_selector('input[placeholder*="ID"]')

            # 查找密码输入框
            password_input = page.query_selector('input[type="password"]')

            if username_input and password_input:
                print("找到登录表单")

                # 输入用户名
                username_input.click()
                time.sleep(0.3)
                username_input.fill(REINS_USERNAME)
                time.sleep(0.5)

                # 输入密码
                password_input.click()
                time.sleep(0.3)
                password_input.fill(REINS_PASSWORD)
                time.sleep(0.5)

                # 勾选同意复选框（使用label点击）
                print("勾选同意复选框...")
                # 尝试点击包含"遵守"的label
                labels = page.query_selector_all('label')
                for label in labels:
                    try:
                        text = label.inner_text()
                        if '遵守' in text or '規程' in text:
                            print(f"  点击: {text[:30]}...")
                            label.click()
                            time.sleep(0.5)
                    except:
                        pass

                # 如果label不行，用JavaScript勾选
                page.evaluate('''
                    document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                        if (!cb.checked) {
                            cb.checked = true;
                            cb.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    });
                ''')
                time.sleep(1)

                # 截图看看状态
                page.screenshot(path="data/reins_before_login.png")
                print("勾选后截图: data/reins_before_login.png")

                # 点击登录按钮（用force）
                login_btn = page.query_selector('button:has-text("ログイン")')

                if login_btn:
                    print("点击登录按钮")
                    try:
                        login_btn.click(force=True)
                    except:
                        # 用JavaScript点击
                        page.evaluate('document.querySelector("button").click()')
                    time.sleep(5)

                    page.screenshot(path="data/reins_after_login.png")
                    print("登录后截图: data/reins_after_login.png")
                    print(f"当前URL: {page.url}")

                    # 分析登录后的页面
                    print("\n分析登录后的页面...")

                    # 查找菜单/导航
                    links = page.query_selector_all('a')
                    print(f"找到 {len(links)} 个链接")

                    # 点击物件番号検索
                    print("查找物件番号検索...")

                    # 使用page.locator更可靠
                    bukken_link = page.locator('text=物件番号検索').first
                    if bukken_link.count() > 0:
                        print("  找到物件番号検索，点击...")
                        bukken_link.click()
                        time.sleep(3)
                        page.screenshot(path="data/reins_bukken_search.png")
                        print("截图: data/reins_bukken_search.png")
                        print(f"当前URL: {page.url}")

                        # 分析物件検索页面
                        print("\n分析物件検索页面...")
                        inputs2 = page.query_selector_all('input')
                        print(f"找到 {len(inputs2)} 个输入框")
                        for inp in inputs2[:10]:
                            inp_type = inp.get_attribute('type') or 'text'
                            inp_placeholder = inp.get_attribute('placeholder') or ''
                            print(f"  type={inp_type}, placeholder={inp_placeholder}")
                    else:
                        print("  未找到物件番号検索")

            else:
                print("未找到登录表单")

            # 保存HTML
            html = page.content()
            with open("data/reins_page.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("\nHTML已保存: data/reins_page.html")

            print("\n等待10秒供手动观察...")
            time.sleep(10)

        except Exception as e:
            print(f"错误: {e}")
            import traceback
            traceback.print_exc()
            page.screenshot(path="data/reins_error.png")

        finally:
            browser.close()


if __name__ == "__main__":
    os.chdir(r"D:\Fango Ads")
    debug_reins()
