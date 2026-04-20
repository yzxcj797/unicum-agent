"""测试 Agent 核心逻辑。"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import AIAgent, IterationBudget
from core.model_client import ChatResponse, ToolCall
from core.memory_store import MemoryStore
from core.prompt_builder import build_system_prompt


class TestIterationBudget:
    def test_consume_within_limit(self):
        budget = IterationBudget(5)
        assert budget.consume() is True
        assert budget.remaining == 4
        assert budget.used == 1

    def test_consume_exceeds_limit(self):
        budget = IterationBudget(2)
        assert budget.consume() is True
        assert budget.consume() is True
        assert budget.consume() is False
        assert budget.remaining == 0

    def test_remaining_never_negative(self):
        budget = IterationBudget(1)
        budget.consume()
        budget.consume()
        assert budget.remaining == 0


class TestAIAgentInit:
    @patch("core.agent.ModelClient")
    @patch("core.agent.discover_builtin_tools", return_value=[])
    def test_agent_creates(self, mock_discover, mock_client):
        agent = AIAgent(model="test-model")
        assert agent.model == "test-model"
        assert agent.iteration_budget.max_total == 50

    @patch("core.agent.ModelClient")
    @patch("core.agent.discover_builtin_tools", return_value=[])
    def test_agent_custom_iterations(self, mock_discover, mock_client):
        agent = AIAgent(max_iterations=100)
        assert agent.iteration_budget.max_total == 100


class TestAIAgentConversation:
    @patch("core.agent.ModelClient")
    @patch("core.agent.discover_builtin_tools", return_value=[])
    def test_simple_response(self, mock_discover, MockClient):
        # Mock LLM 客户端返回纯文本响应
        mock_client = MockClient.return_value
        mock_client.chat.return_value = ChatResponse(
            content="Hello! How can I help you?",
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        )

        agent = AIAgent(model="test")
        result = agent.run_conversation("Hi!")

        assert result["response"] == "Hello! How can I help you?"
        assert result["tool_calls"] == 0

    @patch("core.agent.ModelClient")
    @patch("core.agent.discover_builtin_tools", return_value=[])
    @patch("core.agent.registry")
    def test_tool_call_then_response(self, mock_registry, mock_discover, MockClient):
        mock_client = MockClient.return_value

        # 第一次调用：返回工具调用
        # 第二次调用：返回文本响应
        tool_call = ToolCall(id="tc_1", name="read_file", arguments='{"path": "/tmp/test.txt"}')
        mock_client.chat.side_effect = [
            ChatResponse(
                content=None,
                tool_calls=[tool_call],
            ),
            ChatResponse(
                content="The file contains: hello world",
                tool_calls=[],
            ),
        ]

        mock_registry.get_all_tool_names.return_value = ["read_file"]
        mock_registry.get_toolset_for_tool.return_value = "file"
        mock_registry.get_definitions.return_value = [
            {"type": "function", "function": {"name": "read_file", "description": "", "parameters": {}}}
        ]
        mock_registry.dispatch.return_value = json.dumps({"success": True, "content": "hello world"})

        agent = AIAgent(model="test", tool_delay=0)
        result = agent.run_conversation("Read /tmp/test.txt")

        assert "hello world" in result["response"].lower() or result["tool_calls"] == 1


class TestMemoryInjection:
    """测试记忆注入到系统提示词。"""

    def test_build_system_prompt_includes_memory(self):
        prompt = build_system_prompt(
            memory_text="用户档案:\n角色: 全栈工程师",
        )
        assert "跨会话记忆" in prompt
        assert "全栈工程师" in prompt
        assert "主动利用" in prompt

    def test_build_system_prompt_without_memory(self):
        prompt = build_system_prompt()
        assert "跨会话记忆" not in prompt

    def test_build_system_prompt_with_usage_instruction(self):
        prompt = build_system_prompt(
            memory_text="项目 [myapp]:\n  已知文件:\n    auth.py: Python, 120行",
        )
        assert "memory 工具更新或删除" in prompt

    @patch("core.agent.ModelClient")
    @patch("core.agent.discover_builtin_tools", return_value=[])
    @patch("core.agent.memory_store")
    def test_reset_refreshes_memory(self, mock_ms, mock_discover, MockClient):
        """reset() 后系统提示词包含最新记忆。"""
        mock_client = MockClient.return_value
        mock_ms.get_memory_text.return_value = "项目 [myapp]:\n  auth.py: Python"

        agent = AIAgent(model="test")
        # 触发初始化
        agent._ensure_initialized()
        assert "auth.py" in agent.system_prompt

        # 记忆更新后 reset
        mock_ms.get_memory_text.return_value = "项目 [myapp]:\n  config.py: YAML"
        agent.reset()

        assert "config.py" in agent.system_prompt
        assert "跨会话记忆" in agent.system_prompt

    @patch("core.agent.ModelClient")
    @patch("core.agent.discover_builtin_tools", return_value=[])
    @patch("core.agent.memory_store")
    def test_custom_prompt_gets_memory(self, mock_ms, mock_discover, MockClient):
        """自定义 system_prompt 也注入记忆。"""
        mock_ms.get_memory_text.return_value = "用户档案:\n角色: 工程师"

        agent = AIAgent(model="test", system_prompt="你是一个助手。")
        agent._ensure_initialized()

        assert "跨会话记忆" in agent.system_prompt
        assert "工程师" in agent.system_prompt
        assert agent.system_prompt.startswith("你是一个助手。")
