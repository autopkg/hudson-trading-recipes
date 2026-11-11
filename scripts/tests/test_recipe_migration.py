from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from scripts import recipe_migration as migration

OLD_NAMESPACE = "com.github.example"
OLD_DOWNLOAD = f"{OLD_NAMESPACE}.download.Example"
OLD_MUNKI = f"{OLD_NAMESPACE}.munki.Example"
NEW_DOWNLOAD = "com.github.hudson-trading.download.Example"
NEW_MUNKI = "com.github.hudson-trading.munki.Example"


def run_git(
    repo: Path, *arguments: str, environment: dict[str, str] | None = None
) -> str:
    command_environment = os.environ.copy()
    if environment:
        command_environment.update(environment)
    result = subprocess.run(
        ("git", "-C", str(repo), *arguments),
        check=True,
        text=True,
        capture_output=True,
        env=command_environment,
    )
    return result.stdout.strip()


def init_repo(path: Path) -> None:
    path.mkdir()
    run_git(path, "init", "-b", "main")
    run_git(path, "config", "user.name", "Recipe Migration Tests")
    run_git(path, "config", "user.email", "recipe-migration@example.com")


def plist_recipe(identifier: str, parent: str | None = None) -> bytes:
    recipe = {
        "Description": "Example",
        "Identifier": identifier,
        "Input": {"NAME": "Example"},
        "MinimumVersion": "2.3",
    }
    if parent:
        recipe["ParentRecipe"] = parent
    recipe["Process"] = [{"Processor": "URLDownloader"}]
    return plistlib.dumps(recipe, sort_keys=False)


class GitHubRemoteTests(unittest.TestCase):
    def test_logical_absolute_preserves_pwd(self) -> None:
        with mock.patch.dict("os.environ", {"PWD": "/home/clay/src/packaging"}):
            self.assertEqual(
                migration.logical_absolute(Path("hudson-trading-recipes")),
                Path("/home/clay/src/packaging/hudson-trading-recipes"),
            )

    def test_supported_github_urls(self) -> None:
        expected = ("autopkg", "hudson-trading-recipes")
        self.assertEqual(
            migration.github_repository(
                "https://github.com/autopkg/hudson-trading-recipes.git"
            ),
            expected,
        )
        self.assertEqual(
            migration.github_repository(
                "git@github.com:autopkg/hudson-trading-recipes.git"
            ),
            expected,
        )
        self.assertIsNone(migration.github_repository("DISABLED"))

    def test_expected_remote_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            init_repo(source)
            init_repo(target)
            run_git(
                source,
                "remote",
                "add",
                "origin",
                "https://github.com/autopkg/example-recipes",
            )
            run_git(
                target,
                "remote",
                "add",
                "origin",
                "https://github.com/contributor/hudson-trading-recipes",
            )
            run_git(
                target,
                "remote",
                "add",
                "upstream",
                "https://github.com/autopkg/hudson-trading-recipes",
            )
            run_git(target, "remote", "set-url", "--push", "upstream", "DISABLED")
            run_git(target, "config", "remote.pushDefault", "origin")
            run_git(target, "config", "branch.main.remote", "origin")
            run_git(target, "config", "branch.main.merge", "refs/heads/main")

            migration.validate_remotes(source, target)

    def test_official_repository_cannot_be_target_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            init_repo(source)
            init_repo(target)
            run_git(
                source,
                "remote",
                "add",
                "origin",
                "https://github.com/autopkg/example-recipes",
            )
            run_git(
                target,
                "remote",
                "add",
                "origin",
                "https://github.com/autopkg/hudson-trading-recipes",
            )
            run_git(
                target,
                "remote",
                "add",
                "upstream",
                "https://github.com/autopkg/hudson-trading-recipes",
            )
            run_git(target, "remote", "set-url", "--push", "upstream", "DISABLED")
            run_git(target, "config", "remote.pushDefault", "origin")
            run_git(target, "config", "branch.main.remote", "origin")
            run_git(target, "config", "branch.main.merge", "refs/heads/main")

            with self.assertRaises(migration.MigrationError):
                migration.validate_remotes(source, target)


