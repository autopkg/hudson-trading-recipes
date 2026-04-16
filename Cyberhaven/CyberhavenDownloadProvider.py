"""A processor to authenticate with the Cyberhaven API and fetch the latest macOS installer."""

# This module contains a processor that authenticates with the Cyberhaven API
# using an API credential (refresh token), then downloads the latest macOS
# installer and extracts the version from the X-Installer-Version response
# header.
#
# Usage:
#     The CyberhavenDownloadProvider class expects two input variables:
#         - cyberhaven_api_credential: API refresh token (access key) for
#           authentication.
#         - cyberhaven_base_url: Base URL for the Cyberhaven tenant
#           (e.g. https://yourcompany.cyberhaven.io).
#     The processor outputs the following variables:
#         - version: Version of the latest macOS installer, with build info
#           stripped (e.g. 26.01.03.24329).
#         - raw_version: Full version string before build-info stripping
#           (e.g. 26.01.03.24329-0de7ab+).
#         - pathname: Path to the downloaded installer pkg.

import json
import os
import re
import tempfile
from typing import Any, ClassVar

from autopkglib import ProcessorError
from autopkglib.URLGetter import URLGetter

__all__ = ["CyberhavenDownloadProvider"]


class CyberhavenDownloadProvider(URLGetter):
    """Authenticate with the Cyberhaven API and download the latest macOS installer."""

    description = __doc__
    input_variables: ClassVar[dict[str, dict[str, Any]]] = {
        "cyberhaven_api_credential": {
            "required": True,
            "description": "Cyberhaven API refresh token (API access key).",
        },
        "cyberhaven_base_url": {
            "required": True,
            "description": (
                "Base URL for the Cyberhaven tenant "
                "(e.g. https://yourcompany.cyberhaven.io)."
            ),
        },
    }
    output_variables: ClassVar[dict[str, dict[str, str]]] = {
        "version": {"description": "Version of the latest macOS installer."},
        "raw_version": {
            "description": (
                "Full version string before build-info stripping "
                "(e.g. 26.01.03.24329-0de7ab+)."
            ),
        },
        "pathname": {"description": "Path to the downloaded installer pkg."},
    }

    def _normalized_base_url(self, base_url: str) -> str:
        if not base_url.startswith(("https://", "http://")):
            base_url = f"https://{base_url}"
        return base_url.rstrip("/")

    def _fetch_access_token(self, refresh_token: str, base_url: str) -> str:
        token_url = f"{base_url}/public/v2/auth/token/access"

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
        }
        payloads = [
            {"refresh_token": refresh_token},
            {"refreshToken": refresh_token},
        ]

        last_error = "unknown auth response"

        for payload in payloads:
            curl_opts = [
                "--url",
                token_url,
                "--request",
                "POST",
                "--data",
                json.dumps(payload),
            ]

            try:
                curl_cmd = self.prepare_curl_cmd()
                self.add_curl_headers(curl_cmd, headers)
                curl_cmd.extend(curl_opts)
                response_token = self.download_with_curl(curl_cmd)
            except Exception as e:
                raise ProcessorError(
                    f"Failed to authenticate with the Cyberhaven API: {e}"
                ) from e

            try:
                if isinstance(response_token, bytes):
                    response_token = response_token.decode("utf-8")
                json_data = json.loads(response_token)
                if not isinstance(json_data, dict):
                    last_error = "auth response was not a JSON object"
                    continue

                json_obj: dict[str, Any] = json_data
                access_token_value: Any = (
                    json_obj.get("access_token")
                    or json_obj.get("accessToken")
                    or json_obj.get("token")
                )

                data_obj = json_obj.get("data")
                if not access_token_value and isinstance(data_obj, dict):
                    access_token_value = (
                        data_obj.get("access_token")
                        or data_obj.get("accessToken")
                        or data_obj.get("token")
                    )

                if access_token_value:
                    access_token = str(access_token_value)
                    self.output("Successfully acquired bearer token.", verbose_level=2)
                    return access_token

                error_hint_val = (
                    json_obj.get("message")
                    or json_obj.get("error")
                    or json_obj.get("detail")
                    or json_obj.get("errors")
                )
                error_hint = (
                    str(error_hint_val) if error_hint_val else "unknown auth response"
                )
                available_keys = ", ".join(sorted(str(k) for k in json_obj.keys()))
                last_error = f"{error_hint}. Keys: [{available_keys}]"
            except json.JSONDecodeError as e:
                raise ProcessorError(
                    f"Failed to parse bearer token from auth response: {e}"
                ) from e

        raise ProcessorError(
            "Failed to parse bearer token from auth response: "
            "Auth response did not include an access token. "
            f"Hint: {last_error}"
        )

    def _download_installer(
        self, base_url: str, access_token: str, name: str, cache_dir: str
    ) -> tuple[str, str]:
        download_url = f"{base_url}/public/v2/installer/macos/latest"
        download_path = os.path.join(cache_dir, f"{name}.pkg")
        header_file = os.path.join(cache_dir, "response_headers.txt")

        auth_headers = {
            "accept": "application/octet-stream",
            "authorization": f"Bearer {access_token}",
        }
        curl_opts = [
            "--url",
            download_url,
            "--location",
            "--output",
            download_path,
            "--dump-header",
            header_file,
        ]

        try:
            curl_cmd = self.prepare_curl_cmd()
            self.add_curl_headers(curl_cmd, auth_headers)
            curl_cmd.extend(curl_opts)
            self.download_with_curl(curl_cmd)
        except Exception as e:
            raise ProcessorError(
                f"Failed to download installer from {download_url}: {e}"
            ) from e

        if not os.path.exists(download_path):
            raise ProcessorError(
                f"Download completed but file not found at {download_path}"
            )
        return download_path, header_file

    def _extract_version_from_headers(self, header_file: str) -> str:
        try:
            with open(header_file, "r", encoding="utf-8") as f:
                header_content = f.read()
        except OSError as e:
            raise ProcessorError(f"Failed to read response headers: {e}") from e

        self.output(f"Response headers:\n{header_content}", verbose_level=3)

        version = None
        for line in header_content.splitlines():
            match = re.match(r"^X-Installer-Version:\s*(.+)$", line, re.IGNORECASE)
            if match:
                version = match.group(1).strip()
                break

        if not version:
            for line in header_content.splitlines():
                cd_match = re.match(
                    r"^Content-Disposition:.*filename=.*?(\d+\.\d+\.\d+\.\d+)",
                    line,
                    re.IGNORECASE,
                )
                if cd_match:
                    version = cd_match.group(1).strip()
                    break

        if not version:
            raise ProcessorError(
                "Could not determine version from response headers. "
                "Headers received:\n" + header_content
            )

        return version

    def main(self) -> None:
        env: dict[str, Any] = self.env  # type: ignore[assignment]
        refresh_token = str(env.get("cyberhaven_api_credential", ""))
        base_url = str(env.get("cyberhaven_base_url", ""))

        if not refresh_token or refresh_token == "%CYBERHAVEN_API_CREDENTIAL%":
            raise ProcessorError(
                "The input variable 'cyberhaven_api_credential' was not set!"
            )
        if not base_url or base_url == "%CYBERHAVEN_BASE_URL%":
            raise ProcessorError(
                "The input variable 'cyberhaven_base_url' was not set!"
            )

        base_url = self._normalized_base_url(base_url)
        self.output(f"Tenant URL: {base_url}", verbose_level=2)
        access_token = self._fetch_access_token(refresh_token, base_url)

        name = str(env.get("NAME", "Cyberhaven"))
        cache_dir = str(
            env.get(
                "RECIPE_CACHE_DIR",
                os.path.join(tempfile.gettempdir(), "cyberhaven"),
            )
        )
        os.makedirs(cache_dir, exist_ok=True)

        # The Cyberhaven API (gRPC gateway) does not support HEAD requests or
        # conditional headers (ETag / Last-Modified), so we must always
        # download the full installer and compare against the cache afterward.
        self.output("Downloading installer...", verbose_level=1)
        download_path, header_file = self._download_installer(
            base_url=base_url,
            access_token=access_token,
            name=name,
            cache_dir=cache_dir,
        )
        download_size = os.path.getsize(download_path)
        self.output(f"Download complete ({download_size} bytes).", verbose_level=2)

        version = self._extract_version_from_headers(header_file)
        os.remove(header_file)

        # Strip build info (e.g. 26.01.03.24329-0de7ab+ -> 26.01.03.24329).
        raw_version = version
        version = version.split("-", 1)[0]
        if version != raw_version:
            self.output(
                f"Stripped build info: {raw_version} -> {version}",
                verbose_level=2,
            )

        self.output(f"Installer version: {version}", verbose_level=1)

        final_path = os.path.join(cache_dir, f"{name}-{version}.pkg")

        # Compare against any previously cached copy of the same version.
        if os.path.exists(final_path):
            cached_size = os.path.getsize(final_path)
            self.output(
                f"Cached file exists: {final_path} ({cached_size} bytes)",
                verbose_level=2,
            )
            if cached_size == download_size:
                os.remove(download_path)
                self.output(
                    f"Installer unchanged (version {version}, "
                    f"{cached_size} bytes), skipping.",
                    verbose_level=1,
                )
                env["version"] = version
                env["raw_version"] = raw_version
                env["pathname"] = final_path
                env["download_changed"] = False
                return
            self.output(
                f"Size changed ({cached_size} -> {download_size} bytes), "
                "replacing cached installer.",
                verbose_level=2,
            )
            os.remove(final_path)

        os.rename(download_path, final_path)
        self.output(f"Downloaded to: {final_path}", verbose_level=2)

        env["version"] = version
        env["raw_version"] = raw_version
        env["pathname"] = final_path
        env["download_changed"] = True


if __name__ == "__main__":
    processor = CyberhavenDownloadProvider()
    processor.execute_shell()
