#!/usr/bin/env python3
"""My-Agent 交互式 CLI。

基于 prompt_toolkit + rich 的终端界面。
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from my_agent.config import load_config, load_dotenv_file, detect_provider
from my_agent.logging import setup_logging
from my_agent.constants import get_home, DEFAULT_MODEL
from core.agent import AIAgent
from core.memory_store import memory_store


# ── 对话日志 ──────────────────────────────────────────────────────────

class ConversationLogger:
    """实时将对话记录写入 Markdown 文件。"""

    def __init__(self, working_dir: str):
        self._path = Path(working_dir).resolve() / "conversation.md"
        is_new = not self._path.exists()
        if is_new:
            self._write(f"# 对话记录\n\n")
        self._write(f"---\n\n**会话开始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n")

    def _write(self, text: str):
        self._path.open("a", encoding="utf-8").write(text)

    def log_user(self, message: str):
        self._write(f"\n## 用户\n\n{message}\n")

    def log_agent(self, response: str):
        self._write(f"\n## Agent\n\n{response}\n")

    def log_tool(self, name: str, args: dict, success: bool):
        icon = "✓" if success else "✗"
        preview = json.dumps(args, ensure_ascii=False)[:120]
        self._write(f"\n> {icon} `{name}` {preview}\n")

    def log_path(self):
        return str(self._path)


_conv_logger: ConversationLogger = None

logger = logging.getLogger(__name__)
console = Console()

# ── 状态动画控制 ──────────────────────────────────────────────────────
_status = None
_stream_buffer = ""


# ── 计时模式 ──────────────────────────────────────────────────────────
_start_time: float = 0.0
_show_time: bool = False
_timing_events: list = []


def _elapsed() -> str:
    """返回相对于任务启动的耗时字符串。"""
    if _start_time <= 0:
        return ""
    seconds = time.time() - _start_time
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m{secs:.0f}s"


def _record_event(label: str):
    """记录一个计时事件。"""
    if not _show_time:
        return
    _timing_events.append(f"[{_elapsed()}] {label}")


def _save_timing_log(user_input: str):
    """将计时日志写入工作目录的 txt 文件。"""
    if not _timing_events:
        return
    project_root = Path(os.getcwd())
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = project_root / f"timing_{timestamp}.txt"
    header = (
        f"My-Agent 计时日志\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"任务: {user_input[:100]}\n"
        f"{'─' * 50}\n"
    )
    filename.write_text(header + "\n".join(_timing_events) + "\n", encoding="utf-8")
    console.print(f"[dim]计时日志已保存: {filename}[/dim]")


def _on_stream_text(text: str):
    """流式输出回调：停掉转圈动画，直接输出文本。"""
    global _stream_buffer
    _stream_buffer += text
    if _status:
        _status.stop()
    sys.stdout.write(text)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

def show_banner(provider_name: str = None, model: str = None):
    info = f"[bold cyan]My-Agent[/bold cyan] v0.1.0"
    if provider_name:
        info += f"\nProvider: [green]{provider_name}[/green] | Model: [green]{model}[/green]"
    info += "\nType [bold]/help[/bold] for commands, [bold]/quit[/bold] to exit"
    console.print(Panel(
        info,
        border_style="cyan",
        title="My-Agent",
        title_align="left",
    ))


# ---------------------------------------------------------------------------
# 工具进度显示
# ---------------------------------------------------------------------------

def on_iteration(iteration: int, model: str):
    """每轮 LLM 调用前的回调，打印当前模型。"""
    ts = f"[{_elapsed()}] " if _show_time else ""
    console.print(f"  [dim]🔄 {ts}第{iteration}轮 | {model}[/dim]")


def on_tool_start(name: str, args: dict):
    """工具开始执行时的回调。"""
    global _stream_buffer
    if _stream_buffer:
        _stream_buffer = ""
        sys.stdout.write("\n")
        sys.stdout.flush()
    if _status:
        _status.start()
    preview = json.dumps(args, ensure_ascii=False)[:80]
    ts = f"[cyan][{_elapsed()}][/cyan] " if _show_time else ""
    console.print(f"  [dim]⏳ {ts}{name}({preview}...)[/dim]")
    _record_event(f"工具开始: {name}({preview})")


def on_clarify(question: str, choices: list = None) -> str:
    """clarify 工具回调：暂停状态动画，展示问题并等待用户回答。"""
    # 暂停状态动画
    if _status:
        _status.stop()

    console.print(f"\n[bold yellow]Agent 提问:[/bold yellow] {question}")
    if choices:
        for i, c in enumerate(choices, 1):
            console.print(f"  [cyan]{i}.[/cyan] {c}")
    try:
        answer = input("你的回答> ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if not answer:
        answer = "1"
    if choices and answer.isdigit():
        idx = int(answer) - 1
        if 0 <= idx < len(choices):
            answer = choices[idx]
    console.print()

    # 恢复状态动画
    if _status:
        _status.start()
    return answer


def on_tool_complete(name: str, args: dict, result: str):
    """工具完成时的回调。"""
    try:
        parsed = json.loads(result)
        success = parsed.get("success", "error" not in parsed)
    except Exception:
        success = False

    icon = "[green]✓[/green]" if success else "[red]✗[/red]"
    status = "成功" if success else "失败"
    ts = f"[cyan][{_elapsed()}][/cyan] " if _show_time else ""
    extra = ""
    if name == "web_search" and success and isinstance(parsed, dict):
        engine = parsed.get("engine", "")
        if engine:
            extra = f" [dim]({engine})[/dim]"
    console.print(f"  {icon} [dim]{ts}{name}{extra}[/dim]")
    _record_event(f"工具完成: {name} ({status})")
    if _conv_logger:
        _conv_logger.log_tool(name, args, success)


# ---------------------------------------------------------------------------
# 斜杠命令
# ---------------------------------------------------------------------------

COMMANDS = {
    "help": "显示帮助",
    "new": "新建会话",
    "model": "查看/切换模型",
    "tools": "查看工具状态",
    "usage": "查看当前会话使用情况",
    "cd": "切换工作目录",
    "pwd": "查看当前工作目录",
    "reset": "重置会话",
    "quit": "退出",
    "exit": "退出",
}


def process_command(agent: AIAgent, cmd: str) -> bool:
    """处理斜杠命令。返回 False 表示退出。"""
    parts = cmd.strip().split(maxsplit=1)
    name = parts[0].lstrip("/").lower()
    arg = parts[1] if len(parts) > 1 else ""

    if name in ("quit", "exit"):
        console.print("[dim]再见！[/dim]")
        return False

    if name == "help":
        console.print("\n[bold]命令列表:[/bold]")
        for cmd_name, desc in COMMANDS.items():
            console.print(f"  [cyan]/{cmd_name}[/cyan]  {desc}")
        console.print()
        return True

    if name == "new" or name == "reset":
        agent.reset()
        console.print("[green]会话已重置[/green]")
        return True

    if name == "model":
        if arg:
            console.print(f"[yellow]切换模型功能暂未实现，当前模型: {agent.model or DEFAULT_MODEL}[/yellow]")
        else:
            console.print(f"当前模型: [cyan]{agent.model or DEFAULT_MODEL}[/cyan]")
        return True

    if name == "tools":
        toolsets = registry.get_available_toolsets()
        for ts_name, info in sorted(toolsets.items()):
            status = "[green]✓[/green]" if info["available"] else "[red]✗[/red]"
            tools_list = ", ".join(info["tools"])
            console.print(f"  {status} [cyan]{ts_name}[/cyan]: {tools_list}")
        return True

    if name == "usage":
        console.print(agent.get_usage_summary())
        return True

    if name == "cd":
        if not arg:
            console.print("[yellow]用法: /cd <目录路径>[/yellow]")
            return True
        target = os.path.expanduser(arg)
        if not os.path.isdir(target):
            console.print(f"[red]目录不存在: {arg}[/red]")
            return True
        os.chdir(target)
        agent.working_dir = os.getcwd()
        memory_store.set_project(agent.working_dir)
        agent.reset()  # 切换目录后重置会话以更新系统提示词中的工作目录
        console.print(f"[green]工作目录已切换到: {agent.working_dir}[/green]")
        return True

    if name == "pwd":
        console.print(f"当前工作目录: [cyan]{agent.working_dir}[/cyan]")
        return True

    console.print(f"[yellow]未知命令: /{name}，输入 /help 查看帮助[/yellow]")
    return True


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def get_input(prompt_text: str = "你> ") -> str:
    """获取用户输入，支持多行。"""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory

        history_path = get_home() / "cli_history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        session = PromptSession(history=FileHistory(str(history_path)))
        return session.prompt(prompt_text, multiline=False)
    except ImportError:
        return input(prompt_text)


def _parse_args():
    """简单解析命令行参数。"""
    args = sys.argv[1:]
    working_dir = None
    filtered = []
    i = 0
    while i < len(args):
        if args[i] in ("--dir", "-d") and i + 1 < len(args):
            working_dir = args[i + 1]
            i += 2
        else:
            filtered.append(args[i])
            i += 1
    verbose = "--verbose" in filtered or "-v" in filtered
    debug = "--debug" in filtered
    show_time = "--time" in filtered
    return working_dir, verbose, debug, show_time


def _debug_print(label: str, data):
    """调试模式下的打印回调。"""
    console.print(f"\n[bold yellow]{'─' * 20} {label} {'─' * 20}[/bold yellow]")
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    console.print(text)
    console.print()


def main():
    """CLI 入口。"""
    global _start_time, _show_time, _conv_logger
    working_dir, verbose, debug, show_time = _parse_args()
    _show_time = show_time

    # 初始化
    load_dotenv_file()
    config = load_config()
    setup_logging(verbose)

    # 检测提供商
    provider = detect_provider()
    p_name = provider["name"] if provider else "unknown"
    p_model = config.get("model") or (provider["default_model"] if provider else "unknown")
    show_banner(provider_name=p_name, model=p_model)
    if working_dir:
        console.print(f"[dim]工作目录: {os.path.expanduser(working_dir)}[/dim]")
    if debug:
        console.print("[bold yellow][调试模式] 将打印所有 API 请求/响应原始数据[/bold yellow]")
    if show_time:
        console.print("[bold cyan][计时模式] 将显示每个关键节点的耗时[/bold cyan]")

    # 创建 Agent
    try:
        agent = AIAgent(
            model=config.get("model"),
            max_iterations=config.get("max_iterations", 50),
            tool_delay=config.get("tool_delay", 0.5),
            enabled_toolsets=config.get("enabled_toolsets"),
            disabled_toolsets=config.get("disabled_toolsets"),
            platform="cli",
            working_dir=working_dir,
            clarify_callback=on_clarify,
            tool_start_callback=on_tool_start,
            tool_complete_callback=on_tool_complete,
            verbose=verbose,
            debug_callback=_debug_print if debug else None,
            stream_callback=_on_stream_text,
            fast_model=config.get("fast_model"),
            iteration_callback=on_iteration,
        )
    except ValueError as e:
        console.print(f"[red]初始化失败: {e}[/red]")
        console.print("[dim]请在 .env 中设置 API Key（支持智谱/OpenAI/Anthropic/OpenRouter）[/dim]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]初始化失败: {e}[/red]")
        sys.exit(1)

    # 需要在 agent 初始化后才能 import registry（工具已注册）
    from tools.registry import registry

    # 初始化对话日志
    _conv_logger = ConversationLogger(agent.working_dir)
    console.print(f"[dim]对话日志: {_conv_logger.log_path()}[/dim]")

    console.print("[dim]输入消息开始对话，/help 查看命令[/dim]\n")

    # 主循环
    while True:
        try:
            user_input = get_input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见！[/dim]")
            break

        if not user_input:
            continue

        # 斜杠命令
        if user_input.startswith("/"):
            if not process_command(agent, user_input):
                break
            continue

        # 正常对话
        try:
            _start_time = time.time()
            _timing_events.clear()
            global _stream_buffer
            _stream_buffer = ""

            # 记录用户输入
            _conv_logger.log_user(user_input)

            if _show_time:
                console.print(f"[cyan][{_elapsed()}][/cyan] 任务开始")
                _record_event("任务开始")

            with console.status("[bold cyan]思考中...[/bold cyan]") as status:
                global _status
                _status = status
                result = agent.run_conversation(user_input)
            _status = None

            response = result.get("response", "")
            if _stream_buffer:
                # 流式已输出，只需补换行
                sys.stdout.write("\n")
                sys.stdout.flush()
                # 流式内容即为 agent 响应
                _conv_logger.log_agent(_stream_buffer)
            elif response:
                console.print()  # 空行
                console.print(Panel(
                    Markdown(response),
                    border_style="blue",
                    title=f"Agent [{_elapsed()}]" if _show_time else "Agent",
                    title_align="left",
                ))
                console.print()  # 空行
                _conv_logger.log_agent(response)

            if _show_time:
                console.print(f"[cyan][{_elapsed()}][/cyan] 任务完成")
                _record_event("任务完成")
                _record_event(f"统计: 工具调用 {result.get('tool_calls', 0)} 次, "
                              f"迭代 {result.get('iterations', 0)} 轮, 总耗时 {_elapsed()}")
                _save_timing_log(user_input)
            if result.get("tool_calls", 0) > 0:
                console.print(f"[dim]{agent.get_usage_summary()}[/dim]\n")

        except KeyboardInterrupt:
            console.print("\n[yellow]已中断[/yellow]")
        except Exception as e:
            import traceback
            console.print(f"[red]错误: {e}[/red]")
            console.print(f"[dim red]{traceback.format_exc()}[/dim red]")


if __name__ == "__main__":
    main()
