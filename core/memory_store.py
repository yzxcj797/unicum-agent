"""结构化记忆存储：跨会话持久化的 JSON 记忆。

两层作用域：
  - global.json — 用户档案、全局偏好、环境信息
  - projects/{dir_name}/memory.json — 项目级记忆（文件信息、约定、笔记）

依赖链：
    my_agent/constants.py  (get_home)
           ↑
    core/memory_store.py   (零外部依赖)
           ↑
    tools/memory_tool.py   (工具注册)
    core/agent.py          (加载/注入)
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class MemoryStore:
    """结构化记忆存储，JSON 格式，项目级隔离。"""

    def __init__(self, home_dir: Path):
        self._memory_dir = home_dir / "memory"
        self._global_path = self._memory_dir / "global.json"
        self._global: Dict = self._load(self._global_path)

        self._project_dir_name: str = ""
        self._project_path: Optional[Path] = None
        self._project: Dict = {}

    # ── 项目作用域切换 ────────────────────────────────────────────────

    def set_project(self, working_dir: str):
        """根据工作目录切换项目记忆作用域。"""
        dir_name = Path(working_dir).resolve().name
        if dir_name == self._project_dir_name:
            return
        self._project_dir_name = dir_name
        self._project_path = self._memory_dir / "projects" / dir_name / "memory.json"
        self._project = self._load(self._project_path)
        logger.info("Memory scope: project=%s", dir_name)

    # ── 读取 ──────────────────────────────────────────────────────────

    def get_memory_text(self) -> str:
        """生成用于注入系统提示词的文本。"""
        parts = []

        # 全局用户偏好
        user = self._global.get("user", {})
        if user:
            lines = []
            role = user.get("role")
            if role:
                lines.append(f"角色: {role}")
            prefs = user.get("preferences", [])
            for p in prefs:
                lines.append(f"- {p}")
            if lines:
                parts.append("用户档案:\n" + "\n".join(lines))

        # 全局环境信息
        env = self._global.get("environment", {})
        if env:
            lines = []
            os_name = env.get("os")
            if os_name:
                lines.append(f"系统: {os_name}")
            tools = env.get("tools", [])
            if tools:
                lines.append(f"工具: {', '.join(tools)}")
            if lines:
                parts.append("环境信息:\n" + "\n".join(lines))

        # 项目记忆
        proj = self._project.get("project", {})
        if proj:
            lines = []

            files = proj.get("files", {})
            if files:
                lines.append("已知文件:")
                for path, note in files.items():
                    lines.append(f"  {path}: {note}")

            conventions = proj.get("conventions", [])
            if conventions:
                lines.append("项目约定:")
                for c in conventions:
                    lines.append(f"  - {c}")

            notes = proj.get("notes", [])
            if notes:
                lines.append("笔记:")
                for n in notes:
                    lines.append(f"  - {n}")

            if lines:
                parts.append(f"项目 [{self._project_dir_name}]:\n" + "\n".join(lines))

        return "\n\n".join(parts)

    def list_entries(self, scope: str = "project") -> dict:
        """返回指定作用域的所有记忆。"""
        data = self._global if scope == "global" else self._project
        return dict(data)

    # ── 写入 ──────────────────────────────────────────────────────────

    def add(self, scope: str, category: str, key: str, value: str):
        """添加一条记忆。

        category: "user", "project", "environment"
        key: 类别内的键名（如 "role", "files.auth.py", "preferences"）
        value: 值
        """
        data = self._global if scope == "global" else self._project
        cat = data.setdefault(category, {})

        # 处理嵌套键（如 "files.auth.py" → cat["files"]["auth.py"]）
        parts = key.split(".", 1)
        if len(parts) == 2:
            sub_cat = cat.setdefault(parts[0], {})
            if isinstance(sub_cat, dict):
                sub_cat[parts[1]] = value
            else:
                # 无法在列表上设键，覆盖为 dict
                cat[parts[0]] = {parts[1]: value}
        else:
            # 简单键
            if isinstance(cat, dict):
                if key in cat:
                    existing = cat[key]
                    if isinstance(existing, list):
                        if value not in existing:
                            existing.append(value)
                    else:
                        cat[key] = [existing, value]
                else:
                    cat[key] = value
            elif isinstance(cat, list):
                if value not in cat:
                    cat.append(value)

        self._save_to_disk(scope)
        logger.info("Memory add: %s/%s/%s = %s", scope, category, key, value[:50])

    def remove(self, scope: str, category: str, key: str) -> bool:
        """删除一条记忆。返回是否成功。"""
        data = self._global if scope == "global" else self._project
        cat = data.get(category)
        if not cat:
            return False

        removed = False
        parts = key.split(".", 1)
        if len(parts) == 2:
            sub_cat = cat.get(parts[0]) if isinstance(cat, dict) else None
            if isinstance(sub_cat, dict) and parts[1] in sub_cat:
                del sub_cat[parts[1]]
                removed = True
        else:
            if isinstance(cat, dict) and key in cat:
                del cat[key]
                removed = True
            elif isinstance(cat, list) and key in cat:
                cat.remove(key)
                removed = True

        if removed:
            # 清理空 category
            if not cat and category in data:
                del data[category]
            self._save_to_disk(scope)
            return True

        return False

    # ── 持久化 ────────────────────────────────────────────────────────

    def _save_to_disk(self, scope: str):
        """将指定作用域持久化到磁盘。"""
        if scope == "global":
            self._atomic_write(self._global_path, self._global)
        elif scope == "project" and self._project_path:
            self._atomic_write(self._project_path, self._project)

    def _atomic_write(self, path: Path, data: dict):
        """原子写入 JSON 文件（tempfile + os.replace）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp",
                prefix=".memory_",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp, str(path))
            except Exception:
                # 清理临时文件
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error("Failed to write memory file %s: %s", path, e)

    def _load(self, path: Path) -> dict:
        """从 JSON 文件加载，不存在则返回空 dict。"""
        if not path or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load memory file %s: %s", path, e)
            return {}

    def has_content(self) -> bool:
        """是否有任何记忆内容。"""
        if self._global:
            return True
        return bool(self._project)


# 模块级单例
from my_agent.constants import get_home

memory_store = MemoryStore(get_home())
