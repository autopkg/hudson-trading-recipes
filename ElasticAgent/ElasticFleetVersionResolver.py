"""Resolve the Elastic Agent version and download base URL via Kibana's Fleet API.

If ELASTIC_VERSION is set and non-empty, it wins. Otherwise the processor consults
Kibana to find the version this stack wants agents to run, and the configured
download source (so an internal mirror is honored automatically).
"""

import base64
import json
from urllib.parse import quote

from autopkglib import ProcessorError
from autopkglib.URLGetter import URLGetter

__all__ = ["ElasticFleetVersionResolver"]


def _version_key(v: str) -> tuple:
    """Return a sort key for a SemVer-ish string. Non-numeric parts sort lower."""
    parts = v.split("-", 1)[0].split(".")
    out = []
    for p in parts:
        try:
            out.append((1, int(p)))
        except ValueError:
            out.append((0, p))
    return tuple(out)


def _as_bool(value: object) -> bool:
    """Interpret common AutoPkg string values as booleans."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


class ElasticFleetVersionResolver(URLGetter):
    """Resolve elastic-agent version and download URL from Kibana's Fleet API."""

    description: str = __doc__
    input_variables: dict = {
        "KIBANA_URL": {
            "required": True,
            "description": "Base URL of the Kibana instance, no trailing slash.",
        },
        "KIBANA_API_KEY": {
            "required": False,
            "description": "Kibana API key. Sent as 'Authorization: ApiKey <key>'.",
        },
        "KIBANA_USERNAME": {
            "required": False,
            "description": "Basic-auth username (used when KIBANA_API_KEY is unset).",
        },
        "KIBANA_PASSWORD": {
            "required": False,
            "description": "Basic-auth password (used when KIBANA_API_KEY is unset).",
        },
        "ELASTIC_VERSION": {
            "required": False,
            "description": (
                "Explicit version override. If set and non-empty, the Kibana lookup "
                "is skipped for version selection."
            ),
        },
        "ELASTIC_ARCH": {
            "required": False,
            "description": (
                "Elastic Agent architecture ('aarch64' or 'x86_64'). Defaults to "
                "'aarch64' when unset."
            ),
        },
        "AGENT_POLICY_ID": {
            "required": False,
            "description": (
                "If set, read required_versions[0].version from this agent policy "
                "rather than picking the max of available_versions."
            ),
        },
        "ALLOW_SNAPSHOT": {
            "required": False,
            "description": "If true, include -SNAPSHOT versions when picking the max.",
        },
    }
    output_variables: dict = {
        "version": {"description": "Resolved elastic-agent version, e.g. '9.3.3'."},
        "ELASTIC_VERSION": {
            "description": "Resolved version for compatibility with child recipes."
        },
        "ELASTIC_ARCH": {
            "description": "Configured architecture, defaulting to 'aarch64'."
        },
        "download_url": {
            "description": (
                "Elastic Agent artifact URL derived from the default download source, "
                "e.g. 'https://artifacts.elastic.co/downloads/beats/elastic-agent'."
            ),
        },
    }

    def _auth_headers(self) -> dict:
        headers = {"kbn-xsrf": "true", "Accept": "application/json"}
        api_key = (self.env.get("KIBANA_API_KEY") or "").strip()
        if api_key:
            headers["Authorization"] = f"ApiKey {api_key}"
            return headers
        user = (self.env.get("KIBANA_USERNAME") or "").strip()
        pw = self.env.get("KIBANA_PASSWORD") or ""
        if not user or not pw:
            raise ProcessorError(
                "Set KIBANA_API_KEY or both KIBANA_USERNAME and KIBANA_PASSWORD."
            )
        token = base64.b64encode(f"{user}:{pw}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
        return headers

    def _get_json(self, path: str) -> dict:
        base = self.env["KIBANA_URL"].rstrip("/")
        url = f"{base}{path}"
        self.output(f"GET {url}", verbose_level=2)
        try:
            body = self.download(url, headers=self._auth_headers(), text=True)
        except ProcessorError as e:
            raise ProcessorError(f"Kibana request failed for {path}: {e}") from e
        try:
            return json.loads(body)
        except ValueError as e:
            raise ProcessorError(
                f"Kibana returned non-JSON for {path}: {body[:200]!r}"
            ) from e

    def _resolve_version(self) -> str:
        explicit = (self.env.get("ELASTIC_VERSION") or "").strip()
        if explicit:
            self.output(f"Using explicit ELASTIC_VERSION={explicit}")
            return explicit

        policy_id = (self.env.get("AGENT_POLICY_ID") or "").strip()
        if policy_id:
            data = self._get_json(
                f"/api/fleet/agent_policies/{quote(policy_id, safe='')}"
            )
            required = (data.get("item") or {}).get("required_versions") or []
            if required and required[0].get("version"):
                v = required[0]["version"]
                self.output(f"Using agent policy {policy_id} required_versions: {v}")
                return v
            self.output(
                f"Policy {policy_id} has no required_versions; falling back to "
                "available_versions."
            )

        data = self._get_json("/api/fleet/agents/available_versions")
        items = data.get("items") or []
        if not items:
            raise ProcessorError("Kibana returned an empty available_versions list.")
        allow_snapshot = _as_bool(self.env.get("ALLOW_SNAPSHOT"))
        candidates = [v for v in items if allow_snapshot or "-SNAPSHOT" not in v]
        if not candidates:
            raise ProcessorError(
                "No non-SNAPSHOT versions in available_versions; set ALLOW_SNAPSHOT."
            )
        chosen = max(candidates, key=_version_key)
        self.output(f"Picked max available_versions: {chosen}")
        return chosen

    def _resolve_download_url(self) -> str:
        data = self._get_json("/api/fleet/agent_download_sources")
        items = data.get("items") or []
        if not items:
            raise ProcessorError("Kibana returned no agent_download_sources entries.")
        default = next((i for i in items if i.get("is_default")), items[0])
        host = (default.get("host") or "").rstrip("/")
        if not host:
            raise ProcessorError(
                f"Default agent_download_sources entry has no host: {default!r}"
            )
        # Fleet download-source hosts are roots such as
        # https://artifacts.elastic.co/downloads/. Elastic Agent archives live
        # below beats/elastic-agent at every compatible mirror.
        artifact_base = f"{host}/beats/elastic-agent"
        self.output(
            f"Using download source '{default.get('name')}' host={artifact_base}"
        )
        return artifact_base

    def main(self) -> None:
        if not (self.env.get("KIBANA_URL") or "").strip():
            raise ProcessorError("KIBANA_URL is required.")
        architecture = (self.env.get("ELASTIC_ARCH") or "").strip()
        if not architecture:
            architecture = "aarch64"
            self.output("ELASTIC_ARCH is unset; defaulting to aarch64.")
        self.env["ELASTIC_ARCH"] = architecture
        version = self._resolve_version()
        self.env["version"] = version
        # Preserve the established recipe variable used by the pkg and Munki
        # parent recipes, including when the version came from Fleet.
        self.env["ELASTIC_VERSION"] = version
        self.env["download_url"] = self._resolve_download_url()


if __name__ == "__main__":
    PROCESSOR = ElasticFleetVersionResolver()
    PROCESSOR.execute_shell()