class RecipeEditingTests(unittest.TestCase):
    def test_xml_identifier_rewrite_and_deprecation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_dir = PurePosixPath("Example")
            directory = root / "Example"
            directory.mkdir()
            download = directory / "Example.download.recipe"
            munki = directory / "Example.munki.recipe"
            download.write_bytes(plist_recipe(OLD_DOWNLOAD))
            munki.write_bytes(plist_recipe(OLD_MUNKI, OLD_DOWNLOAD))
            identifiers = {OLD_DOWNLOAD: NEW_DOWNLOAD, OLD_MUNKI: NEW_MUNKI}

            migration.rewrite_identifiers(root, recipe_dir, identifiers)

            rewritten_download = plistlib.loads(download.read_bytes())
            rewritten_munki = plistlib.loads(munki.read_bytes())
            self.assertEqual(rewritten_download["Identifier"], NEW_DOWNLOAD)
            self.assertEqual(rewritten_munki["Identifier"], NEW_MUNKI)
            self.assertEqual(rewritten_munki["ParentRecipe"], NEW_DOWNLOAD)

            download.write_bytes(plist_recipe(OLD_DOWNLOAD))
            munki.write_bytes(plist_recipe(OLD_MUNKI, OLD_DOWNLOAD))
            paths = {
                PurePosixPath("Example/Example.download.recipe"): (
                    OLD_DOWNLOAD,
                    NEW_DOWNLOAD,
                ),
                PurePosixPath("Example/Example.munki.recipe"): (OLD_MUNKI, NEW_MUNKI),
            }
            migration.add_deprecation_warnings(root, recipe_dir, paths)

            deprecated = plistlib.loads(download.read_bytes())
            self.assertEqual(
                deprecated["Process"][0], migration.warning_processor(NEW_DOWNLOAD)
            )

    @unittest.skipIf(migration.yaml is None, "PyYAML is not installed")
    def test_yaml_edits_preserve_comments_and_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_dir = PurePosixPath("Example")
            directory = root / "Example"
            directory.mkdir()
            path = directory / "Example.munki.recipe.yaml"
            original = f"""# retained comment
Identifier: '{OLD_MUNKI}'
ParentRecipe: \"{OLD_DOWNLOAD}\"
MinimumVersion: \"2.3\"
Process:
  - Processor: MunkiImporter
"""
            path.write_text(original, encoding="utf-8")
            identifiers = {OLD_DOWNLOAD: NEW_DOWNLOAD, OLD_MUNKI: NEW_MUNKI}

            migration.rewrite_identifiers(root, recipe_dir, identifiers)
            rewritten = path.read_text(encoding="utf-8")
            self.assertIn("# retained comment", rewritten)
            self.assertIn(f"Identifier: '{NEW_MUNKI}'", rewritten)
            self.assertIn(f'ParentRecipe: "{NEW_DOWNLOAD}"', rewritten)

            path.write_text(original, encoding="utf-8")
            mapping = {
                PurePosixPath("Example/Example.munki.recipe.yaml"): (
                    OLD_MUNKI,
                    NEW_MUNKI,
                )
            }
            migration.add_deprecation_warnings(root, recipe_dir, mapping)
            deprecated_text = path.read_text(encoding="utf-8")
            self.assertIn("# retained comment", deprecated_text)
            document = migration.parse_recipe(
                PurePosixPath("Example/Example.munki.recipe.yaml"),
                deprecated_text.encode(),
            )
            self.assertEqual(
                document.data["Process"][0], migration.warning_processor(NEW_MUNKI)
            )

    @unittest.skipIf(migration.yaml is None, "PyYAML is not installed")
    def test_yaml_empty_process_gets_deprecation_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_dir = PurePosixPath("Example")
            directory = root / "Example"
            directory.mkdir()
            path = directory / "Example.download.recipe.yaml"
            path.write_text(
                f"Identifier: {OLD_DOWNLOAD}\nProcess: []\n", encoding="utf-8"
            )
            mapping = {
                PurePosixPath("Example/Example.download.recipe.yaml"): (
                    OLD_DOWNLOAD,
                    NEW_DOWNLOAD,
                )
            }

            migration.add_deprecation_warnings(root, recipe_dir, mapping)

            document = migration.parse_recipe(
                PurePosixPath("Example/Example.download.recipe.yaml"), path.read_bytes()
            )
            self.assertEqual(
                document.data["Process"][0], migration.warning_processor(NEW_DOWNLOAD)
            )

    @unittest.skipIf(migration.yaml is None, "PyYAML is not installed")
    def test_yaml_flow_process_preserves_inline_comment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_dir = PurePosixPath("Example")
            directory = root / "Example"
            directory.mkdir()
            path = directory / "Example.download.recipe.yaml"
            path.write_text(
                f"Identifier: {OLD_DOWNLOAD}\n"
                "Process: [{Processor: URLDownloader}] # retained\n",
                encoding="utf-8",
            )
            mapping = {
                PurePosixPath("Example/Example.download.recipe.yaml"): (
                    OLD_DOWNLOAD,
                    NEW_DOWNLOAD,
                )
            }

            migration.add_deprecation_warnings(root, recipe_dir, mapping)

            text = path.read_text(encoding="utf-8")
            self.assertIn("# retained", text)
            document = migration.parse_recipe(
                PurePosixPath("Example/Example.download.recipe.yaml"), text.encode()
            )
            self.assertEqual(
                document.data["Process"][0], migration.warning_processor(NEW_DOWNLOAD)
            )


