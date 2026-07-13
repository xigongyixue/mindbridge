from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.services.ai import AiClient


AGENT_MODEL_ALIASES = {
    "CoordinatorAgent": "coordinator",
    "UnderstandingAgent": "understanding",
    "IntentAgent": "intent",
    "SafetyAgent": "safety",
    "RiskGuardianAgent": "risk",
    "ContextAgent": "context",
    "KnowledgeAgent": "knowledge",
    "ResponseAgent": "response",
    "CompanionAgent": "companion",
    "CounselorAgent": "counselor",
    "SafetyCriticAgent": "safety_critic",
}


@dataclass(frozen=True)
class AgentModelProfile:
    """智能体模型配置信息。"""
    provider: str
    model: str
    temperature: float
    max_tokens: int


class AgentModelRegistry:
    """智能体模型配置注册表。"""

    def __init__(self, settings: Settings):
        """初始化注册表。"""
        self.settings = settings

    def profile_for(self, agent_name: str) -> AgentModelProfile:
        """根据智能体名称获取模型配置。"""
        alias = AGENT_MODEL_ALIASES.get(agent_name, _snake(agent_name.removesuffix("Agent")))
        provider = self._setting(f"agent_model_{alias}_provider", self._default_provider())
        model = self._setting(f"agent_model_{alias}_model", self._default_model(provider))
        temperature = float(self._setting(f"agent_model_{alias}_temperature", getattr(self.settings, "ai_temperature", 0.35)))
        max_tokens = int(self._setting(f"agent_model_{alias}_max_tokens", getattr(self.settings, "ai_max_tokens", 512)))
        return AgentModelProfile(provider=provider, model=model, temperature=temperature, max_tokens=max_tokens)

    def client_for(self, agent_name: str) -> AiClient:
        """根据智能体名称创建AI客户端。"""
        profile = self.profile_for(agent_name)
        settings = copy.copy(self.settings)
        settings.ai_provider = profile.provider
        settings.ai_temperature = profile.temperature
        settings.ai_max_tokens = profile.max_tokens
        if profile.provider == "openai":
            settings.openai_model = profile.model
        else:
            settings.ollama_model = profile.model
        return AiClient(settings)

    def _setting(self, name: str, fallback: Any) -> Any:
        """读取配置项，不存在时返回默认值。"""
        value = getattr(self.settings, name, None)
        if value in {None, ""}:
            return fallback
        return value

    def _default_provider(self) -> str:
        """获取默认模型提供方。"""
        return self._setting("agent_model_default_provider", getattr(self.settings, "ai_provider", "mock")).lower()

    def _default_model(self, provider: str) -> str:
        """根据提供方获取默认模型名。"""
        configured = self._setting("agent_model_default_model", "")
        if configured:
            return configured
        if provider == "openai":
            return getattr(self.settings, "openai_model", "gpt-4o-mini")
        if provider == "ollama":
            return getattr(self.settings, "ollama_model", "mindbridge-qwen2.5-7b-ft:latest")
        return "mock"


def _snake(value: str) -> str:
    """将驼峰命名转为下划线命名。"""
    chars = []
    for index, char in enumerate(value):
        if char.isupper() and index > 0:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)
