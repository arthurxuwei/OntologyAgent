# README Worktree Compose Env Design

## Summary

This spec adds a focused README clarification for one concrete operational pitfall:

when `docker compose` is launched from a git worktree directory, Compose reads `.env` from the current working directory rather than automatically reading the main repository root `.env`.

In this repository, that can silently drop critical values such as `PRIVATE_KEY` and `OPENAI_API_KEY`, leading to confusing runtime failures that do not occur when running from the main checkout.

The goal is not to change the runtime system. The goal is to document the correct command so future worktree-based runs behave consistently.

## Current Context

The current `README.md` already documents:

- the docker compose startup command
- key environment variables
- the role of `PRIVATE_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and related settings

What is currently missing is an explicit note that Compose resolves `.env` relative to the current working directory. When working from `.worktrees/...`, users can reasonably assume the root `.env` still applies, but that is not how Compose behaves.

This has already caused a real failure mode:

- `PRIVATE_KEY` present in the repository root `.env`
- `docker compose` run from a worktree directory
- `PRIVATE_KEY` resolved as empty inside the `chain` container
- signer shown as unconfigured
- autonomy and on-chain flows behave differently than expected

## Scope

### Included

- add a short `README` note for worktree users
- explain why `.env` resolution changes in worktree directories
- point out the main affected variables
- provide a repository-safe recommended command using `git rev-parse --show-toplevel`

### Excluded

- changes to `docker-compose.yml`
- wrapper scripts or helper commands
- multiple `.env` management schemes
- shell aliases or automation

## Design Goals

- make the pitfall visible at the point where users are already reading about compose and environment variables
- keep the note short and operationally useful
- give a command that works both from the main checkout and from a worktree
- prevent silent configuration drift between the main checkout and `.worktrees/...`

## Recommended Approach

Three documentation approaches were considered:

1. add a short `Worktree 注意事项` section in `README`
2. replace all compose examples with explicit `--env-file`
3. write a separate operational doc

The recommended approach is the short `Worktree 注意事项` section.

It is focused, low-noise, and easy to find near the existing compose and environment variable guidance without overcomplicating the README.

## Placement

The new section should appear near the existing compose and environment-variable guidance, specifically around the `README` area that already explains runtime configuration.

Recommended placement:

- after the basic compose startup section or near `## 关键环境变量`

The section title should be explicit and easy to scan:

- `## Worktree 注意事项`

## Content Structure

The section should have four short parts.

### 1. Problem Statement

Explain that when `docker compose` is run from `.worktrees/...`, Compose does not automatically use the repository root `.env`.

### 2. Impact

Explain the concrete risk:

- `PRIVATE_KEY` may be empty
- `OPENAI_API_KEY` may be empty
- `OPENAI_BASE_URL` may be empty

This can cause:

- unconfigured signer state
- model configuration failures
- behavior differences between the main checkout and worktree runs

### 3. Recommended Command

The primary recommended command should be:

```bash
docker compose --env-file "$(git rev-parse --show-toplevel)/.env" up -d
```

This should be the main recommendation because it works from both:

- the main repository checkout
- any git worktree created from the same repository

### 4. Practical Explanation

Add one short sentence explaining why this command is preferred:

- it explicitly binds Compose to the repository root `.env`
- it prevents missing runtime variables when launched from a worktree directory

## Wording Style

The wording should be practical and direct rather than tutorial-heavy.

Good style:

- describe the failure mode plainly
- name the affected variables explicitly
- show one recommended command

Avoid:

- long explanations of Docker internals
- abstract warnings without a copy-paste command
- multiple competing command variants in the main note

## Acceptance Criteria

This README update is complete when all of the following are true:

1. `README.md` contains a dedicated worktree warning section
2. the section explicitly says Compose resolves `.env` from the current directory
3. the section names the main affected variables such as `PRIVATE_KEY`, `OPENAI_API_KEY`, and `OPENAI_BASE_URL`
4. the section includes the recommended command:

```bash
docker compose --env-file "$(git rev-parse --show-toplevel)/.env" up -d
```

5. the note is short enough to be practical and visible rather than hidden in a long wall of text

## Final Recommendation

Do not change runtime behavior for this issue yet.

Document the correct operational command first.

This is a proven real-world pitfall, and the highest-value fix is a README note that makes the correct `docker compose --env-file ...` invocation obvious to anyone launching services from a worktree.
