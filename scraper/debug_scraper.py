"""
交互式调试脚本 - 用于分析页面结构
手动操作浏览器，保存各阶段页面以分析选择器
"""
import os
import sys
import time
import random

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SUMMO_USERNAME, SUMMO_PASSWORD, BASE_URL


def save_page_state(page, name: str):
    """保存页面状态"""
    os.makedirs("data/debug", exist_ok=True)

    # 保存截图
    page.screenshot(path=f"data/debug/{name}.png", full_page=True)

    # 保存HTML
    html = page.content()
    with open(f"data/debug/{name}.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"已保存: data/debug/{name}.png 和 data/debug/{name}.html")
    print(f"当前URL: {page.url}")
    print(f"页面标题: {page.title()}")


def analyze_page(page):
    """分析页面元素"""
    print("\n=== 页面元素分析 ===")

    # 表单
    forms = page.query_selector_all('form')
    print(f"表单数量: {len(forms)}")

    # 输入框
    inputs = page.query_selector_all('input')
    print(f"\n输入框 ({len(inputs)}):")
    for inp in inputs[:15]:
        try:
            inp_type = inp.get_attribute('type') or 'text'
            inp_name = inp.get_attribute('name') or ''
            inp_id = inp.get_attribute('id') or ''
            inp_value = inp.get_attribute('value') or ''
            print(f"  type={inp_type}, name={inp_name}, id={inp_id}, value={inp_value[:30] if inp_value else ''}")
        except:
            pass

    # 表格
    tables = page.query_selector_all('table')
    print(f"\n表格数量: {len(tables)}")
    for i, table in enumerate(tables[:5]):
        rows = table.query_selector_all('tr')
        print(f"  表格{i+1}: {len(rows)} 行")

    # 链接
    links = page.query_selector_all('a')
    print(f"\n链接 ({len(links)}):")
    for link in links[:20]:
        try:
            text = link.inner_text().strip()[:40]
            href = link.get_attribute('href') or ''
            if text:
                print(f"  {text} -> {href[:50]}")
        except:
            pass

    # 按钮
    buttons = page.query_selector_all('button, input[type="submit"], input[type="button"]')
    print(f"\n按钮 ({len(buttons)}):")
    for btn in buttons[:10]:
        try:
            text = btn.inner_text().strip() or btn.get_attribute('value') or ''
            print(f"  {text[:30]}")
        except:
            pass


def main():
    """交互式调试主函数"""
    print("=" * 60)
    print("交互式页面调试工具")
    print("按照提示操作，分析页面结构")
    print("=" * 60)

    playwright = sync_playwright().start()

    browser = playwright.chromium.launch(
        headless=False,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--lang=ja-JP',
        ]
    )

    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        viewport={'width': 1920, 'height': 1080},
        locale='ja-JP',
        timezone_id='Asia/Tokyo',
    )

    page = context.new_page()
    page.set_default_timeout(60000)

    # 隐藏自动化特征
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    """)

    try:
        # 步骤1: 访问登录页
        print("\n[步骤1] 访问登录页面...")
        page.goto(BASE_URL, wait_until='networkidle')
        time.sleep(2)
        save_page_state(page, "01_login_page")
        analyze_page(page)

        input("\n按Enter继续登录...")

        # 步骤2: 登录
        print("\n[步骤2] 尝试登录...")

        # 查找输入框
        username_input = page.query_selector('input[type="text"]')
        password_input = page.query_selector('input[type="password"]')

        if username_input and password_input:
            username_input.fill(SUMMO_USERNAME)
            time.sleep(0.5)
            password_input.fill(SUMMO_PASSWORD)
            time.sleep(0.5)

            # 查找提交按钮
            submit_btn = page.query_selector('input[type="submit"], button[type="submit"]')
            if submit_btn:
                submit_btn.click()
                time.sleep(3)

        save_page_state(page, "02_after_login")
        analyze_page(page)

        input("\n按Enter继续（如需手动操作请现在进行）...")

        # 步骤3: 导航
        print("\n[步骤3] 请手动导航到物件列表页面...")
        print("操作步骤:")
        print("  1. 点击「会社間流通」")
        print("  2. 点击「物件を探す」")
        print("  3. 点击「エリアから」")
        print("  4. 选择「東京」")
        print("  5. 选择一个区（如「港区」）")
        print("  6. 点击「検索」")

        input("\n完成后按Enter保存页面状态...")

        save_page_state(page, "03_search_result")
        analyze_page(page)

        # 分析物件列表
        print("\n=== 分析物件列表结构 ===")

        # 尝试各种可能的选择器
        selectors_to_try = [
            'table tbody tr',
            'table tr',
            '.list-item',
            '.property-item',
            '.bukken',
            '[class*="item"]',
            '[class*="row"]',
            'div[class*="list"] > div',
        ]

        for selector in selectors_to_try:
            elements = page.query_selector_all(selector)
            if elements:
                print(f"\n选择器 '{selector}': 找到 {len(elements)} 个元素")
                if len(elements) > 0 and len(elements) < 100:
                    # 打印第一个元素的文本
                    first_text = elements[0].inner_text()[:200] if elements else ""
                    print(f"  第一个元素内容: {first_text}...")

        input("\n按Enter继续或Ctrl+C退出...")

        # 保存最终状态
        save_page_state(page, "04_final")

        print("\n调试完成！请检查 data/debug/ 目录中的文件")
        print("根据分析结果调整 scraper.py 中的选择器")

        input("\n按Enter关闭浏览器...")

    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        browser.close()
        playwright.stop()


if __name__ == "__main__":
    main()
