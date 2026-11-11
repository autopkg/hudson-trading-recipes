# Migrating recipes

This guide moves an HRT-owned recipe directory from another AutoPkg recipe
repository into `autopkg/hudson-trading-recipes`. The commands retain the
commits that changed the directory, then give the copied recipes HRT
identifiers. A directory may contain one or more recipes, custom processors,
scripts, icons, and other supporting files.

## Before starting

- Confirm that the recipes were created for HRT and that their copyright and
  license permit the move.
- Review the directory's complete Git history, not only its current files.
  Earlier versions will become public, so check them for credentials, internal
  URLs, customer names, and other private information.
- Identify every recipe, custom processor, icon, and parent relationship that
  must move together. Confirm that every file being migrated is under the
  selected directory. Handle shared files outside the directory separately.
- Commit all intended source changes. The history import only includes
  committed changes.
- Start with clean source and destination working trees.
- These commands assume that the destination repository uses `upstream` for
  `autopkg/hudson-trading-recipes` and `origin` for the contributor's fork.
  They assume that the source repository uses `origin` for its GitHub remote.
  Check both repositories with `git remote -v` before starting.

The destination clone should use this remote and branch layout:

```text
origin    https://github.com/<contributor>/hudson-trading-recipes
upstream  https://github.com/autopkg/hudson-trading-recipes
main      tracks origin/main
```

Disable pushes to `upstream` and make `origin` the default push remote:

```bash
git remote set-url --push upstream DISABLED
git config remote.pushDefault origin
git branch --set-upstream-to=origin/main main
```

## Automated workflow

`scripts/recipe_migration.py` automates the history import, identifier changes,
validation, and source deprecation. It supports XML plist and YAML recipes. It
also migrates every supporting file in the selected directory.

The script uses PyYAML when it encounters YAML recipes. AutoPkg environments
normally provide it. If the active Python environment does not, install the
script requirement into that environment:

```bash
python3 -m pip install -r scripts/requirements.txt
```

Run `import` without `--apply` first. The command checks both repositories,
fetches their remotes, and prints the files, commits, branch, and identifier
changes. It does not change either worktree or create commits:

```bash
python3 scripts/recipe_migration.py import \
    --source-repo /path/to/source-recipes \
    --recipe-dir RecipeName
```

The script normally infers the old namespace from each recipe's filename and
`Identifier`. Every recipe in the directory must produce the same namespace.
For nonstandard or ambiguous identifiers, provide it explicitly with
`--old-namespace com.github.source-owner`.

Review the plan and the source history. Then rerun it with `--apply`:

```bash
python3 scripts/recipe_migration.py import \
    --source-repo /path/to/source-recipes \
    --recipe-dir RecipeName \
    --apply
```

The command creates `migrate-recipe-name` from `upstream/main`, imports the
directory history, compares the imported tree with the source, runs pre-commit
against the migrated files, and creates separate normalization and identifier
commits when needed. It stops without pushing and prints the review and push
commands.

After the target pull request merges, plan the source deprecation. The command
fetches `upstream` before confirming that the replacement recipes exist:

```bash
python3 scripts/recipe_migration.py deprecate \
    --source-repo /path/to/source-recipes \
    --recipe-dir RecipeName
```

The deprecation command confirms that every replacement identifier exists on
`upstream/main`. Rerun it with `--apply` to create the source branch, add the
warnings, run pre-commit, validate the exact replacement identifiers, and
commit the changes. It does not push.

Run either validation independently when reviewing an interrupted or manually
edited migration:

```bash
python3 scripts/recipe_migration.py validate \
    --stage target \
    --source-repo /path/to/source-recipes \
    --recipe-dir RecipeName

python3 scripts/recipe_migration.py validate \
    --stage deprecation \
    --source-repo /path/to/source-recipes \
    --recipe-dir RecipeName
```

Use the manual procedure below when resolving unusual history, shared files,
submodules, Git LFS content, or merge-only changes.

## Manual workflow

### 1. Import the directory and its history

Replace these placeholder values for each migration. Create a temporary
directory for the generated patch series:

