"""统一的 LLM 客户端封装。

智谱使用 Anthropic 原生格式，其他提供商使用 OpenAI 兼容模式。
消息直接以原生格式存储，无需转换层。
"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from my_agent.config import detect_provider, get_default_model
from my_agent.constants import TaskPhase

logger = logging.getLogger(__name__)


class ChatResponse:
    """统一的聊天响应。"""
    __slots__ = ("content", "tool_calls", "usage", "model")

    def __init__(self, content: str = None, tool_calls: list = None,
                 usage: dict = None, model: str = ""):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage
        self.model = model


class ToolCall:
    """统一的工具调用。"""
    __slots__ = ("id", "name", "arguments")

    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.name = name
        self.arguments = arguments

    def parse_args(self) -> dict:
        try:
            return json.loads(self.arguments)
        except json.JSONDecodeError:
            return {}


def _create_client(provider_info: dict):
    """根据提供商信息创建对应的 SDK 客户端。"""
    name = provider_info["name"]
    api_key = provider_info["api_key"]

    if name == "zhipu":
        from anthropic import Anthropic
        return Anthropic(
            api_key=api_key,
            base_url=provider_info["base_url"],
            max_retries=2,
            timeout=300.0,
        )

    from openai import OpenAI
    return OpenAI(
        api_key=api_key,
        base_url=provider_info["base_url"],
        max_retries=2,
        timeout=120.0,
    )


class ModelClient:
    """LLM 客户端，以原生格式管理消息历史。"""

    def __init__(self, model: str = None, debug_callback: Callable = None):
        provider = detect_provider()
        if not provider:
            raise ValueError(
                "No API key found. Set ZHIPUAI_API_KEY / OPENAI_API_KEY in .env"
            )

        self.model = model or get_default_model()
        self._provider_name = provider["name"]
        self._client = _create_client(provider)
        self._debug = debug_callback
        self._system = None
        self._messages: List[Dict[str, Any]] = []
        self._facts: Dict[str, str] = {}  # tool_call_id → 结构化事实
        self._phase = TaskPhase.PLANNING
        self._l0_inserted = False  # L0 汇总消息是否已插入
        logger.info(
            "ModelClient initialized: provider=%s, model=%s",
            self._provider_name, self.model,
        )

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def native_messages(self) -> List[Dict[str, Any]]:
        return list(self._messages)

    def _debug_log(self, label: str, data: Any):
        if self._debug:
            self._debug(label, data)

    # ── 消息管理（原生格式）─────────────────────────────────────────

    def set_system(self, text: str):
        self._system = text

    def add_user(self, content: str):
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str, tool_calls: list):
        """添加 assistant 响应。tool_calls 为 ToolCall 列表。"""
        if self._provider_name == "zhipu":
            content_blocks = []
            if content:
                content_blocks.append({"type": "text", "text": content})
            for tc in tool_calls:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.parse_args(),
                })
            if not content_blocks:
                content_blocks.append({"type": "text", "text": ""})
            self._messages.append({"role": "assistant", "content": content_blocks})
        else:
            msg = {"role": "assistant"}
            if content:
                msg["content"] = content
            if tool_calls:
                msg["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in tool_calls
                ]
            self._messages.append(msg)

    def add_tool_result(self, tool_call_id: str, content: str, is_error: bool = False):
        """添加工具执行结果。"""
        if self._provider_name == "zhipu":
            block = {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": content,
            }
            if is_error:
                block["is_error"] = True
            # Anthropic 要求连续 tool_result 合并到同一个 user 消息
            if (self._messages
                    and self._messages[-1]["role"] == "user"
                    and isinstance(self._messages[-1]["content"], list)
                    and self._messages[-1]["content"]
                    and self._messages[-1]["content"][0].get("type") == "tool_result"):
                self._messages[-1]["content"].append(block)
            else:
                self._messages.append({"role": "user", "content": [block]})
        else:
            self._messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            })

    def record_fact(self, tool_call_id: str, fact: str):
        """记录一条工具调用的结构化事实，用于压缩时替换完整结果。"""
        self._facts[tool_call_id] = fact

    def set_phase(self, phase: TaskPhase):
        """设置当前任务阶段，影响压缩策略。"""
        self._phase = phase

    def reset(self):
        self._messages = []
        self._facts = {}
        self._phase = TaskPhase.PLANNING
        self._l0_inserted = False

    # ── 上下文压缩 ──────────────────────────────────────────────────

    _COMPRESS_THRESHOLD = 40000  # 字符数阈值，超过则压缩（≈10K tokens）
    _L0_MARKER = "[会话进度]"

    # 阶段感知保护边界
    _PHASE_CONFIG = {
        TaskPhase.PLANNING:  {"protect_first": 1, "protect_last": 4},
        TaskPhase.EXECUTING: {"protect_first": 2, "protect_last": 6},
        TaskPhase.VERIFYING: {"protect_first": 1, "protect_last": 4},
    }

    # VERIFYING 阶段保留完整事实的工具前缀
    _VERIFYING_KEEP_PREFIXES = ("[write_file]", "[terminal]")

    def _compress_if_needed(self):
        """如果上下文过长，根据任务阶段采用不同保护策略压缩旧消息。"""
        config = self._PHASE_CONFIG.get(self._phase, self._PHASE_CONFIG[TaskPhase.PLANNING])
        protect_first = config["protect_first"]
        protect_last = config["protect_last"]

        if len(self._messages) <= protect_first + protect_last:
            return
        total_chars = sum(
            len(str(m.get("content", ""))) if isinstance(m.get("content"), str)
            else len(str(m.get("content", [])))
            for m in self._messages
        )
        if total_chars < self._COMPRESS_THRESHOLD:
            return

        logger.info(
            "Compressing context: %d chars across %d messages (phase=%s, protect=%d/%d)",
            total_chars, len(self._messages), self._phase.value,
            protect_first, protect_last,
        )
        start = protect_first
        end = len(self._messages) - protect_last
        for i in range(start, max(start, end)):
            self._compress_message(self._messages[i])

        # 更新 L0 汇总
        self._update_l0_message()

    def _compress_message(self, msg: dict):
        """用结构化事实替换完整工具结果，根据阶段决定保留策略。"""
        content = msg.get("content")
        if isinstance(content, list):
            # Anthropic 格式：content block 数组
            for block in content:
                if block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                    tool_use_id = block.get("tool_use_id", "")
                    text = block["content"]
                    if tool_use_id in self._facts:
                        fact = self._facts[tool_use_id]
                        if self._should_keep_fact(fact):
                            continue  # 保留完整事实，不压缩
                        block["content"] = f"[已归档] {fact}"
                    elif len(text) > 200:
                        block["content"] = text[:100] + f"\n...[已截断: 原{len(text)}字]"
                elif block.get("type") == "tool_use" and "input" in block:
                    inp = block["input"]
                    if isinstance(inp, dict):
                        path = inp.get("path", inp.get("command", ""))
                        char_count = sum(len(str(v)) for v in inp.values())
                        if char_count > 200:
                            block["input"] = {"path": path, "_compressed": f"原{char_count}字"}
        elif isinstance(content, str) and len(content) > 2000:
            msg["content"] = content[:500] + f"\n...[已压缩: 原{len(content)}字]"

        # OpenAI 格式：tool role 消息
        if msg.get("role") == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            text = msg.get("content", "")
            if tool_call_id in self._facts:
                fact = self._facts[tool_call_id]
                if self._should_keep_fact(fact):
                    return  # 保留完整事实，不压缩
                msg["content"] = f"[已归档] {fact}"
            elif isinstance(text, str) and len(text) > 200:
                msg["content"] = text[:100] + f"\n...[已截断: 原{len(text)}字]"

    def _should_keep_fact(self, fact: str) -> bool:
        """VERIFYING 阶段保留修改记录和测试结果的事实。"""
        if self._phase != TaskPhase.VERIFYING:
            return False
        return any(fact.startswith(p) for p in self._VERIFYING_KEEP_PREFIXES)

    # ── L0 汇总管理 ─────────────────────────────────────────────────

    def _build_l0_summary(self) -> str:
        """从 _facts 生成 L0 汇总文本。"""
        if not self._facts:
            return ""
        lines = [self._L0_MARKER]
        for fact in self._facts.values():
            lines.append(f"- {fact}")
        return "\n".join(lines)

    def _update_l0_message(self):
        """在消息历史中插入或更新 L0 汇总消息。"""
        l0_text = self._build_l0_summary()
        if not l0_text:
            return
        # 查找现有 L0 消息并更新
        for msg in self._messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content.startswith(self._L0_MARKER):
                msg["content"] = l0_text
                return
        # 未找到 → 在第 0 条（用户任务）之后插入
        self._messages.insert(1, {"role": "user", "content": l0_text})

    # ── API 调用 ────────────────────────────────────────────────────

    def chat(
        self,
        tools: List[dict] = None,
        max_tokens: int = 16384,
        on_text: Callable[[str], None] = None,
        model: str = None,
        **kwargs,
    ) -> ChatResponse:
        """发送聊天请求。model 可覆盖默认模型。"""
        self._compress_if_needed()
        effective_model = model or self.model
        if on_text and self._provider_name == "zhipu":
            return self._chat_anthropic_stream(tools, max_tokens, on_text, effective_model, **kwargs)
        if self._provider_name == "zhipu":
            return self._chat_anthropic(tools, max_tokens, effective_model, **kwargs)
        return self._chat_openai(tools, max_tokens, effective_model, **kwargs)

    def _chat_anthropic(self, tools, max_tokens, model, **kwargs):
        params = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": self._messages,
        }
        if self._system:
            params["system"] = [
                {"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}
            ]
        if tools:
            tool_list = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get(
                        "parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
            tool_list[-1]["cache_control"] = {"type": "ephemeral"}
            params["tools"] = tool_list
        params.update(kwargs)

        self._debug_log("REQUEST >>>", params)
        try:
            response = self._client.messages.create(**params)
        except Exception as e:
            # 记录 API 错误详情
            status = getattr(e, 'status_code', None)
            body = getattr(e, 'response', None)
            if body is not None:
                body = getattr(body, 'text', str(body))
            logger.error(
                "Anthropic API error: status=%s, type=%s, message=%s, body=%s",
                status, type(e).__name__, e, body[:500] if body else None
            )
            raise
        self._debug_log("RESPONSE <<<", _safe_response_dump(response))

        # 检查 stop_reason
        stop_reason = getattr(response, 'stop_reason', None)
        if stop_reason == "max_tokens":
            logger.warning(
                "Output truncated (stop_reason=max_tokens). "
                "Consider increasing max_tokens (current: %d)", max_tokens
            )

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                input_data = block.input or {}
                if not input_data:
                    logger.warning(
                        "Empty tool_use input: name=%s, id=%s, stop_reason=%s",
                        block.name, block.id, stop_reason
                    )
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=json.dumps(input_data, ensure_ascii=False),
                ))

        content = "\n".join(text_parts) if text_parts else None
        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                "cache_read": getattr(response.usage, 'cache_read_input_tokens', 0),
                "cache_creation": getattr(response.usage, 'cache_creation_input_tokens', 0),
            }

        return ChatResponse(content=content, tool_calls=tool_calls,
                            usage=usage, model=response.model)

    def _chat_anthropic_stream(self, tools, max_tokens, on_text, model, **kwargs):
        """Anthropic 流式调用，实时回调文本块。"""
        params = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": self._messages,
        }
        if self._system:
            params["system"] = [
                {"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}
            ]
        if tools:
            tool_list = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get(
                        "parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
            tool_list[-1]["cache_control"] = {"type": "ephemeral"}
            params["tools"] = tool_list
        params.update(kwargs)

        self._debug_log("REQUEST (stream) >>>", params)

        text_parts = []
        tool_calls = []
        current_tool = None
        current_tool_input = ""

        try:
            with self._client.messages.stream(**params) as stream:
                for event in stream:
                    if event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            current_tool = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                            }
                            current_tool_input = ""
                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            text_parts.append(event.delta.text)
                            on_text(event.delta.text)
                        elif event.delta.type == "input_json_delta":
                            current_tool_input += event.delta.partial_json
                    elif event.type == "content_block_stop":
                        if current_tool:
                            try:
                                input_data = json.loads(current_tool_input) if current_tool_input else {}
                            except json.JSONDecodeError:
                                input_data = {}
                            tool_calls.append(ToolCall(
                                id=current_tool["id"],
                                name=current_tool["name"],
                                arguments=json.dumps(input_data, ensure_ascii=False),
                            ))
                            current_tool = None

                response = stream.get_final_message()
        except Exception as e:
            status = getattr(e, 'status_code', None)
            logger.error("Anthropic stream error: status=%s, %s: %s", status, type(e).__name__, e)
            raise

        self._debug_log("RESPONSE (stream) <<<", _safe_response_dump(response))

        stop_reason = getattr(response, 'stop_reason', None)
        if stop_reason == "max_tokens":
            logger.warning("Stream output truncated (stop_reason=max_tokens)")

        content = "".join(text_parts) or None
        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                "cache_read": getattr(response.usage, 'cache_read_input_tokens', 0),
                "cache_creation": getattr(response.usage, 'cache_creation_input_tokens', 0),
            }

        return ChatResponse(content=content, tool_calls=tool_calls,
                            usage=usage, model=response.model)

    def _chat_openai(self, tools, max_tokens, model, **kwargs):
        params = {
            "model": model,
            "messages": self._messages,
            "max_tokens": max_tokens,
        }
        if tools:
            params["tools"] = tools
        params.update(kwargs)

        self._debug_log("REQUEST >>>", params)
        response = self._client.chat.completions.create(**params)
        self._debug_log("RESPONSE <<<", _safe_response_dump(response))

        choice = response.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments or "{}",
                ))

        content = msg.content
        if content is not None and not isinstance(content, str):
            content = str(content)

        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return ChatResponse(content=content, tool_calls=tool_calls,
                            usage=usage, model=response.model)

    @staticmethod
    def estimate_tokens(text) -> int:
        if isinstance(text, int):
            return text // 4
        return len(text) // 4


def _safe_response_dump(response) -> Any:
    try:
        if hasattr(response, "model_dump"):
            return response.model_dump()
        if hasattr(response, "__dict__"):
            return str(response)
        return response
    except Exception:
        return str(response)
