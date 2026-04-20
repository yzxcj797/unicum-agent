"""L2 归档存储：保存被压缩掉的工具完整输出。

当上下文压缩将工具结果替换为简短摘要时，原始内容被存入此归档。
agent 可通过 recall 工具检索归档中的完整输出。

依赖链：
    core/context_archive.py  (零依赖)
           ↑
    core/model_client.py     (压缩时存入归档)
    tools/recall_tool.py     (recall 工具检索归档)
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class ContextArchive:
    """存储被压缩掉的工具完整输出（L2 层）。"""

    def __init__(self):
        self._entries: Dict[str, dict] = {}  # tool_call_id → entry

    def store(self, tool_call_id: str, tool_name: str, args: dict,
              result: str, fact: str):
        """存入一条完整的工具调用记录。"""
        self._entries[tool_call_id] = {
            "tool": tool_name,
            "args": args,
            "result": result,
            "fact": fact,
        }

    def recall(self, query: str) -> List[dict]:
        """搜索归档，返回匹配的完整记录。

        搜索策略（按优先级）：
        1. 精确匹配 tool_call_id → 返回单条
        2. 匹配 tool_name → 返回该工具的所有记录
        3. 关键词搜索 → 在 fact 和 args 中搜索
        """
        query = query.strip()
        if not query:
            return []

        # 1. 精确匹配 tool_call_id
        if query in self._entries:
            return [self._entries[query]]

        # 2. 匹配 tool_name
        tool_matches = [
            e for e in self._entries.values()
            if e["tool"] == query
        ]
        if tool_matches:
            return tool_matches

        # 3. 关键词搜索（fact + args 值）
        keyword = query.lower()
        results = []
        for entry in self._entries.values():
            # 搜索 fact
            if keyword in entry["fact"].lower():
                results.append(entry)
                continue
            # 搜索 args 的值
            for v in entry.get("args", {}).values():
                if keyword in str(v).lower():
                    results.append(entry)
                    break

        return results

    def get_all_facts(self) -> List[str]:
        """返回所有事实字符串，按存储顺序。"""
        return [e["fact"] for e in self._entries.values()]

    def format_recall_results(self, entries: List[dict]) -> str:
        """将检索结果格式化为可读文本。"""
        import json

        if not entries:
            return json.dumps(
                {"error": f"未找到匹配 '{entries}' 的记录"},
                ensure_ascii=False,
            )

        if len(entries) == 1:
            e = entries[0]
            return json.dumps({
                "found": True,
                "tool": e["tool"],
                "args": e["args"],
                "fact": e["fact"],
                "result_preview": e["result"][:500],
                "result_length": len(e["result"]),
            }, ensure_ascii=False)

        # 多条匹配：返回摘要列表
        summaries = []
        for e in entries:
            summaries.append({
                "tool": e["tool"],
                "fact": e["fact"],
                "args_preview": {k: str(v)[:50] for k, v in e.get("args", {}).items()},
                "result_length": len(e["result"]),
            })
        return json.dumps({
            "found": True,
            "count": len(entries),
            "matches": summaries,
            "hint": "使用更精确的关键词或 tool_call_id 查看完整结果",
        }, ensure_ascii=False)

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self):
        """清空归档。"""
        self._entries.clear()


# 模块级单例
archive = ContextArchive()
