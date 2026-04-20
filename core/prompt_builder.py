"""系统提示词构建器。"""

import os
import logging
from pathlib import Path
from typing import List, Optional

from my_agent.constants import get_home

logger = logging.getLogger(__name__)

DEFAULT_AGENT_IDENTITY = """\
你是一个强大的 AI 助手。你可以使用工具来完成用户交给你的任务。

核心原则:
- 使用工具前先思考：真的需要工具吗？还是直接回答更好？
- 每次只做必要的事，不要过度操作
- 文件操作要精确：读取指定范围，写入确认过的内容
- 执行终端命令前考虑安全性
- 如果不确定用户的意图，使用 clarify 工具提问
- 完成任务后简洁汇报结果
- 不要读回刚写入的文件来验证，直接信任写入结果

工具调用规则:
- 调用工具时不要在文本中重复工具参数的内容（如代码、文件内容），直接通过工具参数传递
- 回复要简短，把 token 留给工具参数
- 不要在文本回复中写代码，代码一律通过 write_file 工具写入
- 尽量在同一次回复中批量调用多个工具，减少交互轮次
- 在验证和总结阶段（所有文件已创建完毕后），不要再调用 write_file 或 edit_file
"""


def build_system_prompt(
    identity: str = None,
    platform: str = None,
    working_dir: str = None,
    extra_instructions: str = None,
    memory_text: str = None,
) -> str:
    """组装系统提示词。"""
    parts = []

    # 1. Agent 身份
    parts.append(identity or DEFAULT_AGENT_IDENTITY)

    # 2. 工作目录
    cwd = working_dir or os.getcwd()
    parts.append(f"\n当前工作目录: {cwd}")

    # 3. 平台提示
    if platform:
        parts.append(f"当前平台: {platform}")

    # 4. 用户自定义指令（SOUL.md）
    soul_content = _load_soul_md()
    if soul_content:
        parts.append(f"\n## 用户指令\n\n{soul_content}")

    # 5. 跨会话记忆
    if memory_text:
        parts.append(
            f"\n## 跨会话记忆\n\n"
            f"以下是之前会话中积累的知识，请主动利用：\n\n"
            f"{memory_text}\n\n"
            f"如果发现记忆中的信息过时或错误，可用 memory 工具更新或删除。"
        )

    # 6. 额外指令
    if extra_instructions:
        parts.append(f"\n{extra_instructions}")

    return "\n".join(parts)


def _load_soul_md() -> Optional[str]:
    """加载 SOUL.md 用户自定义人格/指令文件。"""
    home = get_home()
    for name in ("SOUL.md", "soul.md"):
        path = home / name
        if path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except Exception:
                pass

    # 也检查当前目录
    for name in ("SOUL.md", "AGENTS.md"):
        path = Path(name)
        if path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except Exception:
                pass
    return None
