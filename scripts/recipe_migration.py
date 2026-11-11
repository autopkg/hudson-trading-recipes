#!/usr/bin/env python3
"""Import HRT-owned AutoPkg recipes and deprecate their old copies."""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

try:
    import yaml
except ImportError:  # XML migrations do not need PyYAML.
    yaml = None


NEW_NAMESPACE = "com.github.hudson-trading"
OFFICIAL_TARGET = ("autopkg", "hudson-trading-recipes")
RECIPE_SUFFIXES = (".recipe", ".recipe.plist", ".recipe.yaml", ".recipe.yml")


class MigrationError(RuntimeError):
    """An expected, actionable migration failure."""


@dataclass(frozen=True)
class RecipeDocument:
    """A parsed recipe and its repository-relative path."""

    path: PurePosixPath
    data: dict[str, Any]
    format: str


@dataclass(frozen=True)
class MigrationPlan:
    """The source state and identifier changes for one directory."""

    files: tuple[PurePosixPath, ...]
    commits: tuple[str, ...]
    documents: tuple[RecipeDocument, ...]
    identifiers: dict[str, str]
    identifiers_by_path: dict[PurePosixPath, tuple[str, str]]


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command with predictable text output."""

    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode:
        rendered = " ".join(command)
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f"\n{detail}" if detail else ""
        raise MigrationError(
            f"Command failed ({result.returncode}): {rendered}{suffix}"
        )
    return result


def git(repo: Path, *arguments: str, check: bool = True) -> str:
    """Run Git in a repository and return stdout."""

    return run(
        ("git", "-C", str(repo), *arguments), check=check, capture=True
    ).stdout.rstrip("\n")


def repository_root_from_script() -> Path:
    return logical_absolute(Path(__file__)).parents[1]


def logical_absolute(path: Path) -> Path:
    """Make a path absolute without resolving user-facing symlinks."""

    if path.is_absolute():
        return path
    logical_cwd = Path(os.environ.get("PWD", os.getcwd()))
    return logical_cwd / path


def require_repository(path: Path, label: str) -> Path:
    path = logical_absolute(path.expanduser())
    if not path.is_dir():
        raise MigrationError(f"{label} repository does not exist: {path}")
    if git(path, "rev-parse", "--is-inside-work-tree", check=False) != "true":
        raise MigrationError(f"{label} path is not a Git repository: {path}")
    return path


def require_clean(repo: Path, label: str) -> None:
    status = git(repo, "status", "--porcelain")
    if status:
        raise MigrationError(
            f"{label} repository has uncommitted changes: {repo}\n"
            f"Run: git -C {repo} status --short"
        )


def recipe_directory(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise argparse.ArgumentTypeError(
            "recipe directory must be a relative repository path without '.' or '..'"
        )
    return path


def branch_slug(recipe_dir: PurePosixPath) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", recipe_dir.name.lower()).strip("-")
    if not slug:
        raise MigrationError(f"Cannot derive a branch name from {recipe_dir}")
    return slug


def github_repository(url: str) -> tuple[str, str] | None:
    """Return owner and repository for supported GitHub remote URLs."""

    value = url.strip()
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urlparse(value)
        if parsed.hostname != "github.com":
            return None
        path = parsed.path.lstrip("/")
    path = path.removesuffix(".git").rstrip("/")
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0].lower(), parts[1].lower()


def remote_url(repo: Path, remote: str, *, push: bool = False) -> str:
    arguments = (
        ("remote", "get-url", "--push", remote)
        if push
        else (
            "remote",
            "get-url",
            remote,
        )
    )
    value = git(repo, *arguments, check=False)
    if not value:
        kind = "push " if push else ""
        raise MigrationError(f"Missing {kind}URL for remote '{remote}' in {repo}")
    return value


def validate_remotes(source_repo: Path, target_repo: Path) -> None:
    """Enforce the source and destination remote layout."""

    source_url = remote_url(source_repo, "origin")
    source_github = github_repository(source_url)
    if (
        source_github is None
        or not source_github[1].endswith("-recipes")
        or source_github == OFFICIAL_TARGET
    ):
        raise MigrationError(
            "Source origin must be a GitHub repository whose name ends in -recipes; "
            f"found {source_url}"
        )
    source_push_url = remote_url(source_repo, "origin", push=True)
    if github_repository(source_push_url) != source_github:
        raise MigrationError(
            "Source origin must push to the same GitHub repository that it fetches; "
            f"fetch is {source_url}, push is {source_push_url}"
        )

    upstream_url = remote_url(target_repo, "upstream")
    if github_repository(upstream_url) != OFFICIAL_TARGET:
        raise MigrationError(
            "Target upstream must fetch from "
            "https://github.com/autopkg/hudson-trading-recipes; "
            f"found {upstream_url}"
        )

    origin_url = remote_url(target_repo, "origin")
    origin_github = github_repository(origin_url)
    if (
        origin_github is None
        or origin_github[1] != OFFICIAL_TARGET[1]
        or origin_github[0] == OFFICIAL_TARGET[0]
    ):
        raise MigrationError(
            "Target origin must be a contributor fork of hudson-trading-recipes; "
            f"found {origin_url}"
        )
    origin_push_url = remote_url(target_repo, "origin", push=True)
    if github_repository(origin_push_url) != origin_github:
        raise MigrationError(
            "Target origin must push to the contributor fork that it fetches; "
            f"fetch is {origin_url}, push is {origin_push_url}"
        )

    push_default = git(
        target_repo, "config", "--get", "remote.pushDefault", check=False
    )
    if push_default != "origin":
        raise MigrationError(
            "Target remote.pushDefault must be origin. Run:\n"
            f"  git -C {target_repo} config remote.pushDefault origin"
        )

    main_remote = git(target_repo, "config", "--get", "branch.main.remote", check=False)
    main_merge = git(target_repo, "config", "--get", "branch.main.merge", check=False)
    if main_remote != "origin" or main_merge != "refs/heads/main":
        raise MigrationError(
            "Target main must track origin/main. Run:\n"
            f"  git -C {target_repo} branch --set-upstream-to=origin/main main"
        )

    upstream_push = remote_url(target_repo, "upstream", push=True)
    if upstream_push != "DISABLED":
        raise MigrationError(
            "Target upstream push URL must be disabled. Run:\n"
            f"  git -C {target_repo} remote set-url --push upstream DISABLED"
        )


def require_yaml() -> Any:
    if yaml is None:
        raise MigrationError(
            "YAML recipe support requires PyYAML. Install the script requirements:\n"
            "  python3 -m pip install -r scripts/requirements.txt"
        )
    return yaml


def yaml_loader() -> type[Any]:
    yaml_module = require_yaml()

    class AutoPkgYAMLLoader(yaml_module.SafeLoader):
        pass

    AutoPkgYAMLLoader.yaml_implicit_resolvers = {
        key: [
            (tag, regexp)
            for tag, regexp in resolvers
            if tag != "tag:yaml.org,2002:float"
        ]
        for key, resolvers in (
            yaml_module.SafeLoader.yaml_implicit_resolvers.copy().items()
        )
    }
    return AutoPkgYAMLLoader


def recipe_format(path: PurePosixPath) -> str:
    return "yaml" if path.name.endswith((".yaml", ".yml")) else "plist"


def parse_recipe(path: PurePosixPath, content: bytes) -> RecipeDocument:
    format_name = recipe_format(path)
    try:
        if format_name == "yaml":
            parsed = require_yaml().load(content.decode("utf-8"), Loader=yaml_loader())
        else:
            parsed = plistlib.loads(content)
    except Exception as error:
        raise MigrationError(f"Cannot parse recipe {path}: {error}") from error
    if not isinstance(parsed, dict):
        raise MigrationError(f"Recipe root is not a mapping: {path}")
    return RecipeDocument(path=path, data=parsed, format=format_name)


def is_recipe(path: PurePosixPath) -> bool:
    return path.name.endswith(RECIPE_SUFFIXES)


def files_at_ref(
    repo: Path, ref: str, recipe_dir: PurePosixPath
) -> tuple[PurePosixPath, ...]:
    output = git(repo, "ls-tree", "-r", "--name-only", ref, "--", str(recipe_dir))
    return tuple(PurePosixPath(line) for line in output.splitlines() if line)


def documents_at_ref(
    repo: Path, ref: str, files: Iterable[PurePosixPath]
) -> tuple[RecipeDocument, ...]:
    documents = []
    for path in files:
        if not is_recipe(path):
            continue
        result = run(
            ("git", "-C", str(repo), "show", f"{ref}:{path}"),
            check=True,
            capture=True,
        )
        documents.append(parse_recipe(path, result.stdout.encode("utf-8")))
    return tuple(documents)


def documents_in_worktree(
    repo: Path, recipe_dir: PurePosixPath
) -> tuple[RecipeDocument, ...]:
    base = repo / Path(*recipe_dir.parts)
    if not base.is_dir():
        raise MigrationError(f"Recipe directory does not exist: {base}")
    documents = []
    for path in sorted(base.rglob("*")):
        if path.is_file():
            relative = PurePosixPath(path.relative_to(repo).as_posix())
            if is_recipe(relative):
                documents.append(parse_recipe(relative, path.read_bytes()))
    return tuple(documents)


def identifier_mapping(
    documents: Iterable[RecipeDocument], old_namespace: str
) -> tuple[dict[str, str], dict[PurePosixPath, tuple[str, str]]]:
    identifiers: dict[str, str] = {}
    identifiers_by_path: dict[PurePosixPath, tuple[str, str]] = {}
    prefix = f"{old_namespace}."
    for document in documents:
        identifier = document.data.get("Identifier")
        if not isinstance(identifier, str):
            raise MigrationError(f"Recipe has no string Identifier: {document.path}")
        if not identifier.startswith(prefix):
            raise MigrationError(
                f"Recipe identifier is outside {old_namespace}: "
                f"{document.path}: {identifier}"
            )
        replacement = f"{NEW_NAMESPACE}{identifier[len(old_namespace) :]}"
        if identifier in identifiers:
            raise MigrationError(f"Duplicate recipe identifier: {identifier}")
        identifiers[identifier] = replacement
        identifiers_by_path[document.path] = (identifier, replacement)
    if not identifiers:
        raise MigrationError("No recipe files were found in the selected directory")
    return identifiers, identifiers_by_path


def commits_at_ref(repo: Path, ref: str, recipe_dir: PurePosixPath) -> tuple[str, ...]:
    output = git(
        repo,
        "log",
        "--reverse",
        "--format=%h %as %s",
        ref,
        "--",
        str(recipe_dir),
    )
    return tuple(line for line in output.splitlines() if line)


def build_plan(
    source_repo: Path,
    source_ref: str,
    recipe_dir: PurePosixPath,
    old_namespace: str,
) -> MigrationPlan:
    files = files_at_ref(source_repo, source_ref, recipe_dir)
    if not files:
        raise MigrationError(f"No tracked files found at {source_ref}:{recipe_dir}")
    documents = documents_at_ref(source_repo, source_ref, files)
    identifiers, identifiers_by_path = identifier_mapping(documents, old_namespace)
    commits = commits_at_ref(source_repo, source_ref, recipe_dir)
    if not commits:
        raise MigrationError(f"No commits found for {source_ref}:{recipe_dir}")
    return MigrationPlan(files, commits, documents, identifiers, identifiers_by_path)


def print_plan(
    plan: MigrationPlan,
    *,
    source_repo: Path,
    source_ref: str,
    target_repo: Path,
    target_ref: str,
    branch: str,
    operation: str,
) -> None:
    print(f"operation: {operation}")
    print(f"source_repo: {source_repo}")
    print(f"source_ref: {source_ref}")
    print(f"target_repo: {target_repo}")
    print(f"target_ref: {target_ref}")
    print(f"branch: {branch}")
    print(f"files: {len(plan.files)}")
    print(f"recipes: {len(plan.documents)}")
    print(f"commits: {len(plan.commits)}")
    print("file_list:")
    for path in plan.files:
        print(f"  {path}")
    print("commit_list:")
    for commit in plan.commits:
        print(f"  {commit}")
    print("identifier_changes:")
    for old_identifier, new_identifier in plan.identifiers.items():
        print(f"  {old_identifier} -> {new_identifier}")


def ref_exists(repo: Path, ref: str) -> bool:
    return bool(git(repo, "rev-parse", "--verify", "--quiet", ref, check=False))


def require_absent_branch(repo: Path, branch: str) -> None:
    if ref_exists(repo, f"refs/heads/{branch}") or ref_exists(
        repo, f"refs/remotes/origin/{branch}"
    ):
        raise MigrationError(
            f"Branch already exists: {branch}\n"
            "Choose a different branch name or inspect the existing migration."
        )


def ensure_target_directory_absent(
    target_repo: Path, target_ref: str, recipe_dir: PurePosixPath
) -> None:
    if files_at_ref(target_repo, target_ref, recipe_dir):
        raise MigrationError(
            f"Target directory already exists at {target_ref}:{recipe_dir}"
        )


def tree_listing(repo: Path, ref: str, recipe_dir: PurePosixPath) -> str:
    return git(repo, "ls-tree", "-r", ref, "--", str(recipe_dir))


def changed_paths(repo: Path) -> tuple[PurePosixPath, ...]:
    output = git(repo, "status", "--porcelain", "-z")
    if not output:
        return ()
    paths = []
    records = output.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        status = record[:2]
        path = record[3:]
        if (status[0] in "RC" or status[1] in "RC") and index < len(records):
            path = records[index]
            index += 1
        paths.append(PurePosixPath(path))
    return tuple(paths)


def require_changes_within(
    repo: Path, recipe_dir: PurePosixPath
) -> tuple[PurePosixPath, ...]:
    paths = changed_paths(repo)
    outside = [
        path for path in paths if recipe_dir not in path.parents and path != recipe_dir
    ]
    if outside:
        rendered = "\n".join(f"  {path}" for path in outside)
        raise MigrationError(
            f"A migration command changed files outside {recipe_dir}:\n{rendered}"
        )
    return paths


def run_pre_commit(
    repo: Path, files: Iterable[PurePosixPath], recipe_dir: PurePosixPath
) -> None:
    if not (repo / ".pre-commit-config.yaml").exists():
        print(f"pre_commit: skipped (no configuration in {repo})")
        return
    file_arguments = [str(path) for path in files]
    if not file_arguments:
        return
    for attempt in range(3):
        before = git(repo, "diff", "--binary")
        result = run(
            ("pre-commit", "run", "--files", *file_arguments),
            cwd=repo,
            check=False,
            capture=False,
        )
        require_changes_within(repo, recipe_dir)
        if result.returncode == 0:
            print("pre_commit: passed")
            return
        after = git(repo, "diff", "--binary")
        if before == after or attempt == 2:
            raise MigrationError(
                "pre-commit failed without a fixable formatting change. "
                f"Run: cd {repo} && pre-commit run --files " + " ".join(file_arguments)
            )
        print("pre_commit: hooks modified files; rerunning")


def commit_directory(repo: Path, recipe_dir: PurePosixPath, message: str) -> None:
    git(repo, "add", "--", str(recipe_dir))
    git(repo, "commit", "-m", message)


def replace_yaml_scalar(text: str, node: Any, replacement: str) -> str:
    start = node.start_mark.index
    end = node.end_mark.index
    original = text[start:end]
    if len(original) >= 2 and original[0] == original[-1] and original[0] in "\"'":
        quote = original[0]
        if quote == '"':
            replacement = replacement.replace("\\", "\\\\").replace('"', '\\"')
        else:
            replacement = replacement.replace("'", "''")
        replacement = f"{quote}{replacement}{quote}"
    return f"{text[:start]}{replacement}{text[end:]}"


def top_level_yaml_nodes(text: str) -> dict[str, Any]:
    yaml_module = require_yaml()
    root = yaml_module.compose(text, Loader=yaml_loader())
    if not isinstance(root, yaml_module.MappingNode):
        raise MigrationError("YAML recipe root is not a mapping")
    return {
        key.value: value
        for key, value in root.value
        if isinstance(key, yaml_module.ScalarNode)
    }


def rewrite_yaml_identifiers(path: Path, identifiers: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    nodes = top_level_yaml_nodes(text)
    replacements: list[tuple[Any, str]] = []
    for key in ("Identifier", "ParentRecipe"):
        node = nodes.get(key)
        if node is not None and node.value in identifiers:
            replacements.append((node, identifiers[node.value]))
    for node, replacement in sorted(
        replacements, key=lambda item: item[0].start_mark.index, reverse=True
    ):
        text = replace_yaml_scalar(text, node, replacement)
    path.write_text(text, encoding="utf-8")


def rewrite_identifiers(
    target_repo: Path,
    recipe_dir: PurePosixPath,
    identifiers: dict[str, str],
) -> None:
    for document in documents_in_worktree(target_repo, recipe_dir):
        path = target_repo / Path(*document.path.parts)
        if document.format == "yaml":
            rewrite_yaml_identifiers(path, identifiers)
            continue
        data = document.data
        identifier = data.get("Identifier")
        if identifier in identifiers:
            data["Identifier"] = identifiers[identifier]
        parent = data.get("ParentRecipe")
        if parent in identifiers:
            data["ParentRecipe"] = identifiers[parent]
        path.write_bytes(plistlib.dumps(data, sort_keys=False))


def validate_target_documents(
    documents: Iterable[RecipeDocument],
    source_documents: Iterable[RecipeDocument],
    identifiers_by_path: dict[PurePosixPath, tuple[str, str]],
    identifiers: dict[str, str],
) -> None:
    by_path = {document.path: document for document in documents}
    source_by_path = {document.path: document for document in source_documents}
    errors = []
    for path, (old_identifier, new_identifier) in identifiers_by_path.items():
        document = by_path.get(path)
        if document is None:
            errors.append(f"missing recipe: {path}")
            continue
        if document.data.get("Identifier") != new_identifier:
            errors.append(
                f"{path}: expected Identifier {new_identifier}, "
                f"found {document.data.get('Identifier')}"
            )
        source_parent = source_by_path[path].data.get("ParentRecipe")
        expected_parent = identifiers.get(source_parent, source_parent)
        if document.data.get("ParentRecipe") != expected_parent:
            errors.append(
                f"{path}: expected ParentRecipe {expected_parent}, "
                f"found {document.data.get('ParentRecipe')}"
            )
        if old_identifier == new_identifier:
            errors.append(f"{path}: identifier did not change")
    if errors:
        raise MigrationError("Target validation failed:\n  " + "\n  ".join(errors))


def warning_message(identifier: str) -> str:
    return f"This recipe is deprecated; use {identifier} instead."


def warning_processor(identifier: str) -> dict[str, Any]:
    return {
        "Arguments": {"message": warning_message(identifier)},
        "Processor": "DeprecationWarning",
    }


def insert_yaml_warning(path: Path, identifier: str) -> None:
    text = path.read_text(encoding="utf-8")
    nodes = top_level_yaml_nodes(text)
    process = nodes.get("Process")
    yaml_module = require_yaml()
    if not isinstance(process, yaml_module.SequenceNode):
        raise MigrationError(f"YAML recipe Process is not a sequence: {path}")
    message = warning_message(identifier).replace("\\", "\\\\").replace('"', '\\"')

    if process.flow_style:
        insertion = (
            f'{{Processor: DeprecationWarning, Arguments: {{message: "{message}"}}}}'
        )
        if process.value:
            insertion += ", "
        index = process.start_mark.index + 1
        text = f"{text[:index]}{insertion}{text[index:]}"
    elif process.value:
        start = process.start_mark.index
        indent = process.start_mark.column
        block = (
            "- Processor: DeprecationWarning\n"
            f"{' ' * (indent + 2)}Arguments:\n"
            f'{" " * (indent + 4)}message: "{message}"\n'
            f"{' ' * indent}"
        )
        text = f"{text[:start]}{block}{text[start:]}"
    else:
        raise MigrationError(f"Unsupported empty block Process sequence: {path}")
    path.write_text(text, encoding="utf-8")


def add_deprecation_warnings(
    source_repo: Path,
    recipe_dir: PurePosixPath,
    identifiers_by_path: dict[PurePosixPath, tuple[str, str]],
) -> None:
    documents = documents_in_worktree(source_repo, recipe_dir)
    by_path = {document.path: document for document in documents}
    for path, (_, new_identifier) in identifiers_by_path.items():
        document = by_path[path]
        process = document.data.get("Process")
        if not isinstance(process, list):
            raise MigrationError(f"Recipe Process is not an array: {path}")
        expected = warning_processor(new_identifier)
        if process and process[0] == expected:
            continue
        if any(
            isinstance(item, dict) and item.get("Processor") == "DeprecationWarning"
            for item in process
        ):
            raise MigrationError(
                f"Recipe already has a different DeprecationWarning: {path}"
            )
        file_path = source_repo / Path(*path.parts)
        if document.format == "yaml":
            insert_yaml_warning(file_path, new_identifier)
        else:
            process.insert(0, expected)
            file_path.write_bytes(plistlib.dumps(document.data, sort_keys=False))


def validate_deprecations(
    documents: Iterable[RecipeDocument],
    source_documents: Iterable[RecipeDocument],
    identifiers_by_path: dict[PurePosixPath, tuple[str, str]],
) -> None:
    by_path = {document.path: document for document in documents}
    source_by_path = {document.path: document for document in source_documents}
    errors = []
    for path, (old_identifier, new_identifier) in identifiers_by_path.items():
        document = by_path.get(path)
        if document is None:
            errors.append(f"missing recipe: {path}")
            continue
        if document.data.get("Identifier") != old_identifier:
            errors.append(f"{path}: original Identifier changed")
        expected_parent = source_by_path[path].data.get("ParentRecipe")
        if document.data.get("ParentRecipe") != expected_parent:
            errors.append(f"{path}: original ParentRecipe changed")
        process = document.data.get("Process")
        expected = warning_processor(new_identifier)
        if not isinstance(process, list) or not process or process[0] != expected:
            errors.append(
                f"{path}: first processor is not the expected deprecation warning"
            )
    if errors:
        raise MigrationError("Deprecation validation failed:\n  " + "\n  ".join(errors))


def fetch_repositories(source_repo: Path, target_repo: Path) -> None:
    print("fetch: source origin")
    git(source_repo, "fetch", "origin")
    print("fetch: target origin and upstream")
    git(target_repo, "fetch", "origin")
    git(target_repo, "fetch", "upstream")


def apply_import(args: argparse.Namespace, plan: MigrationPlan) -> None:
    source_repo: Path = args.source_repo
    target_repo: Path = args.target_repo
    recipe_dir: PurePosixPath = args.recipe_dir
    source_ref = f"origin/{args.source_branch}"
    target_ref = f"upstream/{args.target_branch}"
    branch = args.branch or f"migrate-{branch_slug(recipe_dir)}"

    require_absent_branch(target_repo, branch)
    ensure_target_directory_absent(target_repo, target_ref, recipe_dir)
    git(target_repo, "switch", "-c", branch, target_ref)

    with tempfile.TemporaryDirectory(prefix="recipe-migration-") as temporary:
        output = git(
            source_repo,
            "format-patch",
            "--root",
            "--binary",
            "--output-directory",
            temporary,
            source_ref,
            "--",
            str(recipe_dir),
        )
        patches = tuple(line for line in output.splitlines() if line)
        if not patches:
            raise MigrationError("format-patch produced no patches")
        result = run(
            (
                "git",
                "-C",
                str(target_repo),
                "am",
                "--3way",
                "--committer-date-is-author-date",
                *patches,
            ),
            check=False,
            capture=False,
        )
        if result.returncode:
            raise MigrationError(
                "git am stopped during the import. Resolve the conflict and run "
                f"'git -C {target_repo} am --continue', or abort with "
                f"'git -C {target_repo} am --abort'."
            )

    if tree_listing(source_repo, source_ref, recipe_dir) != tree_listing(
        target_repo, "HEAD", recipe_dir
    ):
        raise MigrationError(
            "Imported directory does not exactly match the source tree"
        )
    print("tree_comparison: exact match")

    migrated_files = files_at_ref(target_repo, "HEAD", recipe_dir)
    run_pre_commit(target_repo, migrated_files, recipe_dir)
    if changed_paths(target_repo):
        commit_directory(
            target_repo,
            recipe_dir,
            f"Normalize imported {recipe_dir.name} files",
        )

    rewrite_identifiers(target_repo, recipe_dir, plan.identifiers)
    target_documents = documents_in_worktree(target_repo, recipe_dir)
    validate_target_documents(
        target_documents,
        plan.documents,
        plan.identifiers_by_path,
        plan.identifiers,
    )
    run_pre_commit(target_repo, migrated_files, recipe_dir)
    require_changes_within(target_repo, recipe_dir)
    git(target_repo, "diff", "--check")
    if changed_paths(target_repo):
        commit_directory(
            target_repo,
            recipe_dir,
            f"Use HRT identifiers for {recipe_dir.name} recipes",
        )

    print("result: target migration prepared")
    print(f"branch: {branch}")
    print(f"next: git -C {target_repo} log --stat {target_ref}..HEAD")
    print(f"next: git -C {target_repo} push -u origin {branch}")


def import_command(args: argparse.Namespace) -> None:
    validate_remotes(args.source_repo, args.target_repo)
    require_clean(args.source_repo, "Source")
    require_clean(args.target_repo, "Target")
    if args.apply:
        fetch_repositories(args.source_repo, args.target_repo)

    source_ref = f"origin/{args.source_branch}"
    target_ref = f"upstream/{args.target_branch}"
    branch = args.branch or f"migrate-{branch_slug(args.recipe_dir)}"
    if not ref_exists(args.source_repo, source_ref):
        raise MigrationError(f"Source ref does not exist: {source_ref}")
    if not ref_exists(args.target_repo, target_ref):
        raise MigrationError(f"Target ref does not exist: {target_ref}")
    ensure_target_directory_absent(args.target_repo, target_ref, args.recipe_dir)
    plan = build_plan(args.source_repo, source_ref, args.recipe_dir, args.old_namespace)
    print_plan(
        plan,
        source_repo=args.source_repo,
        source_ref=source_ref,
        target_repo=args.target_repo,
        target_ref=target_ref,
        branch=branch,
        operation="import",
    )
    if not args.apply:
        print("result: no changes (rerun with --apply)")
        return
    apply_import(args, plan)


def confirm_target_migration(
    target_repo: Path,
    target_ref: str,
    recipe_dir: PurePosixPath,
    plan: MigrationPlan,
) -> None:
    target_files = files_at_ref(target_repo, target_ref, recipe_dir)
    if not target_files:
        raise MigrationError(
            f"Replacement directory is not present at {target_ref}:{recipe_dir}. "
            "Merge the target pull request first."
        )
    target_documents = documents_at_ref(target_repo, target_ref, target_files)
    validate_target_documents(
        target_documents,
        plan.documents,
        plan.identifiers_by_path,
        plan.identifiers,
    )


def deprecate_command(args: argparse.Namespace) -> None:
    validate_remotes(args.source_repo, args.target_repo)
    require_clean(args.source_repo, "Source")
    require_clean(args.target_repo, "Target")
    if args.apply:
        fetch_repositories(args.source_repo, args.target_repo)

    source_ref = f"origin/{args.source_branch}"
    target_ref = f"upstream/{args.target_branch}"
    branch = args.branch or f"deprecate-{branch_slug(args.recipe_dir)}"
    plan = build_plan(args.source_repo, source_ref, args.recipe_dir, args.old_namespace)
    confirm_target_migration(args.target_repo, target_ref, args.recipe_dir, plan)
    print_plan(
        plan,
        source_repo=args.source_repo,
        source_ref=source_ref,
        target_repo=args.target_repo,
        target_ref=target_ref,
        branch=branch,
        operation="deprecate",
    )
    if not args.apply:
        print("result: no changes (rerun with --apply)")
        return

    require_absent_branch(args.source_repo, branch)
    git(args.source_repo, "switch", "-c", branch, source_ref)
    add_deprecation_warnings(
        args.source_repo, args.recipe_dir, plan.identifiers_by_path
    )
    source_documents = documents_in_worktree(args.source_repo, args.recipe_dir)
    validate_deprecations(source_documents, plan.documents, plan.identifiers_by_path)
    source_files = tuple(document.path for document in source_documents)
    run_pre_commit(args.source_repo, source_files, args.recipe_dir)
    validate_deprecations(
        documents_in_worktree(args.source_repo, args.recipe_dir),
        plan.documents,
        plan.identifiers_by_path,
    )
    require_changes_within(args.source_repo, args.recipe_dir)
    git(args.source_repo, "diff", "--check")
    if changed_paths(args.source_repo):
        commit_directory(
            args.source_repo,
            args.recipe_dir,
            f"Deprecate {args.recipe_dir.name} recipes",
        )
    print("result: source deprecation prepared")
    print(f"branch: {branch}")
    print(f"next: git -C {args.source_repo} log --stat {source_ref}..HEAD")
    print(f"next: git -C {args.source_repo} push -u origin {branch}")


def validate_command(args: argparse.Namespace) -> None:
    validate_remotes(args.source_repo, args.target_repo)
    source_ref = f"origin/{args.source_branch}"
    plan = build_plan(args.source_repo, source_ref, args.recipe_dir, args.old_namespace)
    if args.stage == "target":
        validate_target_documents(
            documents_in_worktree(args.target_repo, args.recipe_dir),
            plan.documents,
            plan.identifiers_by_path,
            plan.identifiers,
        )
        print("result: target migration valid")
        return
    validate_deprecations(
        documents_in_worktree(args.source_repo, args.recipe_dir),
        plan.documents,
        plan.identifiers_by_path,
    )
    target_ref = f"upstream/{args.target_branch}"
    confirm_target_migration(args.target_repo, target_ref, args.recipe_dir, plan)
    print("result: source deprecation valid")


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-repo",
        required=True,
        type=Path,
        help="source AutoPkg recipe repository",
    )
    parser.add_argument(
        "--target-repo",
        type=Path,
        default=repository_root_from_script(),
        help=(
            "hudson-trading-recipes clone (default: repository containing this script)"
        ),
    )
    parser.add_argument(
        "--recipe-dir",
        required=True,
        type=recipe_directory,
        help="repository-relative directory to migrate",
    )
    parser.add_argument(
        "--old-namespace",
        required=True,
        help="source identifier namespace, for example com.github.ccaviness",
    )
    parser.add_argument("--source-branch", default="main")
    parser.add_argument("--target-branch", default="main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and validate AutoPkg recipe migrations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser(
        "import",
        help="import history and update identifiers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s --source-repo /path/to/source-recipes \\
    --recipe-dir Cyberhaven --old-namespace com.github.ccaviness

  %(prog)s --source-repo /path/to/source-recipes \\
    --recipe-dir 'Okta Verify' --old-namespace com.github.owner --apply
""",
    )
    add_common_arguments(import_parser)
    import_parser.add_argument("--branch", help="target migration branch name")
    import_parser.add_argument(
        "--apply",
        action="store_true",
        help="create the branch and commits; otherwise only print the plan",
    )
    import_parser.set_defaults(function=import_command)

    deprecate_parser = subparsers.add_parser(
        "deprecate",
        help="deprecate source recipes after the target migration merges",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s --source-repo /path/to/source-recipes \\
    --recipe-dir Cyberhaven --old-namespace com.github.ccaviness

  %(prog)s --source-repo /path/to/source-recipes \\
    --recipe-dir Cyberhaven --old-namespace com.github.ccaviness --apply
""",
    )
    add_common_arguments(deprecate_parser)
    deprecate_parser.add_argument("--branch", help="source deprecation branch name")
    deprecate_parser.add_argument(
        "--apply",
        action="store_true",
        help="create the branch and commit; otherwise only print the plan",
    )
    deprecate_parser.set_defaults(function=deprecate_command)

    validate_parser = subparsers.add_parser(
        "validate",
        help="validate an imported or deprecated directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s --stage target --source-repo /path/to/source-recipes \\
    --recipe-dir Cyberhaven --old-namespace com.github.ccaviness

  %(prog)s --stage deprecation --source-repo /path/to/source-recipes \\
    --recipe-dir Cyberhaven --old-namespace com.github.ccaviness
""",
    )
    add_common_arguments(validate_parser)
    validate_parser.add_argument(
        "--stage", choices=("target", "deprecation"), required=True
    )
    validate_parser.set_defaults(function=validate_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.source_repo = require_repository(args.source_repo, "Source")
        args.target_repo = require_repository(args.target_repo, "Target")
        args.function(args)
    except MigrationError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
