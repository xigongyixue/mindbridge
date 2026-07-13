import unittest
from types import SimpleNamespace

from app.services.agent_models import AgentModelRegistry


class AgentModelRegistryTests(unittest.TestCase):
    def test_agent_can_override_model_without_changing_global_default(self):
        settings = SimpleNamespace(
            ai_provider="ollama",
            ollama_model="default-model",
            openai_model="default-openai",
            ai_temperature=0.35,
            ai_max_tokens=512,
            agent_model_intent_model="small-intent-model",
            agent_model_intent_provider="ollama",
            agent_model_risk_model="risk-model",
            agent_model_risk_provider="ollama",
        )

        registry = AgentModelRegistry(settings)

        self.assertEqual(registry.profile_for("IntentAgent").model, "small-intent-model")
        self.assertEqual(registry.profile_for("CounselorAgent").model, "default-model")


if __name__ == "__main__":
    unittest.main()
