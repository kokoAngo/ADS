"""
配置文件
"""
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 登录凭证
SUMMO_USERNAME = os.getenv("SUMMO_USERNAME")
SUMMO_PASSWORD = os.getenv("SUMMO_PASSWORD")

# 数据库配置
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/properties.db")

# 网站配置
# 登录入口页面（原URL中的id是会话ID，已过期）
BASE_URL = "https://www.fn.forrent.jp/fn/"

# 抓取配置
MIN_RESPONSE_COUNT = 10  # 最小推定反響数（件/月）

# 东京各区列表（将在抓取时动态获取）
TOKYO_AREAS = []
