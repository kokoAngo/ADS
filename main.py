"""
Fango Ads - 物件数据抓取与分析系统
主程序入口
"""
import os
import sys
import argparse

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_scraper(headless: bool = False):
    """运行爬虫抓取数据"""
    from scraper.scraper import SummoScraper

    print("=" * 50)
    print("启动数据抓取...")
    print("=" * 50)

    scraper = SummoScraper(headless=headless)

    try:
        scraper.start()

        if not scraper.login():
            print("登录失败，请检查 .env 文件中的账号密码配置")
            scraper.inspect_page_structure()
            return

        if not scraper.navigate_to_property_search():
            print("导航到搜索页面失败")
            scraper.inspect_page_structure()
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


def run_analysis():
    """运行数据分析"""
    from analysis.analyzer import PropertyAnalyzer

    print("=" * 50)
    print("启动数据分析...")
    print("=" * 50)

    analyzer = PropertyAnalyzer()

    try:
        analyzer.run_full_analysis()
    except Exception as e:
        print(f"分析过程出错: {e}")
        import traceback
        traceback.print_exc()


def init_database():
    """初始化数据库"""
    from database.models import init_db

    print("=" * 50)
    print("初始化数据库...")
    print("=" * 50)

    # 确保 data 目录存在
    os.makedirs("data", exist_ok=True)

    init_db()
    print("数据库初始化完成")


def inspect_page(url: str = None):
    """检查页面结构（调试用）"""
    from scraper.scraper import SummoScraper
    from config import BASE_URL

    print("=" * 50)
    print("检查页面结构...")
    print("=" * 50)

    target_url = url or BASE_URL

    scraper = SummoScraper(headless=False)

    try:
        scraper.start()
        scraper.page.goto(target_url)

        import time
        time.sleep(3)

        scraper.inspect_page_structure()

        print("\n页面已打开，可以手动操作。按 Ctrl+C 关闭。")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        scraper.stop()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='Fango Ads - 物件数据抓取与分析系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  python main.py init        # 初始化数据库
  python main.py scrape      # 运行爬虫抓取数据
  python main.py analyze     # 运行数据分析
  python main.py inspect     # 检查页面结构（调试用）
  python main.py all         # 运行完整流程（抓取+分析）
        """
    )

    parser.add_argument(
        'command',
        choices=['init', 'scrape', 'analyze', 'inspect', 'all'],
        help='要执行的命令'
    )

    parser.add_argument(
        '--headless',
        action='store_true',
        help='使用无头模式运行爬虫'
    )

    parser.add_argument(
        '--url',
        type=str,
        help='指定要检查的URL（用于inspect命令）'
    )

    args = parser.parse_args()

    if args.command == 'init':
        init_database()

    elif args.command == 'scrape':
        run_scraper(headless=args.headless)

    elif args.command == 'analyze':
        run_analysis()

    elif args.command == 'inspect':
        inspect_page(args.url)

    elif args.command == 'all':
        init_database()
        run_scraper(headless=args.headless)
        run_analysis()


if __name__ == "__main__":
    main()
