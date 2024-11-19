"""A processor that applies a patch to a specified target file using the `patch` command."""

#
# Usage:
#
# Generate a patch file using the `diff` command, e.g.:
#    diff -Naur original_file modified_file > my_patch.patch
#
# The FilePatcher class expects the following input variables:
#     - patch_file (str): Path to the patch file.
#     - patch_string (str): String containing the patch to apply. Should be XML-escaped.
#     - target_file (str): Path to the target file to be patched. This variable is required.

# The class does not define any output variables.

import subprocess
from autopkglib import Processor, ProcessorError

__all__ = ["FilePatcher"]


class FilePatcher(Processor):
    """Applies a patch to a file."""

    description: str = (
        "Applies a patch to a file. One of `patch_file` or `patch_string` must be "
        "supplied. Generate a patch file using the `diff` command, e.g.: "
        "`diff -Naur original_file modified_file > my_patch.patch`"
    )
    input_variables: dict[str, dict[str, str | bool]] = {
        "patch_file": {"required": False, "description": "Path to the patch file."},
        "patch_string": {
            "required": False,
            "description": "String containing the patch to apply. Should be XML-escaped.",
        },
        "target_file": {
            "required": True,
            "description": "Path to the target file to be patched.",
        },
    }
    output_variables: dict = {}

    def main(self) -> None:
        patch_file: str = self.env.get("patch_file")
        patch_string: str = self.env.get("patch_string")
        target_file: str = self.env.get("target_file")

        try:
            if patch_file:
                subprocess.run(["patch", target_file, patch_file], check=True)
                self.output(f"Successfully applied patch {patch_file} to {target_file}")
            elif patch_string:
                subprocess.run(
                    ["patch", target_file], input=patch_string, text=True, check=True
                )
                self.output(f"Successfully applied patch string to {target_file}")
            else:
                raise ProcessorError(
                    "One of `patch_file` or `patch_string` must be supplied."
                )

        except subprocess.CalledProcessError as e:
            raise ProcessorError(f"Failed to apply patch: {e}") from e


if __name__ == "__main__":
    processor = FilePatcher()
    processor.execute_shell()
