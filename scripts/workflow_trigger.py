#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Workflow Trigger - 文件监控触发器 + Notion轮询
监控trigger文件的变化，或定期检查Notion新物件，自动执行Prediction Workflow
"""

import os
import sys
import time
import subprocess
import logging
import requests
from datetime import datetime
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 配置
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
TRIGGER_FILE = PROJECT_DIR / "trigger" / "run_workflow.flag"
LOG_FILE = PROJECT_DIR / "logs" / "workflow_trigger.log"

# Notion轮询配置
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
NOTION_DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"
POLL_INTERVAL = 30 * 60  # 30分钟轮询一次

# 确保目录存在
TRIGGER_FILE.parent.mkdir(exist_ok=True)
LOG_FILE.parent.mkdir(exist_ok=True)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def check_notion_for_new_properties():
    """检查Notion是否有新物件需要评估（予測_view数为空）"""
    try:
        headers = {
            "Authorization": f"Bearer {NOTION_API_KEY}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
        payload = {
            "page_size": 1,  # 只需要知道是否有，不需要全部数据
            "filter": {
                "property": "予測_view数",
                "number": {"is_empty": True}
            }
        }
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            count = len(data.get("results", []))
            has_more = data.get("has_more", False)
            # 如果有结果或has_more为True，说明有新物件
            return count > 0 or has_more
        else:
            logger.warning(f"Notion API请求失败: {response.status_code}")
            return False
    except Exception as e:
        logger.warning(f"检查Notion失败: {e}")
        return False


class WorkflowTriggerHandler(FileSystemEventHandler):
    """文件变化处理器"""

    def __init__(self):
        self.last_trigger_time = 0
        self.cooldown = 30  # 30秒冷却时间，避免重复触发
        self.is_running = False

    def on_modified(self, event):
        if event.is_directory:
            return
        if Path(event.src_path).name == TRIGGER_FILE.name:
            self._handle_trigger()

    def on_created(self, event):
        if event.is_directory:
            return
        if Path(event.src_path).name == TRIGGER_FILE.name:
            self._handle_trigger()

    def _handle_trigger(self):
        """处理触发事件"""
        current_time = time.time()

        # 检查冷却时间
        if current_time - self.last_trigger_time < self.cooldown:
            logger.info(f"冷却中，跳过触发 (剩余 {self.cooldown - (current_time - self.last_trigger_time):.0f}秒)")
            return

        # 检查是否正在运行
        if self.is_running:
            logger.info("Workflow正在运行中，跳过")
            return

        self.last_trigger_time = current_time
        self.is_running = True

        try:
            logger.info("=" * 60)
            logger.info("检测到触发信号，开始执行Workflow")
            logger.info("=" * 60)

            self._run_workflow()

        except Exception as e:
            logger.error(f"Workflow执行失败: {e}")
        finally:
            self.is_running = False
            # 清空触发文件
            try:
                TRIGGER_FILE.write_text(f"Last run: {datetime.now()}\n", encoding='utf-8')
            except:
                pass

    def _run_workflow(self):
        """执行完整的Prediction Workflow"""
        steps = [
            ("Step 1: 预测view数", "predict_and_update_notion_v2.py"),
            ("Step 2: 检查管理公司", "check_management_company.py"),
            ("Step 3: 预测反响数", "predict_inquiry.py"),
            ("Step 4a: 市场排名分析", "suumo_rank_analysis.py"),
            ("Step 4b: 广告数统计", "fix_missing_ad.py"),
            ("Step 5: 计算推薦点数", "recommend_properties.py"),
            ("Step 6: 更新TOP推荐", "update_top_recommendations.py"),
        ]

        success_count = 0

        for step_name, script_name in steps:
            script_path = SCRIPT_DIR / script_name

            if not script_path.exists():
                logger.warning(f"脚本不存在: {script_name}，跳过")
                continue

            logger.info(f"\n{'='*40}")
            logger.info(f"执行: {step_name}")
            logger.info(f"{'='*40}")

            try:
                # 需要更长时间的步骤
                if "predict_and_update" in script_name:
                    step_timeout = 1800  # Step1: 30分钟（浏览器爬取）
                elif "predict_inquiry" in script_name:
                    step_timeout = 1800  # Step3: 30分钟（大量API调用）
                elif "recommend_properties" in script_name:
                    step_timeout = 2400  # Step5: 40分钟（大量物件处理）
                else:
                    step_timeout = 600   # 其他: 10分钟

                result = subprocess.run(
                    [sys.executable, str(script_path)],
                    cwd=str(PROJECT_DIR),
                    capture_output=True,
                    text=True,
                    timeout=step_timeout  # Step1: 30分钟, 其他: 10分钟
                )

                if result.returncode == 0:
                    logger.info(f"✓ {step_name} 完成")
                    success_count += 1

                    # Step 6: 记录おすすめ更新详情
                    if "update_top_recommendations" in script_name and result.stdout:
                        for line in result.stdout.split('\n'):
                            # 记录关键信息
                            if any(kw in line for kw in ['新着物件おすすめ:', '確認待ち物件:', '待添加:', '✓', '完成!']):
                                logger.info(f"  {line.strip()}")
                else:
                    logger.error(f"✗ {step_name} 失败")
                    if result.stderr:
                        logger.error(f"错误: {result.stderr[:500]}")

            except subprocess.TimeoutExpired:
                logger.error(f"✗ {step_name} 超时")
            except Exception as e:
                logger.error(f"✗ {step_name} 异常: {e}")

        logger.info(f"\n{'='*60}")
        logger.info(f"Workflow完成: {success_count}/{len(steps)} 步骤成功")
        logger.info(f"{'='*60}\n")


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("Workflow Trigger 启动")
    logger.info(f"监控文件: {TRIGGER_FILE}")
    logger.info("=" * 60)

    # 创建触发文件（如果不存在）
    if not TRIGGER_FILE.exists():
        TRIGGER_FILE.write_text("Ready\n", encoding='utf-8')
        logger.info(f"已创建触发文件: {TRIGGER_FILE}")

    # 设置文件监控
    event_handler = WorkflowTriggerHandler()
    observer = Observer()
    observer.schedule(event_handler, str(TRIGGER_FILE.parent), recursive=False)
    observer.start()

    logger.info("开始监控... (按Ctrl+C停止)")
    logger.info(f"触发方式1: 修改或更新 {TRIGGER_FILE}")
    logger.info(f"触发方式2: 每 {POLL_INTERVAL // 60} 分钟自动检查Notion新物件")

    # 睡眠检测参数
    SLEEP_DETECT_THRESHOLD = 60  # 如果循环间隔超过60秒，认为电脑经历了睡眠
    last_check_time = time.time()
    last_poll_time = time.time()  # 上次轮询Notion的时间

    try:
        while True:
            time.sleep(1)
            current_time = time.time()

            # 睡眠检测：如果距离上次检查超过阈值，说明电脑经历了睡眠
            time_gap = current_time - last_check_time
            if time_gap > SLEEP_DETECT_THRESHOLD:
                logger.warning(f"检测到系统恢复（暂停了 {time_gap:.0f} 秒），自动触发Workflow")
                event_handler._handle_trigger()
                last_poll_time = current_time  # 重置轮询时间

            # Notion轮询：定期检查是否有新物件
            poll_gap = current_time - last_poll_time
            if poll_gap >= POLL_INTERVAL and not event_handler.is_running:
                logger.info("定时检查Notion新物件...")
                if check_notion_for_new_properties():
                    logger.info("发现新物件，触发Workflow")
                    event_handler._handle_trigger()
                else:
                    logger.info("没有新物件需要评估")
                last_poll_time = current_time

            last_check_time = current_time

    except KeyboardInterrupt:
        logger.info("收到停止信号，正在关闭...")
        observer.stop()

    observer.join()
    logger.info("Workflow Trigger 已停止")


if __name__ == "__main__":
    main()
