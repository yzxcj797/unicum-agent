"""澄清问题工具 — Agent 向用户提问以获取更多信息。"""

import json

from tools.registry import registry, tool_error, tool_result


def _clarify(question: str, choices: list = None, **kwargs) -> str:
    """向用户提问。如果有 clarify_callback，通过回调向用户展示。"""
    callback = kwargs.get("clarify_callback")
    if callback:
        answer = callback(question, choices)
        return json.dumps({"question": question, "answer": answer}, ensure_ascii=False)

    # 无回调时返回问题信息，让 Agent 自行决定
    result = {"question": question}
    if choices:
        result["choices"] = choices
        result["hint"] = "Please choose one of the above options."
    return json.dumps(result, ensure_ascii=False)


registry.register(
    name="clarify",
    toolset="interaction",
    schema={
        "name": "clarify",
        "description": (
            "当你需要向用户提问以澄清意图或获取更多信息时使用此工具。"
            "提供问题和可选的选择列表。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要向用户提出的问题",
                },
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选的选项列表",
                },
            },
            "required": ["question"],
        },
    },
    handler=lambda args, **kw: _clarify(
        question=args["question"],
        choices=args.get("choices"),
        **kw,
    ),
    check_fn=lambda: True,
)
