"""测试工具注册中心。"""

import json
import sys
import pytest
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.registry import ToolRegistry, registry, tool_error, tool_result, discover_builtin_tools


class TestToolRegistry:
    def setup_method(self):
        self.reg = ToolRegistry()

    def test_register_and_get_definitions(self):
        self.reg.register(
            name="test_tool",
            toolset="test",
            schema={
                "name": "test_tool",
                "description": "A test tool",
                "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
            },
            handler=lambda args, **kw: json.dumps({"result": args.get("x")}),
        )
        defs = self.reg.get_definitions({"test_tool"})
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "test_tool"

    def test_dispatch(self):
        self.reg.register(
            name="echo",
            toolset="test",
            schema={"name": "echo", "description": "Echo", "parameters": {}},
            handler=lambda args, **kw: json.dumps({"echo": args.get("msg", "")}),
        )
        result = self.reg.dispatch("echo", {"msg": "hello"})
        parsed = json.loads(result)
        assert parsed["echo"] == "hello"

    def test_dispatch_unknown_tool(self):
        result = self.reg.dispatch("nonexistent", {})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_check_fn_filters_unavailable(self):
        self.reg.register(
            name="unavailable_tool",
            toolset="test",
            schema={"name": "unavailable_tool", "description": "Unavailable", "parameters": {}},
            handler=lambda args, **kw: json.dumps({"ok": True}),
            check_fn=lambda: False,
        )
        defs = self.reg.get_definitions({"unavailable_tool"})
        assert len(defs) == 0

    def test_tool_error(self):
        result = tool_error("something failed")
        parsed = json.loads(result)
        assert parsed["error"] == "something failed"

    def test_tool_result(self):
        result = tool_result(success=True, count=42)
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["count"] == 42

    def test_get_all_tool_names(self):
        self.reg.register(
            name="a_tool", toolset="a",
            schema={"name": "a_tool", "description": "", "parameters": {}},
            handler=lambda args, **kw: "{}",
        )
        self.reg.register(
            name="b_tool", toolset="b",
            schema={"name": "b_tool", "description": "", "parameters": {}},
            handler=lambda args, **kw: "{}",
        )
        names = self.reg.get_all_tool_names()
        assert "a_tool" in names
        assert "b_tool" in names

    def test_toolset_availability(self):
        self.reg.register(
            name="good", toolset="good_set",
            schema={"name": "good", "description": "", "parameters": {}},
            handler=lambda args, **kw: "{}",
            check_fn=lambda: True,
        )
        self.reg.register(
            name="bad", toolset="bad_set",
            schema={"name": "bad", "description": "", "parameters": {}},
            handler=lambda args, **kw: "{}",
            check_fn=lambda: False,
        )
        assert self.reg.is_toolset_available("good_set") is True
        assert self.reg.is_toolset_available("bad_set") is False
