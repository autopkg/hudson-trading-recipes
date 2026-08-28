"""Tests for ElasticFleetVersionResolver that do not require AutoPkg or Kibana."""

import base64
import importlib.util
import pathlib
import sys
import types
import unittest


class ProcessorError(Exception):
    """AutoPkg ProcessorError test double."""


class URLGetter:
    """Minimal URLGetter test double."""

    def __init__(self):
        self.env = {}
        self.messages = []

    def output(self, message, verbose_level=1):
        self.messages.append((message, verbose_level))


autopkglib = types.ModuleType("autopkglib")
autopkglib.ProcessorError = ProcessorError
urlgetter_module = types.ModuleType("autopkglib.URLGetter")
urlgetter_module.URLGetter = URLGetter
sys.modules.setdefault("autopkglib", autopkglib)
sys.modules.setdefault("autopkglib.URLGetter", urlgetter_module)

MODULE_PATH = pathlib.Path(__file__).parents[1] / "ElasticFleetVersionResolver.py"
SPEC = importlib.util.spec_from_file_location("elastic_resolver", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Resolver = MODULE.ElasticFleetVersionResolver


class TestResolver(unittest.TestCase):
    def resolver(self, responses, **environment):
        instance = Resolver()
        instance.env = environment
        instance._get_json = lambda path: responses[path]
        return instance

    def test_resolves_latest_release_and_artifact_url(self):
        instance = self.resolver(
            {
                "/api/fleet/agents/available_versions": {
                    "items": ["8.19.10", "9.1.2-SNAPSHOT", "9.1.1"]
                },
                "/api/fleet/agent_download_sources": {
                    "items": [
                        {
                            "name": "Elastic Artifacts",
                            "host": "https://artifacts.elastic.co/downloads/",
                            "is_default": True,
                        }
                    ]
                },
            },
            KIBANA_URL="https://kibana.example.test",
            ALLOW_SNAPSHOT="false",
        )

        instance.main()

        self.assertEqual(instance.env["version"], "9.1.1")
        self.assertEqual(instance.env["ELASTIC_VERSION"], "9.1.1")
        self.assertEqual(instance.env["ELASTIC_ARCH"], "aarch64")
        self.assertEqual(
            instance.env["download_url"],
            "https://artifacts.elastic.co/downloads/beats/elastic-agent",
        )

    def test_explicit_version_wins_but_source_is_still_resolved(self):
        instance = self.resolver(
            {
                "/api/fleet/agent_download_sources": {
                    "items": [{"name": "Mirror", "host": "https://mirror.test/root"}]
                }
            },
            KIBANA_URL="https://kibana.example.test",
            ELASTIC_VERSION="8.18.7",
            ELASTIC_ARCH="x86_64",
        )

        instance.main()

        self.assertEqual(instance.env["ELASTIC_VERSION"], "8.18.7")
        self.assertEqual(instance.env["ELASTIC_ARCH"], "x86_64")
        self.assertEqual(
            instance.env["download_url"],
            "https://mirror.test/root/beats/elastic-agent",
        )

    def test_policy_version_and_encoded_policy_id(self):
        instance = Resolver()
        instance.env = {
            "KIBANA_URL": "https://kibana.example.test",
            "AGENT_POLICY_ID": "policy/one",
        }
        paths = []

        def response(path):
            paths.append(path)
            return {"item": {"required_versions": [{"version": "9.0.4"}]}}

        instance._get_json = response

        self.assertEqual(instance._resolve_version(), "9.0.4")
        self.assertEqual(paths, ["/api/fleet/agent_policies/policy%2Fone"])

    def test_requires_kibana_url(self):
        instance = Resolver()
        instance.env = {"ELASTIC_VERSION": "9.1.0"}

        with self.assertRaisesRegex(ProcessorError, "KIBANA_URL is required"):
            instance.main()

    def test_auth_headers_support_api_key_and_basic_auth(self):
        instance = Resolver()
        instance.env = {"KIBANA_API_KEY": "secret"}
        self.assertEqual(instance._auth_headers()["Authorization"], "ApiKey secret")

        instance.env = {"KIBANA_USERNAME": "elastic", "KIBANA_PASSWORD": "secret"}
        expected = base64.b64encode(b"elastic:secret").decode()
        self.assertEqual(
            instance._auth_headers()["Authorization"], f"Basic {expected}"
        )

    def test_auth_headers_require_credentials(self):
        instance = Resolver()
        instance.env = {}

        with self.assertRaisesRegex(ProcessorError, "Set KIBANA_API_KEY"):
            instance._auth_headers()


if __name__ == "__main__":
    unittest.main()
