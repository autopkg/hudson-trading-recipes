import subprocess
from autopkglib import Processor, ProcessorError

__all__ = ["FilePatcher"]

class FilePatcher(Processor):
    """Applies a patch to a file."""
    description = "Applies a patch to a file."
    input_variables = {
        "patch_file": {
            "required": True,
            "description": "Path to the patch file."
        },
        "target_file": {
            "required": True,
            "description": "Path to the target file to be patched."
        }
    }
    output_variables = {}

    def main(self):
        patch_file = self.env.get("patch_file")
        target_file = self.env.get("target_file")

        try:
            subprocess.run(["patch", target_file, patch_file], check=True)
            self.output(f"Successfully applied patch {patch_file} to {target_file}")
        except subprocess.CalledProcessError as e:
            raise ProcessorError(f"Failed to apply patch: {e}") from e

if __name__ == "__main__":
    processor = FilePatcher()
    processor.execute_shell()
