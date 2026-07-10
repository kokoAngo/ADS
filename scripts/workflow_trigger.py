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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 从项目根的 .env 读取 NOTION_API_KEY 等
load_dotenv(Path(__file__).parent.parent / ".env")

# MAIN DB(新着物件)已迁 PostgreSQL；轮询新物件改查 PG。
# 必须在 load_dotenv 之后 import：pg_main 导入时读 FANGO_MAIN_DSN。
sys.path.insert(0, str(Path(__file__).parent))
import pg_main

# 阻止系统睡眠: Windows 用 SetThreadExecutionState, macOS 用 caffeinate
if sys.platform == 'win32':
    import ctypes
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
elif sys.platform == 'darwin':
    subprocess.Popen(['caffeinate', '-s', '-w', str(os.getpid())])

# 截止时间(JST) — 每天 11:00/15:00/19:00/23:00 是新物件登载截止时间
JST = timezone(timedelta(hours=9))
CUTOFF_HOURS = [11, 15, 19, 23]
CUTOFF_MINUTE = 0


def get_most_recent_cutoff():
    now = datetime.now(JST)
    today_cutoffs = [
        now.replace(hour=h, minute=CUTOFF_MINUTE, second=0, microsecond=0)
        for h in CUTOFF_HOURS
    ]
    past = [c for c in today_cutoffs if c <= now]
    if past:
        return max(past)
    return (now - timedelta(days=1)).replace(hour=23, minute=CUTOFF_MINUTE, second=0, microsecond=0)

# 配置
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
TRIGGER_FILE = PROJECT_DIR / "trigger" / "run_workflow.flag"
LOG_FILE = PROJECT_DIR / "logs" / "workflow_trigger.log"

# Notion轮询配置
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"
POLL_INTERVAL = 10 * 60  # 10分钟轮询一次

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


def check_bridge_inbox():
    """Bridge inbox の未読件数だけ logger に出す (本文は取らない、軽量)。失敗しても落ちない。"""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "bridge.py"), "inbox", "--count-only"],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=15,
        )
        msg = (result.stderr or result.stdout).strip()
        if msg:
            logger.info(f"[Bridge] {msg}")
    except Exception as e:
        logger.debug(f"Bridge inbox チェック失敗 (無視): {e}")


def check_pg_for_new_properties():
    """检查 PG(main.shinchaku_bukken)是否有当前 session 内新物件需要评估
    （predicted_view IS NULL 且 created_time > 当前截止时间）。
    2026-07 MAIN DB 迁 PG 后，新物件不再进 Notion 3031，故轮询改查 PG。"""
    try:
        cutoff = get_most_recent_cutoff()
        return pg_main.has_unscored(cutoff)
    except Exception as e:
        logger.warning(f"检查PG新物件失败: {e}")
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

            check_bridge_inbox()
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
        """执行完整的Prediction Workflow（V2: 单步逐物件流水线）"""
        steps = [
            ("Pipeline: 逐物件处理 (view→広告可→反响→市場順位→広告数→推薦点数→TOP)", "process_pipeline.py"),
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
                # process_pipeline 是长时间运行的统一流水线
                if "process_pipeline" in script_name:
                    step_timeout = 14400  # 4小时（处理大量积压物件）
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
                            if any(kw in line for kw in ['新着物件おすすめ:', '待添加:', '✓', '完成!']):
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

    # Bridge inbox の未読件数を 1 回出す (起動時のみ)
    check_bridge_inbox()

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
    logger.info(f"触发方式2: 每 {POLL_INTERVAL // 60} 分钟自动检查PG新物件")
    logger.info(f"触发方式3: 截止时刻(JST {','.join(f'{h}:{CUTOFF_MINUTE:02d}' for h in CUTOFF_HOURS)})到达时立即触发")

    # 睡眠检测参数
    SLEEP_DETECT_THRESHOLD = 60  # 如果循环间隔超过60秒，认为电脑经历了睡眠
    last_check_time = time.time()
    last_poll_time = time.time()  # 上次轮询Notion的时间
    last_known_cutoff = get_most_recent_cutoff()  # 上次已知的截止时间

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

            # 截止时刻检测：如果当前最近截止时间发生了变化，立即触发
            current_cutoff = get_most_recent_cutoff()
            if current_cutoff != last_known_cutoff:
                logger.warning(f"截止时刻到达 ({current_cutoff.strftime('%Y-%m-%d %H:%M JST')})，立即触发Workflow追最新物件")
                last_known_cutoff = current_cutoff
                event_handler._handle_trigger()
                last_poll_time = current_time

            # PG轮询：定期检查是否有新物件
            poll_gap = current_time - last_poll_time
            if poll_gap >= POLL_INTERVAL and not event_handler.is_running:
                logger.info("定时检查PG新物件...")
                if check_pg_for_new_properties():
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
