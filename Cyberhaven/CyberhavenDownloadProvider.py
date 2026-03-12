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
#         - version: Version of the latest macOS installer, extracted from the
#           X-Installer-Version response header.
#         - pathname: Path to the downloaded installer pkg.

import json
import os
import re
import tempfile

from autopkglib import ProcessorError, URLGetter

__all__ = ["CyberhavenDownloadProvider"]


class CyberhavenDownloadProvider(URLGetter):
    """Authenticate with the Cyberhaven API and download the latest macOS installer."""

    description = __doc__
    input_variables = {
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
    output_variables = {
        "version": {
            "description": "Version of the latest macOS installer."
        },
        "pathname": {
            "description": "Path to the downloaded installer pkg."
        },
    }

    def main(self):
        refresh_token = self.env.get("cyberhaven_api_credential")
        base_url = self.env.get("cyberhaven_base_url", "")

        if not refresh_token or refresh_token == "%CYBERHAVEN_API_CREDENTIAL%":
            raise ProcessorError(
                "The input variable 'cyberhaven_api_credential' was not set!"
            )
        if not base_url or base_url == "%CYBERHAVEN_BASE_URL%":
            raise ProcessorError(
                "The input variable 'cyberhaven_base_url' was not set!"
            )

        # Ensure base_url has a scheme
        if not base_url.startswith(("https://", "http://")):
            base_url = f"https://{base_url}"
        # Strip trailing slash
        base_url = base_url.rstrip("/")

        # Step 1: Authenticate to get a bearer token
        token_url = f"{base_url}/public/v2/auth/token/access"
        token_payload = json.dumps({"refresh_token": refresh_token})

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
        }

        curl_opts = [
            "--url", token_url,
            "--request", "POST",
            "--data", token_payload,
        ]

        try:
            curl_cmd = self.prepare_curl_cmd()
            self.add_curl_headers(curl_cmd, headers)
            curl_cmd.extend(curl_opts)
            response_token = self.download_with_curl(curl_cmd)
        except Exception as e:
            raise ProcessorError(
                f"Failed to authenticate with the Cyberhaven API: {e}"
            )

        try:
            json_data = json.loads(response_token)
            access_token = json_data["access_token"]
            self.output("Successfully acquired bearer token.", verbose_level=2)
            self.output(f"Access Token: {access_token}", verbose_level=4)
        except (json.JSONDecodeError, KeyError) as e:
            raise ProcessorError(
                f"Failed to parse bearer token from auth response: {e}"
            )

        # Step 2: Download the installer, capturing response headers
        download_url = f"{base_url}/public/v2/installer/macos/latest"
        name = self.env.get("NAME", "Cyberhaven")
        cache_dir = self.env.get(
            "RECIPE_CACHE_DIR",
            os.path.join(tempfile.gettempdir(), "cyberhaven"),
        )
        os.makedirs(cache_dir, exist_ok=True)

        # Use a temp filename until we know the version
        download_path = os.path.join(cache_dir, f"{name}.pkg")
        header_file = os.path.join(cache_dir, "response_headers.txt")

        auth_headers = {
            "accept": "application/octet-stream",
            "authorization": f"Bearer {access_token}",
        }

        curl_opts = [
            "--url", download_url,
            "--location",
            "--output", download_path,
            "--dump-header", header_file,
        ]

        try:
            curl_cmd = self.prepare_curl_cmd()
            self.add_curl_headers(curl_cmd, auth_headers)
            curl_cmd.extend(curl_opts)
            self.download_with_curl(curl_cmd)
        except Exception as e:
            raise ProcessorError(
                f"Failed to download installer from {download_url}: {e}"
            )

        if not os.path.exists(download_path):
            raise ProcessorError(
                f"Download completed but file not found at {download_path}"
            )

        # Parse version from response headers
        version = None
        try:
            with open(header_file, "r") as f:
                header_content = f.read()
        except OSError as e:
            raise ProcessorError(f"Failed to read response headers: {e}")

        self.output(
            f"Response headers:\n{header_content}", verbose_level=3
        )

        for line in header_content.splitlines():
            match = re.match(
                r"^X-Installer-Version:\s*(.+)$", line, re.IGNORECASE
            )
            if match:
                version = match.group(1).strip()
                break

        if not version:
            # Fall back: try to parse version from Content-Disposition header
            for line in header_content.splitlines():
                cd_match = re.match(
                    r"^Content-Disposition:.*filename=.*?"
                    r"(\d+\.\d+\.\d+\.\d+)",
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

        # Rename to include version
        final_path = os.path.join(cache_dir, f"{name}-{version}.pkg")
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(download_path, final_path)

        # Clean up header file
        os.remove(header_file)

        self.output(f"Installer version: {version}", verbose_level=1)
        self.output(f"Downloaded to: {final_path}", verbose_level=2)

        self.env["version"] = version
        self.env["pathname"] = final_path
        self.env["download_changed"] = True


if __name__ == "__main__":
    processor = CyberhavenDownloadProvider()
    processor.execute_shell()
