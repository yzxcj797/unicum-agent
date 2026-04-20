"""测试结构化记忆存储和 memory 工具。"""

import json
import pytest
from pathlib import Path

import tools.memory_tool  # noqa: F401 — 注册工具

from core.memory_store import MemoryStore
from tools.registry import registry


class TestMemoryStore:
    """测试 MemoryStore 核心逻辑。"""

    def _make_store(self, tmp_path: Path) -> MemoryStore:
        return MemoryStore(tmp_path)

    def test_add_and_list_project(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.set_project("/home/user/auth-service")
        store.add("project", "project", "files.auth.py", "JWT认证模块")
        data = store.list_entries("project")
        assert data["project"]["files"]["auth.py"] == "JWT认证模块"

    def test_add_global_preference(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.add("global", "user", "role", "全栈工程师")
        data = store.list_entries("global")
        assert data["user"]["role"] == "全栈工程师"

    def test_add_list_value(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.set_project("/home/user/myapp")
        store.add("project", "project", "conventions", "测试放 tests/ 目录")
        store.add("project", "project", "conventions", "2空格缩进")
        data = store.list_entries("project")
        assert len(data["project"]["conventions"]) == 2

    def test_remove_dict_key(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.add("global", "user", "role", "工程师")
        assert store.remove("global", "user", "role") is True
        assert store.list_entries("global") == {}

    def test_remove_nonexistent(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        assert store.remove("global", "user", "role") is False

    def test_nested_key(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.set_project("/home/user/myapp")
        store.add("project", "project", "files.auth.py", "认证模块")
        store.add("project", "project", "files.config.py", "配置文件")
        data = store.list_entries("project")
        assert data["project"]["files"]["auth.py"] == "认证模块"
        assert data["project"]["files"]["config.py"] == "配置文件"

    def test_remove_nested_key(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.set_project("/home/user/myapp")
        store.add("project", "project", "files.auth.py", "认证")
        store.add("project", "project", "files.config.py", "配置")
        assert store.remove("project", "project", "files.auth.py") is True
        data = store.list_entries("project")
        assert "auth.py" not in data["project"]["files"]
        assert data["project"]["files"]["config.py"] == "配置"

    def test_persistence(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.set_project("/home/user/myapp")
        store.add("global", "user", "role", "工程师")
        store.add("project", "project", "notes", "入口是 main.py")

        # 新实例加载同一目录
        store2 = self._make_store(tmp_path)
        store2.set_project("/home/user/myapp")
        assert store2.list_entries("global")["user"]["role"] == "工程师"
        assert store2.list_entries("project")["project"]["notes"] == "入口是 main.py"

    def test_project_isolation(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.set_project("/home/user/project-a")
        store.add("project", "project", "notes", "项目A笔记")

        store.set_project("/home/user/project-b")
        # 切换项目后，project-b 应为空
        assert store.list_entries("project") == {}

        # 切回 project-a 仍能读到
        store.set_project("/home/user/project-a")
        assert store.list_entries("project")["project"]["notes"] == "项目A笔记"

    def test_get_memory_text_empty(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        assert store.get_memory_text() == ""

    def test_get_memory_text_with_data(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.set_project("/home/user/myapp")
        store.add("global", "user", "role", "全栈工程师")
        store.add("project", "project", "files.auth.py", "认证模块")

        text = store.get_memory_text()
        assert "全栈工程师" in text
        assert "auth.py" in text

    def test_has_content(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        assert store.has_content() is False
        store.add("global", "user", "role", "工程师")
        assert store.has_content() is True


class TestMemoryTool:
    """测试 memory 工具的注册和调用。"""

    def _make_store(self, tmp_path: Path) -> MemoryStore:
        store = MemoryStore(tmp_path)
        # 替换模块级单例（两个模块都要补丁）
        import core.memory_store as core_mod
        import tools.memory_tool as tool_mod
        core_mod.memory_store = store
        tool_mod.memory_store = store
        return store

    def test_add_action(self, tmp_path: Path):
        self._make_store(tmp_path)
        result = registry.dispatch("memory", {
            "action": "add",
            "category": "project",
            "key": "files.auth.py",
            "value": "认证模块",
            "scope": "project",
        })
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_add_missing_value(self, tmp_path: Path):
        self._make_store(tmp_path)
        result = registry.dispatch("memory", {
            "action": "add",
            "category": "project",
            "key": "test",
        })
        parsed = json.loads(result)
        assert "error" in parsed

    def test_remove_action(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.add("global", "user", "role", "工程师")
        result = registry.dispatch("memory", {
            "action": "remove",
            "category": "user",
            "key": "role",
            "scope": "global",
        })
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_remove_nonexistent(self, tmp_path: Path):
        self._make_store(tmp_path)
        result = registry.dispatch("memory", {
            "action": "remove",
            "category": "user",
            "key": "role",
            "scope": "global",
        })
        parsed = json.loads(result)
        assert "error" in parsed

    def test_list_action(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        store.add("global", "user", "role", "工程师")
        result = registry.dispatch("memory", {
            "action": "list",
            "category": "user",
            "key": "",
            "scope": "global",
        })
        parsed = json.loads(result)
        assert parsed["entries"]["user"]["role"] == "工程师"

    def test_unknown_action(self, tmp_path: Path):
        self._make_store(tmp_path)
        result = registry.dispatch("memory", {
            "action": "update",
            "category": "user",
            "key": "test",
        })
        parsed = json.loads(result)
        assert "error" in parsed
