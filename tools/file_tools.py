"""文件操作工具：读、写、搜索。"""

import json
import logging
import os
import re
from pathlib import Path

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# 单次读取最大字符数
_MAX_READ_CHARS = 100_000

# 设备路径黑名单
_BLOCKED_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/null",
})


def _is_blocked(filepath: str) -> bool:
    return filepath in _BLOCKED_PATHS


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

def _read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    if _is_blocked(path):
        return tool_error(f"Blocked device path: {path}")

    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return tool_error(f"File not found: {path}")
        if p.is_dir():
            return tool_error(f"Path is a directory, not a file: {path}")

        content = p.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        selected = lines[offset:offset + limit]
        result_text = "\n".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(selected))

        total_lines = len(lines)
        shown_lines = len(selected)

        meta = {
            "success": True,
            "path": str(p),
            "total_lines": total_lines,
            "shown_lines": shown_lines,
            "offset": offset,
        }

        if total_lines > shown_lines:
            meta["hint"] = f"Showing {shown_lines}/{total_lines} lines. Use offset/limit to read more."

        return json.dumps(meta, ensure_ascii=False)[:-1] + ',\n"content": ' + json.dumps(result_text, ensure_ascii=False) + '}'
    except Exception as e:
        return tool_error(f"{type(e).__name__}: {e}")


registry.register(
    name="read_file",
    toolset="file",
    schema={
        "name": "read_file",
        "description": "读取文件内容，返回带行号的文本。支持 offset 和 limit 参数分段读取大文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "offset": {"type": "integer", "description": "起始行号（0-based，默认 0）"},
                "limit": {"type": "integer", "description": "读取行数（默认 2000）"},
            },
            "required": ["path"],
        },
    },
    handler=lambda args, **kw: _read_file(
        path=args["path"],
        offset=args.get("offset", 0),
        limit=args.get("limit", 2000),
    ),
    check_fn=lambda: True,
)


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

def _write_file(path: str, content: str, append: bool = False) -> str:
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if append else "w"
        p.write_text(content, encoding="utf-8")

        return tool_result(
            success=True,
            path=str(p),
            action="appended" if append else "written",
            bytes=len(content.encode("utf-8")),
        )
    except Exception as e:
        return tool_error(f"{type(e).__name__}: {e}")


registry.register(
    name="write_file",
    toolset="file",
    schema={
        "name": "write_file",
        "description": "写入文件。默认覆盖写入，设置 append=true 追加。自动创建不存在的父目录。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
                "append": {"type": "boolean", "description": "是否追加模式（默认 false）"},
            },
            "required": ["path", "content"],
        },
    },
    handler=lambda args, **kw: _write_file(
        path=args["path"],
        content=args["content"],
        append=args.get("append", False),
    ),
    check_fn=lambda: True,
)


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------

def _search_files(pattern: str, path: str = ".", file_glob: str = "*", max_results: int = 50) -> str:
    """搜索文件内容（grep 风格）或文件名（glob 风格）。"""
    try:
        search_path = Path(path).expanduser().resolve()
        if not search_path.exists():
            return tool_error(f"Path not found: {path}")

        matches = []
        regex = re.compile(pattern, re.IGNORECASE)

        for filepath in search_path.rglob(file_glob):
            if len(matches) >= max_results:
                break
            # 跳过二进制目录和隐藏文件
            if any(part.startswith(".") for part in filepath.relative_to(search_path).parts):
                continue
            if filepath.is_dir():
                continue
            try:
                # 检查文件名匹配
                if regex.search(filepath.name):
                    matches.append({"file": str(filepath.relative_to(search_path)), "match": "filename"})
                    continue
                # 检查文件内容
                text = filepath.read_text(encoding="utf-8", errors="ignore")
                for i, line in enumerate(text.splitlines()):
                    if regex.search(line):
                        matches.append({
                            "file": str(filepath.relative_to(search_path)),
                            "line": i + 1,
                            "text": line.strip()[:200],
                        })
                        if len(matches) >= max_results:
                            break
            except Exception:
                continue

        return tool_result(
            success=True,
            pattern=pattern,
            path=str(search_path),
            matches=matches,
            total=len(matches),
            truncated=len(matches) >= max_results,
        )
    except re.error as e:
        return tool_error(f"Invalid regex: {e}")
    except Exception as e:
        return tool_error(f"{type(e).__name__}: {e}")


registry.register(
    name="search_files",
    toolset="file",
    schema={
        "name": "search_files",
        "description": "搜索文件名和文件内容。支持正则表达式。自动跳过隐藏目录和 .git。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索模式（正则表达式）"},
                "path": {"type": "string", "description": "搜索根目录（默认当前目录）"},
                "file_glob": {"type": "string", "description": "文件名 glob（默认 *）"},
                "max_results": {"type": "integer", "description": "最大结果数（默认 50）"},
            },
            "required": ["pattern"],
        },
    },
    handler=lambda args, **kw: _search_files(
        pattern=args["pattern"],
        path=args.get("path", "."),
        file_glob=args.get("file_glob", "*"),
        max_results=args.get("max_results", 50),
    ),
    check_fn=lambda: True,
)
