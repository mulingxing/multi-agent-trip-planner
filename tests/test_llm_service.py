"""LLM配置回归测试：不读取真实.env，不连接模型服务。"""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend" / "app" / "services" / "llm_service.py"
)


class LLMServiceTests(unittest.TestCase):
    def setUp(self):
        # 按文件加载模块，避免app.config在导入时加载用户的实际.env。
        fake_hello_agents = types.ModuleType("hello_agents")
        fake_hello_agents.HelloAgentsLLM = Mock()
        fake_dotenv = types.ModuleType("dotenv")
        fake_dotenv.dotenv_values = Mock(return_value={})
        spec = importlib.util.spec_from_file_location("isolated_llm_service", SERVICE_PATH)
        self.service = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "hello_agents": fake_hello_agents,
            "dotenv": fake_dotenv,
        }):
            spec.loader.exec_module(self.service)
        self.client_factory = fake_hello_agents.HelloAgentsLLM
        self.dotenv_values = fake_dotenv.dotenv_values
        self.env_patch = patch.dict(self.service.os.environ, {}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_project_llm_settings_override_inherited_provider_credentials(self):
        self.dotenv_values.return_value = {
            "LLM_MODEL_ID": "project-model",
            "LLM_API_KEY": "test-project-key",
            "LLM_BASE_URL": "https://project.example/v1",
            "LLM_TIMEOUT": "120",
        }
        self.service.os.environ.update({
            "OPENAI_API_KEY": "test-unrelated-key",
            "OPENAI_MODEL": "unrelated-model",
            "LLM_API_KEY": "test-inherited-key",
            "LLM_BASE_URL": "https://inherited.example/v1",
        })

        client = self.service.get_llm()

        self.client_factory.assert_called_once_with(
            model="project-model",
            api_key="test-project-key",
            base_url="https://project.example/v1",
            timeout=120,
            provider="custom",
        )
        self.dotenv_values.assert_called_once_with(
            SERVICE_PATH.parents[2] / ".env", interpolate=False
        )
        self.assertIs(client, self.service.get_llm())
        self.client_factory.assert_called_once()

    def test_legacy_openai_settings_remain_supported(self):
        self.dotenv_values.return_value = {
            "OPENAI_MODEL": "legacy-model",
            "OPENAI_API_KEY": "test-legacy-key",
            "OPENAI_BASE_URL": "https://legacy.example/v1",
        }

        self.service.get_llm()

        self.client_factory.assert_called_once_with(
            model="legacy-model",
            api_key="test-legacy-key",
            base_url="https://legacy.example/v1",
            timeout=60,
            provider="custom",
        )

    def test_environment_only_configuration_remains_supported(self):
        self.service.os.environ.update({
            "LLM_MODEL_ID": "environment-model",
            "LLM_API_KEY": "test-environment-key",
            "LLM_BASE_URL": "https://environment.example/v1",
        })

        self.service.get_llm()

        self.assertEqual(self.client_factory.call_args.kwargs["model"], "environment-model")
        self.assertEqual(self.client_factory.call_args.kwargs["api_key"], "test-environment-key")
        self.assertEqual(self.client_factory.call_args.kwargs["provider"], "custom")

    def test_missing_model_does_not_silently_choose_a_default(self):
        self.dotenv_values.return_value = {"LLM_API_KEY": "test-project-key"}

        with self.assertRaisesRegex(ValueError, "LLM_MODEL_ID"):
            self.service.get_llm()

        self.client_factory.assert_not_called()

    def test_missing_key_fails_before_client_creation(self):
        self.dotenv_values.return_value = {"LLM_MODEL_ID": "project-model"}

        with self.assertRaisesRegex(ValueError, "LLM_API_KEY"):
            self.service.get_llm()

        self.client_factory.assert_not_called()

    def test_invalid_timeout_fails_before_client_creation(self):
        for timeout in ("0", "-1", "invalid"):
            with self.subTest(timeout=timeout):
                self.dotenv_values.return_value = {
                    "LLM_MODEL_ID": "project-model",
                    "LLM_API_KEY": "test-project-key",
                    "LLM_TIMEOUT": timeout,
                }
                with self.assertRaisesRegex(ValueError, "LLM_TIMEOUT"):
                    self.service.get_llm()
                self.client_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
