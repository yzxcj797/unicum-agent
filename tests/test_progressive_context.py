"""测试渐进式上下文：L0 汇总、L2 归档、recall 工具。"""

import json
import pytest

# 确保工具模块被导入（注册到 registry）
import tools.recall_tool  # noqa: F401

from core.context_archive import ContextArchive
from core.model_client import ModelClient
from my_agent.constants import TaskPhase
from tools.registry import registry
from core.context_archive import archive as global_archive


class TestContextArchive:
    """测试 L2 归档存储。"""

    def test_store_and_recall_by_id(self):
        arc = ContextArchive()
        arc.store("t1", "read_file", {"path": "a.py"}, '{"success": true}', "[read_file] a.py")
        results = arc.recall("t1")
        assert len(results) == 1
        assert results[0]["tool"] == "read_file"
        assert results[0]["result"] == '{"success": true}'

    def test_recall_by_tool_name(self):
        arc = ContextArchive()
        arc.store("t1", "read_file", {"path": "a.py"}, "r1", "[read_file] a.py")
        arc.store("t2", "terminal", {"command": "ls"}, "r2", "[terminal] ls")
        arc.store("t3", "read_file", {"path": "b.py"}, "r3", "[read_file] b.py")

        results = arc.recall("read_file")
        assert len(results) == 2

    def test_recall_by_keyword(self):
        arc = ContextArchive()
        arc.store("t1", "read_file", {"path": "auth.py"}, "content", "[read_file] auth.py")
        arc.store("t2", "read_file", {"path": "config.py"}, "content", "[read_file] config.py")

        results = arc.recall("auth")
        assert len(results) == 1
        assert "auth.py" in results[0]["fact"]

    def test_recall_no_match(self):
        arc = ContextArchive()
        arc.store("t1", "read_file", {"path": "a.py"}, "r", "[read_file] a.py")
        results = arc.recall("nonexistent")
        assert len(results) == 0

    def test_get_all_facts(self):
        arc = ContextArchive()
        arc.store("t1", "read_file", {"path": "a.py"}, "r1", "[read_file] a.py")
        arc.store("t2", "terminal", {"command": "ls"}, "r2", "[terminal] ls")
        facts = arc.get_all_facts()
        assert len(facts) == 2
        assert "[read_file] a.py" in facts
        assert "[terminal] ls" in facts

    def test_clear(self):
        arc = ContextArchive()
        arc.store("t1", "read_file", {}, "r", "fact")
        arc.clear()
        assert len(arc) == 0
        assert arc.recall("t1") == []

    def test_format_single_result(self):
        arc = ContextArchive()
        arc.store("t1", "read_file", {"path": "a.py"}, "full content here", "[read_file] a.py")
        entries = arc.recall("t1")
        output = arc.format_recall_results(entries)
        parsed = json.loads(output)
        assert parsed["found"] is True
        assert parsed["tool"] == "read_file"
        assert "result_preview" in parsed

    def test_format_multiple_results(self):
        arc = ContextArchive()
        arc.store("t1", "read_file", {"path": "a.py"}, "r1", "[read_file] a.py")
        arc.store("t2", "read_file", {"path": "b.py"}, "r2", "[read_file] b.py")
        entries = arc.recall("read_file")
        output = arc.format_recall_results(entries)
        parsed = json.loads(output)
        assert parsed["count"] == 2
        assert "hint" in parsed


