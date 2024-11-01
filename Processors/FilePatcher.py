"""A processor that applies a patch to a specified target file using the `patch` command."""
#
# Usage:
#
# Generate a patch file using the `diff` command, e.g.:
#    diff -Naur original_file modified_file > my_patch.patch
#
# The FilePatcher class expects the following input variables:
#     - patch_file (str): Path to the patch file. This variable is required.
#     - target_file (str): Path to the target file to be patched. This variable is required.

# The class does not define any output variables.

import subprocess
from autopkglib import Processor, ProcessorError

__all__ = ["FilePatcher"]

class FilePatcher(Processor):
    """Applies a patch to a file."""
    description: str = (
        "Applies a patch to a file. Generate a patch file using the `diff` command, "
        "e.g.: `diff -Naur original_file modified_file > my_patch.patch`"
    )
    input_variables: dict[str, dict[str, str | bool]] = {
        "patch_file": {
            "required": True,
            "description": "Path to the patch file."
        },
        "target_file": {
            "required": True,
            "description": "Path to the target file to be patched."
        }
    }
    output_variables: dict = {}

    def main(self) -> None:
        patch_file: str = self.env.get("patch_file")
        target_file: str = self.env.get("target_file")

        try:
            subprocess.run(["patch", target_file, patch_file], check=True)
            self.output(f"Successfully applied patch {patch_file} to {target_file}")
        except subprocess.CalledProcessError as e:
            raise ProcessorError(f"Failed to apply patch: {e}") from e

if __name__ == "__main__":
    processor = FilePatcher()
    processor.execute_shell()
