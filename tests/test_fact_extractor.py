"""测试结构化事实提取模块。"""

import json
import pytest

from core.fact_extractor import extract_fact


class TestReadFile:
    def test_success(self):
        r = json.dumps({"success": True, "path": "src/auth.py",
                         "total_lines": 120, "shown_lines": 120})
        fact = extract_fact("read_file", {"path": "src/auth.py"}, r)
        assert "read_file" in fact
        assert "auth.py" in fact
        assert "120" in fact
        assert "Python" in fact

    def test_error(self):
        r = json.dumps({"error": "File not found: missing.py"})
        fact = extract_fact("read_file", {"path": "missing.py"}, r)
        assert "FAIL" in fact
        assert "missing.py" in fact


class TestWriteFile:
    def test_success(self):
        r = json.dumps({"success": True, "path": "out.txt",
                         "action": "written", "bytes": 2048})
        fact = extract_fact("write_file", {"path": "out.txt", "content": "..."}, r)
        assert "write_file" in fact
        assert "out.txt" in fact
        assert "2KB" in fact

    def test_append(self):
        r = json.dumps({"success": True, "path": "log.txt",
                         "action": "appended", "bytes": 500})
        fact = extract_fact("write_file", {"path": "log.txt", "content": "..."}, r)
        assert "追加" in fact
        assert "500字节" in fact


class TestTerminal:
    def test_success(self):
        r = json.dumps({"success": True, "output": "ok\nline2",
                         "return_code": 0, "cwd": "/app"})
        fact = extract_fact("terminal", {"command": "npm test"}, r)
        assert "npm test" in fact
        assert "退出码0" in fact
        assert "2行输出" in fact

    def test_failure(self):
        r = json.dumps({"success": False, "output": "Error!",
                         "return_code": 1})
        fact = extract_fact("terminal", {"command": "build"}, r)
        assert "失败" in fact

    def test_long_command(self):
        cmd = "x" * 80
        r = json.dumps({"success": True, "output": "", "return_code": 0})
        fact = extract_fact("terminal", {"command": cmd}, r)
        assert "..." in fact


class TestSearchFiles:
    def test_success(self):
        r = json.dumps({"success": True, "pattern": "AuthService",
                         "total": 3, "matches": []})
        fact = extract_fact("search_files", {"pattern": "AuthService"}, r)
        assert "AuthService" in fact
        assert "3" in fact

    def test_truncated(self):
        r = json.dumps({"success": True, "pattern": "test",
                         "total": 50, "truncated": True, "matches": []})
        fact = extract_fact("search_files", {"pattern": "test"}, r)
        assert "截断" in fact


class TestWebSearch:
    def test_baidu(self):
        r = json.dumps({"success": True, "engine": "baidu",
                         "results": [{"title": "a"}, {"title": "b"}]})
        fact = extract_fact("web_search", {"query": "Python"}, r)
        assert "百度" in fact
        assert "2条" in fact

    def test_tavily(self):
        r = json.dumps({"success": True, "engine": "tavily",
                         "results": [{"title": "a"}]})
        fact = extract_fact("web_search", {"query": "test"}, r)
        assert "Tavily" in fact


class TestWebRead:
    def test_success(self):
        r = json.dumps({"success": True, "url": "https://example.com",
                         "content": "x" * 5000})
        fact = extract_fact("web_read", {"url": "https://example.com"}, r)
        assert "example.com" in fact
        assert "5000字" in fact


class TestGenericFallback:
    def test_unknown_tool(self):
        r = json.dumps({"success": True, "count": 10})
        fact = extract_fact("custom_tool", {"name": "test"}, r)
        assert "custom_tool" in fact
        assert "test" in fact

    def test_invalid_json(self):
        fact = extract_fact("some_tool", {"a": 1}, "not json at all")
        assert "some_tool" in fact
        assert "字结果" in fact
