#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
触发Workflow执行
其他Python app可以调用此脚本或import此模块来触发Prediction Workflow
"""

from pathlib import Path
from datetime import datetime


def trigger_workflow(message: str = None):
    """
    触发Workflow执行

    Args:
        message: 可选的触发消息，会写入flag文件

    Example:
        from trigger.trigger_workflow import trigger_workflow
        trigger_workflow("新物件已更新")
    """
    trigger_file = Path(__file__).parent / "run_workflow.flag"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"{timestamp} - Triggered"
    if message:
        content += f": {message}"
    content += "\n"

    # 追加写入以触发文件变化
    with open(trigger_file, 'a', encoding='utf-8') as f:
        f.write(content)

    print(f"Workflow triggered at {timestamp}")
    return True


if __name__ == "__main__":
    import sys
    message = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    trigger_workflow(message)
