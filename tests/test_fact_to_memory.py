"""测试自动记忆提取：从 facts 纯规则推导出 memory 条目。"""

import pytest

from core.fact_to_memory import extract_memories


class TestReadFileExtraction:
    """测试 read_file fact → files.* memory。"""

    def test_extracts_python_file(self):
        entries = [(
            "read_file",
            {"path": "src/auth.py"},
            "[read_file] src/auth.py (120行, Python)",
        )]
        result = extract_memories(entries)
        assert len(result) == 1
        scope, cat, key, value = result[0]
        assert key == "files.auth.py"
        assert "Python" in value
        assert "120行" in value

    def test_extracts_json_file(self):
        entries = [(
            "read_file",
            {"path": "package.json"},
            "[read_file] package.json (28行, JSON)",
        )]
        result = extract_memories(entries)
        assert len(result) == 1
        assert result[0][2] == "files.package.json"
        assert "JSON" in result[0][3]

    def test_skips_fail_fact(self):
        entries = [(
            "read_file",
            {"path": "missing.py"},
            "[read_file] FAIL File not found: missing.py",
        )]
        assert extract_memories(entries) == []

    def test_skips_noise_paths(self):
        for path, fact in [
            ("node_modules/lodash/index.js", "[read_file] node_modules/lodash/index.js (500行, JavaScript)"),
            (".git/config", "[read_file] .git/config (10行)"),
            ("__pycache__/main.cpython-310.pyc", "[read_file] __pycache__/main.cpython-310.pyc (5行)"),
            ("dist/bundle.js", "[read_file] dist/bundle.js (200行, JavaScript)"),
        ]:
            entries = [("read_file", {"path": path}, fact)]
            assert extract_memories(entries) == [], f"应跳过噪音路径: {path}"

    def test_skips_small_files(self):
        entries = [(
            "read_file",
            {"path": "README.md"},
            "[read_file] README.md (5行, Markdown)",
        )]
        assert extract_memories(entries) == []

    def test_skips_temp_files(self):
        entries = [(
            "read_file",
            {"path": "output.tmp"},
            "[read_file] output.tmp (50行)",
        )]
        assert extract_memories(entries) == []

    def test_no_language_still_extracts(self):
        """无语言推断时仍能提取。"""
        entries = [(
            "read_file",
            {"path": "Makefile"},
            "[read_file] Makefile (80行)",
        )]
        result = extract_memories(entries)
        assert len(result) == 1
        assert result[0][2] == "files.Makefile"
        assert "80行" in result[0][3]


class TestWriteFileExtraction:
    """测试 write_file fact → files.* memory。"""

    def test_extracts_written_file(self):
        entries = [(
            "write_file",
            {"path": "src/auth.py"},
            "[write_file] src/auth.py (写入 3KB)",
        )]
        result = extract_memories(entries)
        assert len(result) == 1
        scope, cat, key, value = result[0]
        assert key == "files.auth.py"
        assert "Python" in value
        assert "已修改" in value

    def test_extracts_appended_file(self):
        entries = [(
            "write_file",
            {"path": "log.txt"},
            "[write_file] log.txt (追加 500字节)",
        )]
        # log.txt 没有 .log 扩展名检查——这里 .txt 不在 _TEMP_EXTS 中
        result = extract_memories(entries)
        # log.txt 不是噪音路径（.txt 不在 _TEMP_EXTS），所以应该能提取
        # 但实际上这个测试的 fact 不含 FAIL，所以不会被过滤
        # 不过 log.txt 扩展名没有在 _LANG_MAP 中，所以没有语言
        assert len(result) == 1
        assert "已追加" in result[0][3]

    def test_skips_fail(self):
        entries = [(
            "write_file",
            {"path": "output.py"},
            "[write_file] FAIL Permission denied",
        )]
        assert extract_memories(entries) == []

    def test_skips_noise_paths(self):
        entries = [(
            "write_file",
            {"path": "dist/bundle.js"},
            "[write_file] dist/bundle.js (写入 50KB)",
        )]
        assert extract_memories(entries) == []


