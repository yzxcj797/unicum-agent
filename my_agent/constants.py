"""My-Agent 全局常量。零依赖，可从任何模块安全导入。"""

import os
from enum import Enum
from pathlib import Path


def get_home() -> Path:
    """返回 my-agent 主目录（默认 ~/.my-agent）。"""
    return Path(os.getenv("MY_AGENT_HOME", Path.home() / ".my-agent"))


# 默认模型（由 config.get_default_model() 自动检测）
DEFAULT_MODEL = "glm-5.1"

# 默认最大迭代次数
DEFAULT_MAX_ITERATIONS = 50


class TaskPhase(Enum):
    """Agent 任务阶段，用于阶段感知的上下文压缩。"""
    PLANNING = "planning"    # 还没写过文件，在读/搜索/规划
    EXECUTING = "executing"  # 正在写文件
    VERIFYING = "verifying"  # 写完了，在测试/验证/总结

# Agent 循环中每次工具调用后的延迟（秒）
DEFAULT_TOOL_DELAY = 0.5
