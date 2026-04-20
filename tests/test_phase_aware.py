"""测试任务阶段感知的上下文压缩。"""

import json
import pytest

from my_agent.constants import TaskPhase
from core.model_client import ModelClient


class TestTaskPhase:
    """测试 TaskPhase 枚举和基本行为。"""

    def test_phase_values(self):
        assert TaskPhase.PLANNING.value == "planning"
        assert TaskPhase.EXECUTING.value == "executing"
        assert TaskPhase.VERIFYING.value == "verifying"


class TestPhaseConfig:
    """测试各阶段的保护边界配置。"""

    def test_planning_config(self):
        config = ModelClient._PHASE_CONFIG[TaskPhase.PLANNING]
        assert config["protect_first"] == 1
        assert config["protect_last"] == 4

    def test_executing_config(self):
        config = ModelClient._PHASE_CONFIG[TaskPhase.EXECUTING]
        assert config["protect_first"] == 2
        assert config["protect_last"] == 6

    def test_verifying_config(self):
        config = ModelClient._PHASE_CONFIG[TaskPhase.VERIFYING]
        assert config["protect_first"] == 1
        assert config["protect_last"] == 4


class TestFactRetention:
    """测试 VERIFYING 阶段的事实保留逻辑。"""

    def test_verifying_keeps_write_file_fact(self):
        """VERIFYING 阶段保留 write_file 事实。"""
        # 使用 _should_keep_fact 方法直接测试
        from core.model_client import ModelClient
        # 需要模拟一个 ModelClient，但不调用 __init__（避免 API key 检测）
        client = object.__new__(ModelClient)
        client._phase = TaskPhase.VERIFYING

        fact = "[write_file] auth.py (写入 2KB)"
        assert client._should_keep_fact(fact) is True

    def test_verifying_keeps_terminal_fact(self):
        """VERIFYING 阶段保留 terminal 事实。"""
        client = object.__new__(ModelClient)
        client._phase = TaskPhase.VERIFYING

        fact = "[terminal] pytest → 退出码0, 100行输出"
        assert client._should_keep_fact(fact) is True

    def test_verifying_compresses_read_file_fact(self):
        """VERIFYING 阶段压缩 read_file 事实。"""
        client = object.__new__(ModelClient)
        client._phase = TaskPhase.VERIFYING

        fact = "[read_file] auth.py (120行, Python)"
        assert client._should_keep_fact(fact) is False

    def test_verifying_compresses_search_fact(self):
        """VERIFYING 阶段压缩 search_files 事实。"""
        client = object.__new__(ModelClient)
        client._phase = TaskPhase.VERIFYING

        fact = '[search_files] "AuthService" → 5处匹配'
        assert client._should_keep_fact(fact) is False

    def test_planning_does_not_keep_any_fact(self):
        """PLANNING 阶段不保留任何事实（全部压缩）。"""
        client = object.__new__(ModelClient)
        client._phase = TaskPhase.PLANNING

        assert client._should_keep_fact("[write_file] x.py (写入)") is False
        assert client._should_keep_fact("[terminal] test") is False

    def test_executing_does_not_keep_any_fact(self):
        """EXECUTING 阶段不保留任何事实（全部压缩）。"""
        client = object.__new__(ModelClient)
        client._phase = TaskPhase.EXECUTING

        assert client._should_keep_fact("[write_file] x.py (写入)") is False


