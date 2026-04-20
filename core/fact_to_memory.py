"""自动记忆提取：从 facts 纯规则推导出跨会话 memory。

零 LLM 成本，纯正则解析 fact 文本格式。
与 fact_extractor.py 配对：fact_extractor 负责"工具结果 → fact"，
本模块负责"fact → memory 条目"。

依赖链：
    core/fact_extractor.py  (零依赖) — 生成 fact
    core/fact_to_memory.py  (零依赖) — 解析 fact → memory
    core/agent.py           — 调用
"""

import logging
import os
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 类型别名
FactEntry = Tuple[str, dict, str]          # (tool_name, args, fact)
MemoryEntry = Tuple[str, str, str, str]    # (scope, category, key, value)

# ── 过滤：噪音路径 ──────────────────────────────────────────────────────

_NOISE_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", "venv", ".venv",
    "dist", "build", "__pypackages__", ".next", ".nuxt",
})

_TEMP_EXTS = frozenset({".tmp", ".log", ".bak", ".swp", ".pyc", ".cache"})


def _is_noise_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    if any(p in _NOISE_DIRS for p in parts):
        return True
    _, ext = os.path.splitext(path)
    return ext.lower() in _TEMP_EXTS


# ── 语言推断 ────────────────────────────────────────────────────────────

_LANG_MAP = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".java": "Java", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
    ".cpp": "C++", ".c": "C", ".h": "C/C++", ".hpp": "C++",
    ".cs": "C#", ".php": "PHP", ".swift": "Swift", ".kt": "Kotlin",
    ".scala": "Scala", ".sh": "Shell", ".bash": "Bash",
    ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".xml": "XML", ".md": "Markdown",
}


def _infer_lang(path: str) -> Optional[str]:
    _, ext = os.path.splitext(path)
    return _LANG_MAP.get(ext.lower())


def _basename(path: str) -> str:
    return os.path.basename(path)


# ── 工具签名：哪些命令值得记录 ──────────────────────────────────────────

_TOOL_SIGNATURES = {
    "npm": "npm", "yarn": "yarn", "pnpm": "pnpm", "bun": "bun",
    "pip": "pip", "pip3": "pip", "poetry": "poetry", "uv": "uv",
    "pytest": "pytest", "jest": "jest", "vitest": "vitest", "mocha": "mocha",
    "cargo": "cargo", "go": "go", "make": "make", "cmake": "cmake",
    "gradle": "gradle", "mvn": "mvn", "dotnet": "dotnet",
    "python": "python", "node": "node", "ruby": "ruby",
}


# ── 提取函数 ────────────────────────────────────────────────────────────

def _extract_read_file(args: dict, fact: str) -> Optional[MemoryEntry]:
    """[read_file] src/auth.py (120行, Python) → files.auth.py"""
    if "FAIL" in fact:
        return None
    path = args.get("path", "")
    if not path or _is_noise_path(path):
        return None

    m = re.match(r'\[read_file\]\s+\S+\s+\((\d+)行', fact)
    if m:
        lines = int(m.group(1))
        if lines < 10:
            return None

    lang = _infer_lang(path)
    # 从 fact 提取行数
    line_info = ""
    m = re.match(r'\[read_file\]\s+\S+\s+\((\d+)行', fact)
    if m:
        line_info = f"{m.group(1)}行"

    parts = []
    if lang:
        parts.append(lang)
    if line_info:
        parts.append(line_info)
    value = ", ".join(parts) if parts else "已读取"

    key = f"files.{_basename(path)}"
    return ("project", "project", key, value)


def _extract_write_file(args: dict, fact: str) -> Optional[MemoryEntry]:
    """[write_file] auth.py (写入 2KB) → files.auth.py = "Python, 已修改" """
    if "FAIL" in fact:
        return None
    path = args.get("path", "")
    if not path or _is_noise_path(path):
        return None

    m = re.match(r'\[write_file\]\s+\S+\s+\((写入|追加)\s', fact)
    action = "已修改" if (m and m.group(1) == "写入") else "已追加"

    lang = _infer_lang(path)
    parts = []
    if lang:
        parts.append(lang)
    parts.append(action)
    value = ", ".join(parts)

    key = f"files.{_basename(path)}"
    return ("project", "project", key, value)


def _extract_terminal(args: dict, fact: str) -> Optional[MemoryEntry]:
    """[terminal] npm test → 退出码0, 47行输出 → commands.npm"""
    if "FAIL" in fact:
        return None

    m = re.match(
        r'\[terminal\]\s+(.+?)\s+→\s+退出码(\d+)(\(失败\))?,\s+(\d+)行输出',
        fact,
    )
    if not m:
        return None

    cmd, exit_code, failed, line_count = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))

    # 过滤失败和短输出
    if failed or exit_code != 0:
        return None
    if line_count < 5:
        return None

    # 提取命令第一个词的 basename
    first_word = cmd.split()[0]
    cmd_name = os.path.basename(first_word)

    signature = _TOOL_SIGNATURES.get(cmd_name)
    if not signature:
        return None

    # 完整命令摘要（截断过长命令）
    cmd_display = cmd if len(cmd) <= 40 else cmd[:37] + "..."
    value = f"项目使用 {signature} ({cmd_display} 成功)"

    key = f"commands.{signature}"
    return ("project", "project", key, value)


# ── 分发表 ──────────────────────────────────────────────────────────────

_EXTRACTORS = {
    "read_file": _extract_read_file,
    "write_file": _extract_write_file,
    "terminal": _extract_terminal,
}


# ── 去重 ────────────────────────────────────────────────────────────────

def _deduplicate(entries: List[MemoryEntry]) -> List[MemoryEntry]:
    """同一 (scope, category, key) 保留最后一条。"""
    seen: Dict[Tuple[str, str, str], MemoryEntry] = {}
    for entry in entries:
        key = (entry[0], entry[1], entry[2])
        seen[key] = entry
    return list(seen.values())


# ── 公开接口 ────────────────────────────────────────────────────────────

def extract_memories(entries: List[FactEntry]) -> List[MemoryEntry]:
    """从会话的所有 fact 推导出 memory 条目。

    Args:
        entries: [(tool_name, args_dict, fact_text), ...] 的列表

    Returns:
        去重后的 [(scope, category, key, value), ...] 列表
    """
    memories: List[MemoryEntry] = []
    for tool_name, args, fact in entries:
        extractor = _EXTRACTORS.get(tool_name)
        if not extractor:
            continue
        try:
            result = extractor(args or {}, fact)
            if result:
                memories.append(result)
        except Exception as e:
            logger.debug("fact_to_memory skip %s: %s", tool_name, e)

    return _deduplicate(memories)
