"""A processor to extract Druva package information."""
# This module contains a processor to extract Druva package information from a given URL.
# It fetches the download data, parses it, and extracts version and package details for macOS.
#
# Usage:
#     The DruvaExtractor class expects two input variables:
#         - url: URL to fetch Druva download information, e.g.
#           https://downloads.druva.com/insync/js/data.json.
#         - govcloud: Set to True to fetch the GovCloud version of the package.
#     The processor outputs the following variables:
#         - version: The version of the Druva package.
#         - installer_version: The installer version of the Druva package.
#         - download_url: The download URL for the Druva package.

import json

try:
    from packaging.version import Version
except ImportError:
    from distutils.version import LooseVersion as Version
from autopkglib import URLGetter, ProcessorError

__all__ = ["DruvaExtractor"]


class DruvaExtractor(URLGetter):
    """Extract Version and Package information from Druva download output."""

    description: str = "Extracts Druva package information from the URL."
    input_variables: dict[str, dict[str, str | bool]] = {
        "url": {
            "required": True,
            "description": "URL to fetch Druva download information, e.g. "
            "https://downloads.druva.com/insync/js/data.json.",
        },
        "govcloud": {
            "required": False,
            "description": "Set to True to fetch the GovCloud version of the package.",
        },
    }
    output_variables: dict = {
        "version": {"description": "The version of the Druva package."},
        "installer_version": {
            "description": "The installer version of the Druva package."
        },
        "build_number": {
            "description": "The build number of the Druva package."
        },
        "download_url": {"description": "The name of the Druva package."},
    }

    def main(self) -> None:
        """Main function."""
        url: str = self.env.get("url")
        govcloud: bool = self.env.get("govcloud", False)
        response: str = self.download(url)

        try:
            data = json.loads(response)
        except json.JSONDecodeError as e:
            raise ProcessorError(f"Failed to parse JSON from {url}: {e}") from e

        mac_items = [i for i in data if i["title"] == "macOS"]

        if not mac_items:
            raise ProcessorError("No macOS items found in the download data.")

        if govcloud:
            self.output("Fetching insync GOVCloud version of the package.", verbose_level=2)
            mac_item = [i for i in mac_items if 'GOVCloud' in i['cloudopsNotes']]
            if not mac_item:
                raise ProcessorError("No insync GOVcloud macOS items found in the download data")
        else:
            self.output("Fetching insync main cloud version of the package.", verbose_level=2)
            mac_item = [i for i in mac_items if 'GOVCloud' not in i['cloudopsNotes']]
            if not mac_item:
                raise ProcessorError("No insync main cloud macOS items found in the download data")

        if len(mac_item) != 1:
            raise ProcessorError("Multiple macOS items found in the download data.")

        latest_version = str(
            max(Version(version) for version in mac_item[0]["supportedVersions"])
        )
        package = [i for i in mac_item[0]["installerDetails"] if i["version"] == latest_version]

        if not package:
            raise ProcessorError(f"No package found for version {latest_version}.")

        if len(package) > 1:
            raise ProcessorError(
                f"Multiple packages found for version {latest_version}."
            )

        main_version = package[0]["version"]
        full_version = package[0]["installerVersion"]
        build_number = full_version.split("-")[-1] if "r" in full_version else None
        if not build_number:
            raise ProcessorError(
                f"Failed to extract build number from installerVersion: {full_version}")
        self.env["build_number"] = build_number
        self.env["version"] = f"{main_version}-{build_number}"
        self.env["download_url"] = package[0]["downloadURL"]


if __name__ == "__main__":
    processor = DruvaExtractor()
    processor.execute_shell()