class TestPhaseCompression:
    """测试阶段感知压缩的实际效果。"""

    def _make_client(self, phase=TaskPhase.PLANNING):
        """创建一个测试用 ModelClient（跳过 API key 检测）。"""
        client = object.__new__(ModelClient)
        client.model = "test"
        client._provider_name = "test"
        client._client = None
        client._debug = None
        client._system = "system prompt"
        client._messages = []
        client._facts = {}
        client._phase = phase
        client._COMPRESS_THRESHOLD = 500  # 测试用低阈值
        return client

    def test_planning_protects_first_1(self):
        """PLANNING 阶段保护第 0 条消息。"""
        client = self._make_client(TaskPhase.PLANNING)

        # 构造 6 条消息
        client._messages = [
            {"role": "user", "content": "任务"},      # index 0: 保护
            {"role": "assistant", "content": "plan"},  # index 1: 可压缩
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 300},
            {"role": "assistant", "content": "step"},
            {"role": "tool", "tool_call_id": "t2", "content": "y" * 300},
            {"role": "assistant", "content": "result"}, # index 5: 保护 (last 4)
        ]

        client._compress_if_needed()

        # 第 0 条未被修改
        assert client._messages[0]["content"] == "任务"
        # 最后 4 条未被修改
        assert client._messages[5]["content"] == "result"

    def test_executing_protects_first_2(self):
        """EXECUTING 阶段保护前 2 条消息（用户任务 + 计划）。"""
        client = self._make_client(TaskPhase.EXECUTING)

        client._messages = [
            {"role": "user", "content": "任务"},      # index 0: 保护
            {"role": "assistant", "content": "plan"},  # index 1: 保护
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 300},
            {"role": "assistant", "content": "step1"},
            {"role": "tool", "tool_call_id": "t2", "content": "y" * 300},
            {"role": "assistant", "content": "step2"},
            {"role": "tool", "tool_call_id": "t3", "content": "z" * 300},
            {"role": "assistant", "content": "last"},  # index 7: 保护 (last 6)
        ]
        client._facts = {"t1": "[read_file] a.py"}

        client._compress_if_needed()

        # 前 2 条未被修改
        assert client._messages[0]["content"] == "任务"
        assert client._messages[1]["content"] == "plan"
        # 最后 6 条中 index 2 是可压缩的
        # (protect_first=2, protect_last=6 → compress range is [2, 8-6=2), empty range)

    def test_verifying_keeps_write_facts(self):
        """VERIFYING 阶段保留 write_file 事实不被压缩。"""
        client = self._make_client(TaskPhase.VERIFYING)

        long_result = "x" * 500
        client._messages = [
            {"role": "user", "content": "任务"},
            {"role": "assistant", "content": "plan"},
            {"role": "tool", "tool_call_id": "t_read1",
             "content": long_result},
            {"role": "assistant", "content": "step"},
            {"role": "tool", "tool_call_id": "t_write",
             "content": long_result},
            {"role": "assistant", "content": "step2"},
            {"role": "tool", "tool_call_id": "t_read2",
             "content": long_result},
            {"role": "assistant", "content": "verify"},
            {"role": "tool", "tool_call_id": "t_term",
             "content": long_result},
            {"role": "assistant", "content": "step3"},
            {"role": "assistant", "content": "done"},
        ]
        client._facts = {
            "t_read1": "[read_file] a.py (100行)",
            "t_write": "[write_file] auth.py (写入 2KB)",
            "t_read2": "[read_file] config.py (50行)",
            "t_term": "[terminal] pytest → 退出码0",
        }

        client._compress_if_needed()

        # 辅助函数：按 tool_call_id 查找消息
        def find_tool(tc_id):
            for m in client._messages:
                if m.get("tool_call_id") == tc_id:
                    return m
            return None

        # write_file 的完整结果被保留（VERIFYING 阶段保留 write_file）
        write_msg = find_tool("t_write")
        assert write_msg is not None
        assert write_msg["content"] == long_result

        # read_file 的结果被归档（压缩为 L1）
        read1_msg = find_tool("t_read1")
        assert read1_msg is not None
        assert "[已归档]" in read1_msg["content"]
        assert "a.py" in read1_msg["content"]

        # L0 汇总消息存在
        l0_found = any(
            isinstance(m.get("content"), str) and m["content"].startswith("[会话进度]")
            for m in client._messages
        )
        assert l0_found
