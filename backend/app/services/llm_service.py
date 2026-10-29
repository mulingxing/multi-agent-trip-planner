"""LLM服务模块，优先使用本项目的显式配置。"""

import logging
import os
from pathlib import Path

from dotenv import dotenv_values
from hello_agents import HelloAgentsLLM

logger = logging.getLogger(__name__)
_PROJECT_ENV = Path(__file__).resolve().parents[2] / ".env"

# 全局LLM实例
_llm_instance = None


def get_llm() -> HelloAgentsLLM:
    """
    获取LLM实例(单例模式)
    
    Returns:
        HelloAgentsLLM实例
    """
    global _llm_instance
    
    if _llm_instance is None:
        # 不依赖启动目录，也不让其他项目继承的 OPENAI_API_KEY 覆盖本项目。
        project_values = dotenv_values(_PROJECT_ENV, interpolate=False)

        def resolve(*names: str) -> str:
            for values in (project_values, os.environ):
                for name in names:
                    value = values.get(name)
                    if value and value.strip():
                        return value.strip()
            return ""

        model = resolve("LLM_MODEL_ID", "OPENAI_MODEL")
        api_key = resolve("LLM_API_KEY", "OPENAI_API_KEY")
        base_url = resolve("LLM_BASE_URL", "OPENAI_BASE_URL")
        if not model:
            raise ValueError("请在 backend/.env 中配置 LLM_MODEL_ID（或 OPENAI_MODEL）")
        if not api_key:
            raise ValueError("请在 backend/.env 中配置 LLM_API_KEY（或 OPENAI_API_KEY）")
        if not base_url:
            base_url = "https://api.openai.com/v1"

        try:
            timeout = int(resolve("LLM_TIMEOUT") or "60")
        except ValueError:
            raise ValueError("LLM_TIMEOUT 必须是正整数（秒）") from None
        if timeout <= 0:
            raise ValueError("LLM_TIMEOUT 必须是正整数（秒）")

        # HelloAgents 0.2.9 自动检测会优先取 OPENAI_API_KEY。
        # custom + 显式参数确保模型、密钥和服务地址来自同一配置解析规则。
        _llm_instance = HelloAgentsLLM(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            provider="custom",
        )
        logger.info("LLM客户端已配置，模型: %s", model)
    
    return _llm_instance


def reset_llm():
    """重置LLM实例(用于测试或重新配置)"""
    global _llm_instance
    _llm_instance = None