class TestTerminalExtraction:
    """测试 terminal fact → commands.* memory。"""

    def test_extracts_npm_command(self):
        entries = [(
            "terminal",
            {"command": "npm test"},
            "[terminal] npm test → 退出码0, 47行输出",
        )]
        result = extract_memories(entries)
        assert len(result) == 1
        scope, cat, key, value = result[0]
        assert key == "commands.npm"
        assert "npm" in value
        assert "成功" in value

    def test_extracts_pytest_command(self):
        entries = [(
            "terminal",
            {"command": "pytest tests/"},
            "[terminal] pytest tests/ → 退出码0, 120行输出",
        )]
        result = extract_memories(entries)
        assert len(result) == 1
        assert result[0][2] == "commands.pytest"
        assert "pytest" in result[0][3]

    def test_skips_failed_commands(self):
        entries = [(
            "terminal",
            {"command": "npm build"},
            "[terminal] npm build → 退出码1(失败), 12行输出",
        )]
        assert extract_memories(entries) == []

    def test_skips_short_output(self):
        entries = [(
            "terminal",
            {"command": "npm --version"},
            "[terminal] npm --version → 退出码0, 1行输出",
        )]
        assert extract_memories(entries) == []

    def test_skips_unknown_commands(self):
        entries = [(
            "terminal",
            {"command": "echo hello"},
            "[terminal] echo hello → 退出码0, 5行输出",
        )]
        # echo 不在 _TOOL_SIGNATURES 中
        assert extract_memories(entries) == []

    def test_skips_fail_fact(self):
        entries = [(
            "terminal",
            {"command": "npm test"},
            "[terminal] FAIL Command timed out",
        )]
        assert extract_memories(entries) == []

    def test_extracts_go_command(self):
        entries = [(
            "terminal",
            {"command": "go test ./..."},
            "[terminal] go test ./... → 退出码0, 50行输出",
        )]
        result = extract_memories(entries)
        assert len(result) == 1
        assert result[0][2] == "commands.go"


class TestDeduplication:
    """测试去重逻辑。"""

    def test_same_file_read_twice_keeps_last(self):
        entries = [
            ("read_file", {"path": "auth.py"}, "[read_file] auth.py (100行, Python)"),
            ("read_file", {"path": "auth.py"}, "[read_file] auth.py (120行, Python)"),
        ]
        result = extract_memories(entries)
        assert len(result) == 1
        assert "120行" in result[0][3]

    def test_read_then_write_keeps_write(self):
        entries = [
            ("read_file", {"path": "auth.py"}, "[read_file] auth.py (100行, Python)"),
            ("write_file", {"path": "auth.py"}, "[write_file] auth.py (写入 3KB)"),
        ]
        result = extract_memories(entries)
        assert len(result) == 1
        assert "已修改" in result[0][3]

    def test_different_commands_both_stored(self):
        entries = [
            ("terminal", {"command": "npm test"}, "[terminal] npm test → 退出码0, 47行输出"),
            ("terminal", {"command": "pytest tests/"}, "[terminal] pytest tests/ → 退出码0, 30行输出"),
        ]
        result = extract_memories(entries)
        assert len(result) == 2

    def test_empty_facts_produce_nothing(self):
        assert extract_memories([]) == []

    def test_all_fail_facts_produce_nothing(self):
        entries = [
            ("read_file", {"path": "a.py"}, "[read_file] FAIL not found"),
            ("write_file", {"path": "b.py"}, "[write_file] FAIL permission"),
            ("terminal", {"command": "x"}, "[terminal] FAIL timeout"),
        ]
        assert extract_memories(entries) == []


class TestUnknownToolsSkipped:
    """测试非目标工具类型被跳过。"""

    def test_search_files_skipped(self):
        entries = [(
            "search_files",
            {"pattern": "auth", "path": "src"},
            '[search_files] "auth" in src → 5处匹配',
        )]
        assert extract_memories(entries) == []

    def test_web_search_skipped(self):
        entries = [(
            "web_search",
            {"query": "Python"},
            '[web_search] "Python" (百度, 5条结果)',
        )]
        assert extract_memories(entries) == []

    def test_clarify_skipped(self):
        entries = [(
            "clarify",
            {"question": "使用什么框架?"},
            "[clarify] 用户回答: 使用 FastAPI",
        )]
        assert extract_memories(entries) == []