class ImportIntegrationTests(unittest.TestCase):
    def test_import_replays_history_and_creates_identifier_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            init_repo(source)
            init_repo(target)

            source_recipe = source / "Example" / "Example.download.recipe"
            source_recipe.parent.mkdir()
            source_recipe.write_bytes(plist_recipe(OLD_DOWNLOAD))
            run_git(source, "add", "Example")
            historical_date = "2024-01-02T03:04:05-0500"
            run_git(
                source,
                "commit",
                "-m",
                "Add Example recipe",
                environment={
                    "GIT_AUTHOR_DATE": historical_date,
                    "GIT_COMMITTER_DATE": historical_date,
                },
            )
            run_git(
                source,
                "update-ref",
                "refs/remotes/origin/main",
                "refs/heads/main",
            )

            (target / "README.md").write_text("target\n", encoding="utf-8")
            run_git(target, "add", "README.md")
            run_git(target, "commit", "-m", "Initial target")
            run_git(
                target,
                "update-ref",
                "refs/remotes/upstream/main",
                "refs/heads/main",
            )

            recipe_dir = PurePosixPath("Example")
            plan = migration.build_plan(
                source, "origin/main", recipe_dir, OLD_NAMESPACE
            )
            args = argparse.Namespace(
                source_repo=source,
                target_repo=target,
                recipe_dir=recipe_dir,
                source_branch="main",
                target_branch="main",
                branch="migrate-example",
            )

            migration.apply_import(args, plan)

            migrated = plistlib.loads(
                (target / "Example" / "Example.download.recipe").read_bytes()
            )
            self.assertEqual(migrated["Identifier"], NEW_DOWNLOAD)
            self.assertEqual(
                run_git(target, "branch", "--show-current"), "migrate-example"
            )
            messages = run_git(
                target, "log", "--format=%s", "upstream/main..HEAD"
            ).splitlines()
            self.assertEqual(
                messages,
                ["Use HRT identifiers for Example recipes", "Add Example recipe"],
            )
            source_author_date = run_git(
                source, "show", "-s", "--format=%aI", "origin/main"
            )
            imported_committer_date = run_git(
                target, "show", "-s", "--format=%cI", "HEAD~1"
            )
            self.assertEqual(imported_committer_date, source_author_date)


if __name__ == "__main__":
    unittest.main()