```bash
source_repo=/path/to/source-recipes
target_repo=/path/to/hudson-trading-recipes
source_branch=main
target_branch=main
recipe_dir=RecipeName
recipe_file=RecipeName.download.recipe  # One file used to inspect history.
old_namespace=com.github.source-owner
migration_branch=migrate-recipe-name
deprecation_branch=deprecate-recipe-name
migration_work=$(mktemp -d)

git -C "$source_repo" status --short --branch
git -C "$target_repo" status --short --branch
git -C "$source_repo" remote -v
git -C "$target_repo" remote -v
git -C "$source_repo" ls-files -- "$recipe_dir"
git -C "$source_repo" log --oneline -- "$recipe_dir"
test ! -e "$target_repo/$recipe_dir"
```

Update the source branch, then create a destination branch from the current
upstream branch:

```bash
git -C "$source_repo" fetch origin
git -C "$source_repo" switch "$source_branch"
git -C "$source_repo" merge --ff-only "origin/$source_branch"

git -C "$target_repo" fetch upstream
git -C "$target_repo" switch "$target_branch"
git -C "$target_repo" merge --ff-only "upstream/$target_branch"
git -C "$target_repo" switch -c "$migration_branch"

(
    cd "$target_repo"
    pre-commit install
)
```

Installing the hook is required once per clone. The
`.pre-commit-config.yaml` file defines the checks but does not install the Git
hook.

Export only the commits and changes affecting the recipe directory, then replay
them in the destination repository:

```bash
git -C "$source_repo" format-patch \
    --root \
    --binary \
    --output-directory "$migration_work" \
    -- "$recipe_dir"

git -C "$target_repo" am \
    --3way \
    --committer-date-is-author-date \
    "$migration_work"/*.patch
```

The `recipe_dir` pathspec is recursive. It includes every tracked file under
the directory, including multiple recipes, processors, scripts, symlinks, and
binary icons. `--binary` includes binary file changes, and the patches retain
Git file modes such as the executable bit.

`format-patch` records the author name, author email, author date, commit
message, and binary changes for each matching commit. It does not record the
original committer date. `--committer-date-is-author-date` prevents the replayed
commits from appearing to have been created on the migration date. The exact
original committer identity and date are not retained when they differ from
the author metadata. Commit hashes also change because the commits have new
parents. Limiting the patches to `recipe_dir` keeps unrelated
source-repository history out of the destination.

If a patch conflicts, resolve it and run
`git -C "$target_repo" am --continue`. Run
`git -C "$target_repo" am --abort` to abandon the import.

Check the result before making migration-specific edits:

```bash
git -C "$target_repo" status --short
git -C "$target_repo" log --oneline -- "$recipe_dir"
git -C "$target_repo" log --follow -- "$recipe_dir/$recipe_file"
git -C "$target_repo" diff --stat "upstream/$target_branch...HEAD"
diff \
    <(git -C "$source_repo" ls-tree -r "$source_branch" -- "$recipe_dir") \
    <(git -C "$target_repo" ls-tree -r HEAD -- "$recipe_dir")
```

The final `diff` compares each path, object type, file mode, and blob hash
before migration-specific edits. No output means that the imported directory
matches the source branch.

Git does not track empty directories, and this procedure does not include
untracked files or files outside `recipe_dir`. Git LFS objects and submodules
need additional migration steps. If the directory or a file within it was
moved from another path, include each old path in the export. Do not use this
procedure when important changes exist only in merge commits because
`format-patch` does not reproduce those merges.

### 2. Check and normalize the imported files

`git am` does not run the normal pre-commit hook. Run the repository checks
before changing identifiers, but skip `check-autopkg-recipes` during this
phase. That hook requires the new HRT namespace, which the imported files do
not have yet:

```bash
(
    cd "$target_repo"
    SKIP=check-autopkg-recipes pre-commit run --all-files
)
```

A formatting hook may modify files and return a failure on the first run.
Review its changes, then rerun the checks until they pass:

