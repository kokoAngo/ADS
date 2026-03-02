#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Workflow Trigger - 文件监控触发器
监控trigger文件的变化，自动执行Prediction Workflow
"""

import os
import sys
import time
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 配置
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
TRIGGER_FILE = PROJECT_DIR / "trigger" / "run_workflow.flag"
LOG_FILE = PROJECT_DIR / "logs" / "workflow_trigger.log"

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
                result = subprocess.run(
                    [sys.executable, str(script_path)],
                    cwd=str(PROJECT_DIR),
                    capture_output=True,
                    text=True,
                    timeout=600  # 10分钟超时
                )

                if result.returncode == 0:
                    logger.info(f"✓ {step_name} 完成")
                    success_count += 1
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
    logger.info(f"触发方式: 修改或更新 {TRIGGER_FILE}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在关闭...")
        observer.stop()

    observer.join()
    logger.info("Workflow Trigger 已停止")


if __name__ == "__main__":
    main()
