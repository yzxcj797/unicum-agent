"""工具注册中心。

每个工具文件在 import 时调用 registry.register() 自注册。
model_tools 启动时扫描 tools/*.py，自动发现所有工具。

依赖链（无循环）:
    tools/registry.py  (零依赖)
           ↑
    tools/*.py         (仅 import registry)
           ↑
    core/agent.py      (import tools)
"""

import ast
import importlib
import json
import logging
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AST 扫描 — 检测模块是否包含顶层 registry.register() 调用
# ---------------------------------------------------------------------------

def _is_registry_register_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "register"
        and isinstance(func.value, ast.Name)
        and func.value.id == "registry"
    )


def _module_registers_tools(module_path: Path) -> bool:
    """检查模块是否包含顶层 registry.register() 调用。"""
    try:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path))
    except (OSError, SyntaxError):
        return False
    return any(_is_registry_register_call(stmt) for stmt in tree.body)


def discover_builtin_tools(tools_dir: Optional[Path] = None) -> List[str]:
    """自动导入所有含 registry.register() 的工具模块，返回模块名列表。"""
    tools_path = Path(tools_dir) if tools_dir else Path(__file__).resolve().parent
    module_names = [
        f"tools.{p.stem}"
        for p in sorted(tools_path.glob("*.py"))
        if p.name not in {"__init__.py", "registry.py"}
        and _module_registers_tools(p)
    ]
    imported: List[str] = []
    for mod_name in module_names:
        try:
            importlib.import_module(mod_name)
            imported.append(mod_name)
        except Exception as e:
            logger.warning("Failed to import tool module %s: %s", mod_name, e)
    return imported


# ---------------------------------------------------------------------------
# ToolEntry & ToolRegistry
# ---------------------------------------------------------------------------

class ToolEntry:
    __slots__ = ("name", "toolset", "schema", "handler", "check_fn", "requires_env")

    def __init__(self, name, toolset, schema, handler, check_fn, requires_env):
        self.name = name
        self.toolset = toolset
        self.schema = schema
        self.handler = handler
        self.check_fn = check_fn
        self.requires_env = requires_env or []


class ToolRegistry:
    """单例注册中心，收集工具 schema + handler。"""

    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}
        self._toolset_checks: Dict[str, Callable] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        check_fn: Callable = None,
        requires_env: list = None,
    ):
        """注册一个工具。由工具文件在 import 时调用。"""
        with self._lock:
            existing = self._tools.get(name)
            if existing and existing.toolset != toolset:
                logger.error(
                    "Tool '%s' (toolset '%s') shadows existing toolset '%s'",
                    name, toolset, existing.toolset,
                )
                return
            self._tools[name] = ToolEntry(
                name=name,
                toolset=toolset,
                schema=schema,
                handler=handler,
                check_fn=check_fn,
                requires_env=requires_env or [],
            )
            if check_fn and toolset not in self._toolset_checks:
                self._toolset_checks[toolset] = check_fn

    def get_definitions(self, tool_names: Set[str]) -> List[dict]:
        """返回 OpenAI 格式的工具 schema 列表（仅包含可用的工具）。"""
        result = []
        with self._lock:
            for name in sorted(tool_names):
                entry = self._tools.get(name)
                if not entry:
                    continue
                if entry.check_fn:
                    try:
                        if not entry.check_fn():
                            continue
                    except Exception:
                        continue
                schema = {**entry.schema, "name": entry.name}
                result.append({"type": "function", "function": schema})
        return result

    def dispatch(self, name: str, args: dict, **kwargs) -> str:
        """执行工具 handler，统一返回 JSON 字符串。"""
        entry = self._tools.get(name) if name else None
        if not entry:
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
        # 校验必填参数
        required = entry.schema.get("parameters", {}).get("required", [])
        missing = [r for r in required if r not in (args or {})]
        if missing:
            return tool_error(f"Missing required parameters: {', '.join(missing)}")
        try:
            return entry.handler(args or {}, **kwargs)
        except Exception as e:
            logger.exception("Tool %s dispatch error", name)
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

    def get_all_tool_names(self) -> List[str]:
        with self._lock:
            return sorted(self._tools.keys())

    def get_toolset_for_tool(self, name: str) -> Optional[str]:
        entry = self._tools.get(name)
        return entry.toolset if entry else None

    def is_toolset_available(self, toolset: str) -> bool:
        with self._lock:
            check = self._toolset_checks.get(toolset)
        if not check:
            return True
        try:
            return bool(check())
        except Exception:
            return False

    def check_toolset_requirements(self) -> Dict[str, bool]:
        with self._lock:
            toolsets = {e.toolset for e in self._tools.values()}
            checks = dict(self._toolset_checks)
        result = {}
        for ts in sorted(toolsets):
            check = checks.get(ts)
            if not check:
                result[ts] = True
            else:
                try:
                    result[ts] = bool(check())
                except Exception:
                    result[ts] = False
        return result

    def get_available_toolsets(self) -> Dict[str, dict]:
        """返回每个 toolset 的元数据，用于 UI 展示。"""
        toolsets: Dict[str, dict] = {}
        with self._lock:
            for entry in self._tools.values():
                ts = entry.toolset
                if ts not in toolsets:
                    toolsets[ts] = {
                        "available": self.is_toolset_available(ts),
                        "tools": [],
                        "requirements": list(entry.requires_env),
                    }
                toolsets[ts]["tools"].append(entry.name)
        return toolsets


# 模块级单例
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# 便捷工具返回函数
# ---------------------------------------------------------------------------

def tool_error(message: str, **extra) -> str:
    result = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data=None, **kwargs) -> str:
    if data is not None:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(kwargs, ensure_ascii=False)