class TestL0Summary:
    """测试 L0 汇总消息的生成和注入。"""

    def _make_client(self):
        client = object.__new__(ModelClient)
        client.model = "test"
        client._provider_name = "test"
        client._client = None
        client._debug = None
        client._system = "system"
        client._messages = []
        client._facts = {}
        client._phase = TaskPhase.PLANNING
        client._l0_inserted = False
        client._COMPRESS_THRESHOLD = 200  # 低阈值，方便触发
        return client

    def test_l0_inserted_after_compression(self):
        """压缩后 L0 汇总消息出现在消息历史中。"""
        client = self._make_client()
        # PLANNING: protect_first=1, protect_last=4 → 需要 >5 条消息
        client._messages = [
            {"role": "user", "content": "任务"},
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 100},
            {"role": "assistant", "content": "step"},
            {"role": "tool", "tool_call_id": "t2", "content": "y" * 100},
            {"role": "assistant", "content": "step2"},
            {"role": "assistant", "content": "done"},
        ]
        client._facts = {
            "t1": "[read_file] a.py (100行)",
            "t2": "[write_file] b.py (写入)",
        }

        client._compress_if_needed()

        # L0 汇总消息存在
        l0_msgs = [m for m in client._messages
                    if isinstance(m.get("content"), str) and m["content"].startswith("[会话进度]")]
        assert len(l0_msgs) == 1
        assert "[read_file] a.py" in l0_msgs[0]["content"]

    def test_l0_updated_on_second_compression(self):
        """多次压缩时 L0 汇总被更新，而非重复插入。"""
        client = self._make_client()

        # 第一次：插入 L0（需要 >5 条消息才能触发压缩）
        client._messages = [
            {"role": "user", "content": "任务"},
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 100},
            {"role": "assistant", "content": "step"},
            {"role": "tool", "tool_call_id": "t2", "content": "y" * 100},
            {"role": "assistant", "content": "step2"},
            {"role": "assistant", "content": "done"},
        ]
        client._facts = {"t1": "[read_file] a.py"}
        client._compress_if_needed()

        l0_count_1 = sum(1 for m in client._messages
                         if isinstance(m.get("content"), str) and m["content"].startswith("[会话进度]"))

        # 第二次：更新 L0（新增 fact），添加更多消息触发压缩
        client._facts["t2"] = "[write_file] b.py"
        client._messages.extend([
            {"role": "tool", "tool_call_id": "t3", "content": "z" * 100},
            {"role": "assistant", "content": "step3"},
        ])

        client._compress_if_needed()

        l0_count_2 = sum(1 for m in client._messages
                         if isinstance(m.get("content"), str) and m["content"].startswith("[会话进度]"))

        # L0 消息数量不变（更新而非新增）
        assert l0_count_2 == l0_count_1

        # L0 包含所有 facts
        l0_msg = next(m for m in client._messages
                      if isinstance(m.get("content"), str) and m["content"].startswith("[会话进度]"))
        assert "[read_file] a.py" in l0_msg["content"]
        assert "[write_file] b.py" in l0_msg["content"]

    def test_l0_not_inserted_without_facts(self):
        """没有 facts 时不插入 L0 消息。"""
        client = self._make_client()
        client._messages = [
            {"role": "user", "content": "任务"},
            {"role": "assistant", "content": "x" * 500},
            {"role": "assistant", "content": "done"},
        ]
        client._compress_if_needed()

        l0_msgs = [m for m in client._messages
                    if isinstance(m.get("content"), str) and m["content"].startswith("[会话进度]")]
        assert len(l0_msgs) == 0


class TestRecallTool:
    """测试 recall 工具。"""

    def setup_method(self):
        global_archive.clear()

    def teardown_method(self):
        global_archive.clear()

    def test_recall_tool_registered(self):
        global_archive.store("t1", "read_file", {"path": "test.py"}, "content", "[read_file] test.py")

        schemas = registry.get_definitions({"recall"})
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "recall"

    def test_recall_dispatch(self):
        global_archive.store("t_test", "read_file", {"path": "auth.py"},
                             "line1\nline2\nline3", "[read_file] auth.py (3行)")

        result = registry.dispatch("recall", {"query": "auth"})
        parsed = json.loads(result)
        assert parsed.get("found") is True
        assert parsed["tool"] == "read_file"
        assert "line1" in parsed["result"]

    def test_recall_no_match(self):
        result = registry.dispatch("recall", {"query": "nonexistent"})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_check_fn_returns_false_when_empty(self):
        schemas = registry.get_definitions({"recall"})
        assert len(schemas) == 0

        global_archive.store("t1", "read_file", {}, "r", "f")
        schemas = registry.get_definitions({"recall"})
        assert len(schemas) == 1