```bash
git -C "$target_repo" status --short
git -C "$target_repo" diff

(
    cd "$target_repo"
    SKIP=check-autopkg-recipes pre-commit run --all-files
)
```

If the hooks changed imported files, commit those changes separately from the
identifier changes:

```bash
git -C "$target_repo" add "$recipe_dir"
git -C "$target_repo" commit -m "Normalize imported $recipe_dir files"
```

### 3. Change identifiers in the migrated copy

Change identifiers in a new commit after importing the history. Change each
HRT-owned identifier from, for example:

```text
com.github.source-owner.download.RecipeName
com.github.source-owner.munki.RecipeName
```

to:

```text
com.github.hudson-trading.download.RecipeName
com.github.hudson-trading.munki.RecipeName
```

Update HRT-owned `ParentRecipe` references and any other references to the old
identifiers. Do not rename third-party parent identifiers. Search the directory
for the old namespace before committing:

```bash
rg -n --fixed-strings "$old_namespace" "$target_repo/$recipe_dir"
git -C "$target_repo" diff
git -C "$target_repo" add "$recipe_dir"
git -C "$target_repo" commit -m "Use HRT identifiers for $recipe_dir recipes"
```

### 4. Validate and publish the migrated recipes

Run the repository checks and review the branch:

```bash
(
    cd "$target_repo"
    pre-commit run --all-files
)
git -C "$target_repo" status --short
git -C "$target_repo" diff --check "upstream/$target_branch...HEAD"
git -C "$target_repo" log --stat "upstream/$target_branch..HEAD"
git -C "$target_repo" push -u origin "$migration_branch"
```

On macOS, run `plutil -lint` against every migrated plist recipe. Test the
recipes from the feature branch on the AutoPkg test system. Store required
credentials outside the repository, for example in AutoPkg preferences. Do
not commit them or pass them on the command line. Lint and pre-commit checks do
not test downloads, signatures, custom processors, or Munki imports.

Merge the destination pull request before deprecating the old recipes. The
warning will then name a replacement that users can fetch.

### 5. Deprecate the originals

Create a separate branch in the source repository. Add `DeprecationWarning` as
the first processor in each old recipe, with a message naming its exact
replacement:

```bash
git -C "$source_repo" fetch origin
git -C "$source_repo" switch "$source_branch"
git -C "$source_repo" merge --ff-only "origin/$source_branch"
git -C "$source_repo" switch -c "$deprecation_branch"

(
    cd "$source_repo"
    pre-commit install
)
```

```xml
<dict>
    <key>Arguments</key>
    <dict>
        <key>message</key>
        <string>This recipe is deprecated; use com.github.hudson-trading.download.RecipeName instead.</string>
    </dict>
    <key>Processor</key>
    <string>DeprecationWarning</string>
</dict>
```

Use the exact replacement identifier in each recipe. A deprecated Munki recipe,
for example, should name its corresponding
`com.github.hudson-trading.munki.RecipeName` identifier. Commit and push this
on a separate source-repository branch:

```bash
git -C "$source_repo" diff --check
git -C "$source_repo" add "$recipe_dir"

(
    cd "$source_repo"
    pre-commit run
)

git -C "$source_repo" status --short
git -C "$source_repo" diff
git -C "$source_repo" add "$recipe_dir"
git -C "$source_repo" commit -m "Deprecate $recipe_dir recipes"
git -C "$source_repo" push -u origin "$deprecation_branch"
```

### 6. Update downstream consumers

After the migration is merged:

1. Add `autopkg/hudson-trading-recipes` to the consumer's repository list.
2. Change overrides to use the new parent identifiers.
3. Review the complete parent and custom-processor diff.
4. Regenerate trust information only after that review.
5. Run a focused test on the macOS AutoPkg test system before deploying the
   change.

Keep the source repository available while other recipes or downstream
consumers still depend on it.

## Recipe format

Do not combine plist-to-YAML conversion with the initial migration. If the
project adopts YAML, convert the recipes in a separate commit or pull request.
Reviewers can then distinguish the identifier changes from the format changes.
