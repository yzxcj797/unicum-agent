"""AI Agent 核心循环。

实现完整的工具调用循环：
  用户消息 → LLM → 工具调用 → LLM → ... → 最终文本回复

依赖链:
    tools/registry.py  (零依赖)
           ↑
    tools/*.py         (仅 import registry)
           ↑
    core/agent.py      (import tools + core/*)
"""

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

from tools.registry import registry, discover_builtin_tools
from core.model_client import ModelClient, ChatResponse, ToolCall
from core.prompt_builder import build_system_prompt
from core.fact_extractor import extract_fact
from core.context_archive import archive
from core.memory_store import memory_store
from my_agent.constants import TaskPhase

logger = logging.getLogger(__name__)


class IterationBudget:
    """线程安全的迭代计数器。"""

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)

    @property
    def used(self) -> int:
        return self._used


class AIAgent:
    """AI Agent：管理对话循环、工具调用和响应处理。"""

    def __init__(
        self,
        model: str = None,
        max_iterations: int = 150,
        tool_delay: float = 0.01,
        enabled_toolsets: List[str] = None,
        disabled_toolsets: List[str] = None,
        system_prompt: str = None,
        platform: str = None,
        working_dir: str = None,
        clarify_callback: Callable = None,
        tool_start_callback: Callable = None,
        tool_complete_callback: Callable = None,
        verbose: bool = False,
        debug_callback: Callable = None,
        stream_callback: Callable = None,
        fast_model: str = None,
        iteration_callback: Callable = None,
    ):
        self.model = model
        self.max_iterations = max_iterations
        self.fast_model = fast_model
        self.iteration_callback = iteration_callback
        self.tool_delay = tool_delay
        self.system_prompt = system_prompt
        self.platform = platform
        self.working_dir = working_dir
        self.clarify_callback = clarify_callback
        self.tool_start_callback = tool_start_callback
        self.tool_complete_callback = tool_complete_callback
        self.verbose = verbose
        self.stream_callback = stream_callback

        # 工作目录：用户指定 > 当前目录
        if working_dir:
            os.chdir(working_dir)
        self.working_dir = os.getcwd()

        # 加载项目记忆
        memory_store.set_project(self.working_dir)

        # 迭代预算
        self.iteration_budget = IterationBudget(max_iterations)

        # 初始化 LLM 客户端
        self._client = ModelClient(model=model, debug_callback=debug_callback)

        # 发现并注册工具
        self._imported_modules = discover_builtin_tools()
        logger.info("Discovered tool modules: %s", self._imported_modules)

        # 确定启用的工具集
        self._enabled_toolsets = set(enabled_toolsets) if enabled_toolsets else None
        self._disabled_toolsets = set(disabled_toolsets) if disabled_toolsets else set()

        # 会话统计（用于 get_usage_summary）
        self._tool_call_count = 0
        self._initialized = False

    def _ensure_initialized(self):
        """延迟初始化：构建系统提示词和工具 schema。"""
        if self._initialized:
            return
        self._initialized = True

        if not self.system_prompt:
            self.system_prompt = build_system_prompt(
                platform=self.platform,
                working_dir=self.working_dir,
                memory_text=memory_store.get_memory_text(),
            )
        else:
            # 自定义 prompt 也注入记忆
            memory_text = memory_store.get_memory_text()
            if memory_text and "跨会话记忆" not in self.system_prompt:
                self.system_prompt += f"\n\n## 跨会话记忆\n\n{memory_text}"
        self._client.set_system(self.system_prompt)

    def _get_tool_schemas(self) -> List[dict]:
        """获取当前启用的工具 schema。"""
        all_tools = set(registry.get_all_tool_names())

        if self._enabled_toolsets:
            enabled = set()
            for name in all_tools:
                ts = registry.get_toolset_for_tool(name)
                if ts in self._enabled_toolsets:
                    enabled.add(name)
            all_tools = enabled

        if self._disabled_toolsets:
            all_tools = {
                name for name in all_tools
                if registry.get_toolset_for_tool(name) not in self._disabled_toolsets
            }

        return registry.get_definitions(all_tools)

    def chat(self, message: str) -> str:
        """简单接口：发送消息，返回最终文本回复。"""
        result = self.run_conversation(message)
        return result.get("response", "")

    def run_conversation(
        self,
        user_message: str,
        extra_system: str = None,
    ) -> Dict[str, Any]:
        """完整接口：运行 Agent 循环，返回详细结果。"""
        self._ensure_initialized()

        # 新对话重置迭代预算和 facts，允许连续对话
        self.iteration_budget = IterationBudget(self.max_iterations)
        self._client._facts = {}

        # 添加用户消息（直接存入 client 的原生格式）
        self._client.add_user(user_message)

        tool_schemas = self._get_tool_schemas()
        if self.verbose:
            logger.info("Tools available: %s", [t["function"]["name"] for t in tool_schemas])

        total_tool_calls = 0
        final_response = None
        _phase = TaskPhase.PLANNING

        while self.iteration_budget.remaining > 0:
            if not self.iteration_budget.consume():
                final_response = "[Agent: 达到最大迭代次数，停止执行]"
                break

            try:
                use_model = self.fast_model if _phase == TaskPhase.VERIFYING else None
                if self.iteration_callback:
                    display_model = use_model or self._client.model
                    self.iteration_callback(self.iteration_budget.used, display_model)
                use_stream = self.stream_callback is not None
                response = self._client.chat(
                    tools=tool_schemas if tool_schemas else None,
                    on_text=self.stream_callback if use_stream else None,
                    model=use_model,
                )

                # 拦截：快模型阶段如果返回 write_file/edit_file，用主模型重做
                if use_model and response.tool_calls:
                    if any(tc.name in ("write_file", "edit_file") for tc in response.tool_calls):
                        logger.info("Fast model requested write, retrying with main model")
                        if self.iteration_callback:
                            self.iteration_callback(self.iteration_budget.used, self._client.model)
                        response = self._client.chat(
                            tools=tool_schemas if tool_schemas else None,
                            on_text=self.stream_callback if use_stream else None,
                        )
            except Exception as e:
                logger.error("LLM API error: %s", e)
                final_response = f"[API 错误: {type(e).__name__}: {e}]"
                break

            # 记录 assistant 响应（直接存入 client 的原生格式）
            self._client.add_assistant(response.content, response.tool_calls)

            # 无工具调用 → 返回最终文本
            if not response.tool_calls:
                final_response = response.content or ""
                break

            # 执行工具调用
            for tc in response.tool_calls:
                args = tc.parse_args()
                total_tool_calls += 1
                self._tool_call_count += 1

                if self.tool_start_callback:
                    self.tool_start_callback(tc.name, args)

                if self.verbose:
                    logger.info("Tool call: %s(%s)", tc.name, json.dumps(args, ensure_ascii=False)[:200])

                # 执行工具
                result = registry.dispatch(tc.name, args, clarify_callback=self.clarify_callback)

                # 提取结构化事实，用于后续压缩
                fact = extract_fact(tc.name, args, result)
                self._client.record_fact(tc.id, fact)

                # 存入 L2 归档（完整输出，可通过 recall 工具检索）
                archive.store(tc.id, tc.name, args, result, fact)

                if self.tool_complete_callback:
                    self.tool_complete_callback(tc.name, args, result)

                # 检测是否为错误结果
                is_error = False
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict) and "error" in parsed:
                        is_error = True
                except (json.JSONDecodeError, TypeError):
                    pass

                # 添加工具结果（直接存入 client 的原生格式）
                self._client.add_tool_result(tc.id, result, is_error=is_error)

            # 更新任务阶段
            _has_write = any(tc.name in ("write_file", "edit_file") for tc in response.tool_calls)
            if _has_write:
                _phase = TaskPhase.EXECUTING
            elif _phase == TaskPhase.EXECUTING:
                _phase = TaskPhase.VERIFYING
            # 传递阶段给 ModelClient（影响压缩策略）
            self._client.set_phase(_phase)

        if final_response is None:
            final_response = "[Agent: 无响应]"

        # 自动记忆提取（零 LLM 成本）
        if self._client._facts:
            self._auto_extract_memories()

        return {
            "response": final_response,
            "tool_calls": total_tool_calls,
            "iterations": self.iteration_budget.used,
            "messages": self._client.native_messages,
        }

    def _auto_extract_memories(self):
        """从本会话的 facts 自动提取记忆，零 LLM 成本。"""
        from core.fact_to_memory import extract_memories
        entries = [
            (e["tool"], e["args"], fact)
            for tc_id, fact in self._client._facts.items()
            if (e := archive._entries.get(tc_id))
        ]
        for scope, category, key, value in extract_memories(entries):
            memory_store.add(scope, category, key, value)

    def reset(self):
        """重置会话（刷新记忆到系统提示词）。"""
        self._client.reset()
        if self._initialized:
            # 重建系统提示词，包含最新的记忆
            self.system_prompt = build_system_prompt(
                platform=self.platform,
                working_dir=self.working_dir,
                memory_text=memory_store.get_memory_text(),
            )
            self._client.set_system(self.system_prompt)
        self.iteration_budget = IterationBudget(self.max_iterations)
        self._tool_call_count = 0
        archive.clear()

    @property
    def messages(self) -> List[Dict[str, Any]]:
        return self._client.native_messages

    def get_usage_summary(self) -> str:
        """返回当前会话使用摘要。"""
        native = self._client.native_messages
        msg_count = len(native)
        total_chars = sum(
            len(str(m.get("content", "")))
            for m in native if isinstance(m, dict)
        )
        return (
            f"消息数: {msg_count} | "
            f"工具调用: {self._tool_call_count} | "
            f"迭代: {self.iteration_budget.used}/{self.max_iterations} | "
            f"上下文约: {ModelClient.estimate_tokens(total_chars)} tokens"
        )
