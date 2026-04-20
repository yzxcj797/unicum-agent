"""从 Claude Code 的 JSONL 对话记录中提取完整对话，生成 Markdown 文件。

用法: python extract_conversation.py [jsonl_path] [output_path]
"""

import json
import sys
import os
from pathlib import Path


def extract_text(content) -> str:
    """从 content 字段中提取纯文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            itype = item.get("type", "")
            if itype == "text":
                text = item.get("text", "")
                if text.strip():
                    parts.append(text.strip())
            elif itype == "thinking":
                text = item.get("text", "")
                if text.strip():
                    parts.append(f"<details>\n<summary>思考过程</summary>\n\n{text.strip()}\n\n</details>")
            elif itype == "tool_use":
                name = item.get("name", "")
                inp = item.get("input", {})
                inp_str = json.dumps(inp, ensure_ascii=False, indent=2)[:300]
                parts.append(f"[调用工具: {name}]\n```json\n{inp_str}\n```")
            elif itype == "tool_result":
                text = item.get("text", "")
                content_inner = item.get("content", "")
                if isinstance(content_inner, str) and content_inner.strip():
                    parts.append(f"[工具结果]\n```\n{content_inner.strip()[:500]}\n```")
                elif isinstance(text, str) and text.strip():
                    parts.append(f"[工具结果]\n```\n{text.strip()[:500]}\n```")
        return "\n\n".join(parts)
    return str(content) if content else ""


def is_tool_only_message(content) -> bool:
    """判断消息是否只包含 tool_result（不需要单独展示）"""
    if not isinstance(content, list):
        return False
    return all(
        isinstance(item, dict) and item.get("type") == "tool_result"
        for item in content
    )


def is_tool_use_message(content) -> bool:
    """判断消息是否只包含 tool_use（不需要单独展示）"""
    if not isinstance(content, list):
        return False
    return all(
        isinstance(item, dict) and item.get("type") in ("tool_use", "thinking")
        for item in content
    )


def extract_conversation(jsonl_path: str, output_path: str):
    """从 JSONL 文件提取对话并写入 Markdown"""

    with open(jsonl_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    messages = []
    for line in lines:
        obj = json.loads(line)
        msg_type = obj.get("type", "")

        if msg_type not in ("user", "assistant"):
            continue

        msg = obj.get("message", {})
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", msg_type)
        content = msg.get("content", "")

        # 跳过空消息
        if not content:
            continue

        # 跳过纯 tool_result 消息（会紧跟在 assistant 的 tool_use 后面）
        if role == "user" and is_tool_only_message(content):
            continue

        # 跳过纯 tool_use 消息（内容会在下一条 assistant 消息中体现）
        if role == "assistant" and is_tool_use_message(content):
            continue

        text = extract_text(content)
        if not text.strip():
            continue

        messages.append((role, text))

    # 写入 Markdown
    md_lines = [
        "# JC Agent 开发对话记录\n",
        f"> 从 JSONL 对话记录自动提取生成\n",
        "---\n",
    ]

    for role, text in messages:
        if role == "user":
            md_lines.append(f"## 👤 用户\n\n{text}\n")
        elif role == "assistant":
            md_lines.append(f"## 🤖 助手\n\n{text}\n")
        md_lines.append("---\n")

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    print(f"已提取 {len(messages)} 条消息 -> {output_path}")
    return len(messages)


if __name__ == "__main__":
    jsonl_path = sys.argv[1] if len(sys.argv) > 1 else None
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    if not jsonl_path:
        # 默认路径
        jsonl_path = os.path.join(
            os.path.expanduser("~"),
            ".claude", "projects", "e--work-claude-code-jc-agent",
            "5b36c0ab-6145-4c0b-9751-5bd3cc99c604.jsonl"
        )

    if not output_path:
        output_path = os.path.join(
            "E:\\", "work", "claude code", "jc_agent",
            ".claude", "conversation.md"
        )

    extract_conversation(jsonl_path, output_path)
