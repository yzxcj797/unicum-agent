"""recall 工具：从 L2 归档中检索被压缩的完整工具输出。

当 agent 需要回溯某个被压缩的细节时，使用此工具搜索归档。
"""

import json

from tools.registry import registry, tool_error, tool_result
from core.context_archive import archive


def _recall(query: str) -> str:
    entries = archive.recall(query)
    if not entries:
        return tool_error(f"未找到匹配 '{query}' 的归档记录")

    # 单条结果：返回完整内容
    if len(entries) == 1:
        e = entries[0]
        result_text = e["result"]
        if len(result_text) > 50000:
            result_text = result_text[:50000] + "\n\n[已截断]"
        return tool_result(
            found=True,
            tool=e["tool"],
            args=e["args"],
            fact=e["fact"],
            result=result_text,
        )

    # 多条结果：返回摘要列表，让 agent 用更精确的关键词再次搜索
    summaries = []
    for e in entries:
        summaries.append({
            "tool": e["tool"],
            "fact": e["fact"],
        })
    return tool_result(
        found=True,
        count=len(entries),
        matches=summaries,
        hint="多条匹配，请用更精确的关键词（如文件路径、工具调用ID）缩小范围",
    )


registry.register(
    name="recall",
    toolset="context",
    schema={
        "name": "recall",
        "description": (
            "回溯被压缩的工具调用详情。当需要查看之前已读取/搜索过的文件内容、"
            "终端完整输出等被压缩掉的信息时使用。"
            "搜索归档中的完整工具输出，支持按工具名、文件路径、关键词搜索。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词：工具调用ID、工具名(read_file/terminal等)、或文件路径等关键词",
                },
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: _recall(query=args["query"]),
    check_fn=lambda: len(archive) > 0,
)
