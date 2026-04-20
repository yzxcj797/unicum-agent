"""配置加载：YAML + .env。"""

import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from my_agent.constants import get_home

logger = logging.getLogger(__name__)

# 提供商配置：优先级从前到后
_PROVIDERS = [
    {
        "name": "zhipu",
        "api_key_env": "ZHIPUAI_API_KEY",
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "default_model": "glm-5.1",
    },
    {
        "name": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
    {
        "name": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-20250514",
    },
    {
        "name": "openrouter",
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-sonnet-4-20250514",
    },
]

_DEFAULT_CONFIG: Dict[str, Any] = {
    "model": None,  # 自动检测
    "max_iterations": 50,
    "tool_delay": 0.5,
    "enabled_toolsets": None,
    "disabled_toolsets": None,
    "fast_model": None,  # 快速模型（如 glm-4-flash），用于工具结果后的决策轮次
}


def load_dotenv_file() -> None:
    """加载 .env 文件。"""
    home = get_home()
    for path in [home / ".env", Path(".env")]:
        if path.exists():
            load_dotenv(path)
            logger.info("Loaded .env from %s", path)
            return


def load_config() -> Dict[str, Any]:
    """加载 config.yaml，合并默认值。"""
    config = dict(_DEFAULT_CONFIG)
    config_path = get_home() / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                user_config = yaml.safe_load(f) or {}
            config.update(user_config)
        except Exception as e:
            logger.warning("Failed to load config.yaml: %s", e)
    return config


def detect_provider() -> Optional[Dict[str, str]]:
    """自动检测可用的 API 提供商，返回配置字典。"""
    for provider in _PROVIDERS:
        key = os.getenv(provider["api_key_env"])
        if key:
            base_url = os.getenv(provider.get("base_url_env", ""), provider["base_url"])
            return {
                "name": provider["name"],
                "api_key": key,
                "base_url": base_url,
                "default_model": provider["default_model"],
            }
    return None


def get_api_key() -> Optional[str]:
    """自动检测可用的 API Key。"""
    p = detect_provider()
    return p["api_key"] if p else None


def get_base_url() -> str:
    """返回 API Base URL。"""
    p = detect_provider()
    return p["base_url"] if p else "https://api.openai.com/v1"


def get_default_model() -> str:
    """返回默认模型名。"""
    p = detect_provider()
    return p["default_model"] if p else "gpt-4o"
