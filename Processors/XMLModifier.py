"""A Processor to modify XML files by adding, removing, or changing elements."""
# pylint: disable=invalid-name
#
# Usage:
#     The XMLModifier class expects two input variables:
#         - xml_file: Path to the XML file to be modified.
#         - actions: List of actions to perform on the XML file. Each action should be
#           a dictionary with 'action' (add, remove, change), 'xpath', and 'value' (for
#           add/change).
#
#     Example:
#         actions = [
#             {"action": "remove", "xpath": ".//ElementToRemove"},
#             {"action": "add", "xpath": ".//ParentElement/NewElement", "value": "NewValue"},
#             {"action": "change", "xpath": ".//ElementToChange", "value": "UpdatedValue"}
#         ]


import xml.etree.ElementTree as ET
from autopkglib import Processor, ProcessorError

__all__ = ["XMLModifier"]


class XMLModifier(Processor):
    """Processor to remove, add, or change specified elements in an XML file."""

    description: str = (
        "Processor to remove, add, or change specified elements in an XML file."
    )
    input_variables: dict[dict[bool, str]] = {
        "xml_file": {
            "required": True,
            "description": "Path to the XML file to be modified.",
        },
        "actions": {
            "required": True,
            "description": ("List of actions to perform on the XML file. Each action "
                            "should be a dictionary with 'action' (add, remove, "
                            "change), 'xpath', and 'value' (for add/change)."),
        },
    }
    output_variables: dict = {}

    def main(self) -> None:
        """Modify the XML file."""
        xml_file: str = self.env["xml_file"]
        actions: list[dict[str, str]] = self.env["actions"]

        try:
            tree: ET.ElementTree = ET.parse(xml_file)
            root: ET.Element = tree.getroot()
        except Exception as e:
            raise ProcessorError(f"Error parsing XML file: {e}") from e

        for action in actions:
            action_type: str = action.get("action")
            xpath: str = action.get("xpath")
            value: str = action.get("value", None)

            if action_type == "remove":
                for elem in root.findall(xpath):
                    root.remove(elem)
            elif action_type == "add":
                parent_xpath, tag = xpath.rsplit("/", 1)
                parent = root.find(parent_xpath)
                if parent is not None:
                    new_elem: ET.Element = ET.Element(tag)
                    new_elem.text = value
                    parent.append(new_elem)
                else:
                    raise ProcessorError(
                        f"Parent element not found for xpath: {parent_xpath}"
                    )
            elif action_type == "change":
                for elem in root.findall(xpath):
                    elem.text = value
            else:
                raise ProcessorError(f"Unknown action type: {action_type}")

        tree.write(xml_file)


if __name__ == "__main__":
    processor = XMLModifier()
    processor.execute_shell()
