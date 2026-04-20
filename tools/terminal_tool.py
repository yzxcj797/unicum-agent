"""终端执行工具。"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# 危险命令模式
_DESTRUCTIVE_PATTERNS = (
    "rm ", "rmdir ", "mv ", "del ", "format ",
    "shutdown", "reboot",
)

# 默认工作目录
_DEFAULT_CWD = str(Path.home())


def _is_destructive(cmd: str) -> bool:
    return any(pat in cmd.lower() for pat in _DESTRUCTIVE_PATTERNS)


def _run_terminal(command: str, cwd: str = None, timeout: int = 120) -> str:
    """执行终端命令并返回结果。"""
    work_dir = cwd or os.getcwd()

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += result.stderr

        # 截断过长输出
        max_chars = 50_000
        truncated = False
        if len(output) > max_chars:
            output = output[:max_chars]
            truncated = True

        return tool_result(
            success=result.returncode == 0,
            output=output,
            return_code=result.returncode,
            cwd=work_dir,
            truncated=truncated,
        )
    except subprocess.TimeoutExpired:
        return tool_error(f"Command timed out after {timeout}s")
    except Exception as e:
        return tool_error(f"{type(e).__name__}: {e}")


registry.register(
    name="terminal",
    toolset="terminal",
    schema={
        "name": "terminal",
        "description": (
            "在终端中执行 shell 命令。返回命令输出、退出码和工作目录。"
            "注意：修改/删除文件的命令需要用户确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "cwd": {
                    "type": "string",
                    "description": "工作目录（默认为当前目录）",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时秒数（默认 120）",
                },
            },
            "required": ["command"],
        },
    },
    handler=lambda args, **kw: _run_terminal(
        command=args["command"],
        cwd=args.get("cwd"),
        timeout=args.get("timeout", 120),
    ),
    check_fn=lambda: True,
)
