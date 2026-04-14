# README Worktree Compose Env Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a short README note that explains why `docker compose` launched from a git worktree can miss the repository root `.env`, and show the correct `--env-file` command.

**Architecture:** Keep this change documentation-only. Update `README.md` near the existing compose and environment-variable sections with a short `Worktree 注意事项` note that explains the `.env` resolution pitfall, names the main affected variables, and recommends a single copy-paste-safe command using `git rev-parse --show-toplevel`.

**Tech Stack:** Markdown, README documentation, pytest or simple content assertions only if the repository already has a doc-testing pattern; otherwise manual verification through file review

---

## File Map

- Modify: `README.md`
  - Add a `## Worktree 注意事项` section near the compose/environment guidance
- No test files expected unless a lightweight doc assertion is already present in the repo pattern

## Task 1: Add The Worktree Compose Env Note

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the failing documentation expectation as a checklist in your working notes**

Use this checklist before editing:

```text
- README contains a dedicated `## Worktree 注意事项` section
- It explains that docker compose resolves `.env` from the current working directory
- It explicitly names PRIVATE_KEY, OPENAI_API_KEY, and OPENAI_BASE_URL as affected examples
- It includes: docker compose --env-file "$(git rev-parse --show-toplevel)/.env" up -d
```

The failing condition is that the current README does not contain that section or command.

- [ ] **Step 2: Verify the README currently lacks the dedicated note**

Run: `rg -n "Worktree 注意事项|--env-file" README.md`
Expected: no `Worktree 注意事项` section and no recommended `--env-file` command in README

- [ ] **Step 3: Add the note to README.md**

Insert a new section near the existing compose/environment guidance with content like this:

```md
## Worktree 注意事项

如果你是在 `.worktrees/...` 目录里执行 `docker compose`，Compose 默认会读取**当前目录**下的 `.env`，不会自动回退到主仓库根目录的 `.env`。

这会导致一些关键变量在容器里变成空值，例如：

- `PRIVATE_KEY`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

进一步可能出现：

- `chain` signer 未配置
- `agent` 模型配置缺失
- 主工作区和 worktree 启动行为不一致

推荐在 worktree 中显式指定主仓库根目录的 `.env`：

```bash
docker compose --env-file "$(git rev-parse --show-toplevel)/.env" up -d
```

这样可以确保 Compose 使用当前仓库根目录的环境变量，而不是 worktree 目录下不存在或不完整的 `.env`。
```

- [ ] **Step 4: Verify the README now contains the required guidance**

Run: `rg -n "Worktree 注意事项|PRIVATE_KEY|OPENAI_API_KEY|OPENAI_BASE_URL|--env-file" README.md`
Expected: matches for the section title, affected variables, and the recommended command

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add worktree compose env note"
```

## Task 2: Final Verification

**Files:**
- Modify: none expected unless the README wording needs tightening

- [ ] **Step 1: Re-read the new README section in context**

Read the `README.md` section around `## 同时启动`, the new `## Worktree 注意事项`, and `## 关键环境变量`.

Expected:

- the new section is easy to find
- it does not repeat the whole environment variable reference
- it clearly explains the pitfall and the fix in under one screenful of text

- [ ] **Step 2: Confirm the recommended command is repository-safe**

Run: `git rev-parse --show-toplevel`
Expected: prints the repository root, showing that the documented command will resolve the root `.env` from both the main checkout and a worktree

- [ ] **Step 3: Inspect git status for intended files only**

Run: `git status --short`
Expected: only `README.md` is modified if no extra fixes were needed

- [ ] **Step 4: Commit verification-only wording fixes if needed**

```bash
git add README.md
git commit -m "docs: refine worktree compose env guidance"
```

## Self-Review

### Spec Coverage

- dedicated worktree warning section: Task 1
- explanation of current-directory `.env` resolution: Task 1
- affected variable examples: Task 1
- recommended `--env-file` command: Task 1
- concise placement near compose/env documentation: Task 2

No spec requirement is left without an implementation step.

### Placeholder Scan

- no `TODO` or `TBD`
- exact file path is named
- exact commands and expected outcomes are included

### Type Consistency

- the plan consistently uses the section title `## Worktree 注意事项`
- the recommended command consistently uses `$(git rev-parse --show-toplevel)/.env`
