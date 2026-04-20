"""结构化事实提取：从工具调用结果中提取关键信息。

纯函数模块，无状态，零外部依赖（仅 json + os）。
用于上下文压缩时替代机械截断——用一行结构化事实替换完整工具输出。

依赖链：
    tools/registry.py  → dispatch() 返回 JSON 字符串
    core/fact_extractor.py  → 从 JSON 中提取事实（零依赖）
    core/model_client.py    → 压缩时使用事实
"""

import json
import os
from typing import Optional


# ---------------------------------------------------------------------------
# 文件扩展名 → 语言推断
# ---------------------------------------------------------------------------

_LANG_MAP = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".java": "Java", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
    ".cpp": "C++", ".c": "C", ".h": "C/C++", ".hpp": "C++",
    ".cs": "C#", ".php": "PHP", ".swift": "Swift", ".kt": "Kotlin",
    ".scala": "Scala", ".r": "R", ".R": "R", ".m": "Objective-C",
    ".sh": "Shell", ".bash": "Bash", ".zsh": "Zsh",
    ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".xml": "XML", ".md": "Markdown", ".rst": "reStructuredText",
}


def _guess_lang(path: str) -> Optional[str]:
    """从文件扩展名推断语言。"""
    _, ext = os.path.splitext(path)
    return _LANG_MAP.get(ext.lower())


# ---------------------------------------------------------------------------
# 工具类型 → 提取函数
# ---------------------------------------------------------------------------

def _extract_read_file(args: dict, result: dict) -> str:
    path = args.get("path", "?")
    total = result.get("total_lines", "?")
    shown = result.get("shown_lines", "?")
    parts = [f"[read_file] {path} ({total}行"]
    lang = _guess_lang(path)
    if lang:
        parts.append(f", {lang}")
    if shown != total and total != "?":
        parts.append(f", 已读{shown}行")
    return "".join(parts) + ")"


def _extract_write_file(args: dict, result: dict) -> str:
    path = args.get("path", "?")
    action = result.get("action", "written")
    size = result.get("bytes", 0)
    action_cn = "写入" if action == "written" else "追加"
    size_str = f"{size // 1024}KB" if size >= 1024 else f"{size}字节"
    return f"[write_file] {path} ({action_cn} {size_str})"


def _extract_search_files(args: dict, result: dict) -> str:
    pattern = args.get("pattern", "?")
    total = result.get("total", 0)
    path = args.get("path", ".")
    path_str = f" in {path}" if path != "." else ""
    truncated = " (截断)" if result.get("truncated") else ""
    return f'[search_files] "{pattern}"{path_str} → {total}处匹配{truncated}'


def _extract_terminal(args: dict, result: dict) -> str:
    cmd = args.get("command", "?")
    # 截断过长命令
    if len(cmd) > 60:
        cmd = cmd[:57] + "..."
    code = result.get("return_code", "?")
    success = result.get("success", True)
    output = result.get("output", "")

    lines = output.strip().splitlines() if output else []
    line_count = len(lines)

    status = f"退出码{code}" if code != "?" else "完成"
    if not success:
        status = f"退出码{code}(失败)"

    parts = [f"[terminal] {cmd} → {status}, {line_count}行输出"]

    # 附加末尾几行作为摘要
    if line_count > 0:
        tail = lines[-1][:80]
        parts.append(f"\n  末行: {tail}")

    return "".join(parts)


def _extract_web_search(args: dict, result: dict) -> str:
    query = args.get("query", "?")
    engine = result.get("engine", "?")
    results = result.get("results", [])
    engine_cn = {"baidu": "百度", "tavily": "Tavily"}.get(engine, engine)
    return f'[web_search] "{query}" ({engine_cn}, {len(results)}条结果)'


def _extract_web_read(args: dict, result: dict) -> str:
    url = args.get("url", "?")
    content = result.get("content", "")
    char_count = len(content)
    if char_count >= 10000:
        size_str = f"{char_count // 1000}K字"
    else:
        size_str = f"{char_count}字"
    return f"[web_read] {url[:80]} ({size_str})"


def _extract_clarify(args: dict, result: dict) -> str:
    answer = result.get("answer", result.get("response", ""))
    if not answer and isinstance(result, dict):
        # 尝试从 JSON 字符串中获取
        answer = str(result)[:100]
    return f"[clarify] 用户回答: {answer[:80]}"


# ---------------------------------------------------------------------------
# 分发表
# ---------------------------------------------------------------------------

_EXTRACTORS = {
    "read_file": _extract_read_file,
    "write_file": _extract_write_file,
    "search_files": _extract_search_files,
    "terminal": _extract_terminal,
    "web_search": _extract_web_search,
    "web_read": _extract_web_read,
    "clarify": _extract_clarify,
}


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def extract_fact(tool_name: str, args: dict, result_json: str) -> str:
    """从工具调用结果中提取一行结构化事实。

    Args:
        tool_name: 工具名称（如 "read_file"）
        args: 工具调用参数 dict
        result_json: 工具返回的 JSON 字符串

    Returns:
        一行结构化事实字符串，如 "[read_file] auth.py (120行, Python)"
    """
    # 解析结果 JSON
    try:
        result = json.loads(result_json)
    except (json.JSONDecodeError, TypeError):
        return f"[{tool_name}] ({len(result_json)}字结果)"

    if not isinstance(result, dict):
        return f"[{tool_name}] ({len(result_json)}字结果)"

    # 错误结果
    if "error" in result:
        err = result["error"]
        if len(err) > 100:
            err = err[:97] + "..."
        return f"[{tool_name}] FAIL {err}"

    # 分发到对应提取函数
    extractor = _EXTRACTORS.get(tool_name)
    if extractor:
        try:
            return extractor(args or {}, result)
        except Exception:
            pass

    # 通用回退
    return _extract_generic(tool_name, args, result)


def _extract_generic(tool_name: str, args: dict, result: dict) -> str:
    """通用事实提取：工具名 + 第一个参数值 + 结果大小。"""
    # 取第一个参数的值作为摘要
    first_val = ""
    if args:
        for key in ("path", "command", "query", "url", "name"):
            if key in args:
                first_val = str(args[key])[:60]
                break
        if not first_val:
            first_val = str(next(iter(args.values()), ""))[:60]

    success = result.get("success", "error" not in result)
    status = "" if success else " (失败)"
    return f"[{tool_name}] {first_val}{status}" if first_val else f"[{tool_name}]{status}"
