"""memory 工具：管理跨会话的结构化记忆。

支持 add/remove/list 三种操作，global/project 两种作用域。"""

import json

from tools.registry import registry, tool_error, tool_result
from core.memory_store import memory_store


def _memory(action: str, category: str, key: str, value: str = None,
            scope: str = "project") -> str:
    if action == "add":
        if not value:
            return tool_error("add 操作需要提供 value 参数")
        memory_store.add(scope, category, key, value)
        return tool_result(ok=True, message=f"已保存: {scope}/{category}/{key}")

    if action == "remove":
        ok = memory_store.remove(scope, category, key)
        if not ok:
            return tool_error(f"未找到: {scope}/{category}/{key}")
        return tool_result(ok=True, message=f"已删除: {scope}/{category}/{key}")

    if action == "list":
        entries = memory_store.list_entries(scope)
        return tool_result(scope=scope, entries=entries)

    return tool_error(f"未知 action: {action}，支持 add/remove/list")


registry.register(
    name="memory",
    toolset="context",
    schema={
        "name": "memory",
        "description": (
            "管理跨会话的结构化记忆。保存用户偏好、项目约定、文件信息等，"
            "重启 agent 后仍然保留。"
            "action: add(添加)/remove(删除)/list(列出)。"
            "scope: global(全局)/project(项目级，默认)。"
            "category: user(用户偏好)/project(项目信息)/environment(环境)。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "remove", "list"],
                    "description": "操作类型",
                },
                "category": {
                    "type": "string",
                    "description": "分类: user(用户偏好/角色) / project(文件/约定/笔记) / environment(系统/工具)",
                },
                "key": {
                    "type": "string",
                    "description": "键名，支持嵌套如 'files.auth.py'",
                },
                "value": {
                    "type": "string",
                    "description": "值（add 时必填）",
                },
                "scope": {
                    "type": "string",
                    "enum": ["global", "project"],
                    "description": "作用域: global(跨项目) / project(当前项目，默认)",
                },
            },
            "required": ["action", "category", "key"],
        },
    },
    handler=lambda args, **kw: _memory(
        action=args["action"],
        category=args["category"],
        key=args["key"],
        value=args.get("value"),
        scope=args.get("scope", "project"),
    ),
)
