# ADR-004 — v1 唯一目标：OpenClaw + OAuth device-code flow

- **状态**：Accepted（2026-05-06）
- **决策者**：Chief 工程团队
- **相关文档**：design.md §6.1

## Context

v1 plugin / skill 受众原本设想覆盖多 agent 客户端（Claude Code / Cursor / Cline / OpenAI Assistants / LangChain / Vercel AI SDK / 自研 stack）。3 人 × 3.5 月窗口下，**广覆盖等于薄交付**。同时 v1 用户实际是 OpenClaw 终端用户，不是写 agent 代码的开发者。

## Decision

### 范围收窄

**v1 唯一目标 = OpenClaw plugin**，不交付其他客户端的官方支持。

| 客户端 | v1 状态 |
|---|---|
| OpenClaw（MCP server + native plugin 双格式） | **P0** |
| 其他 MCP 客户端（Cursor / Cline / Windsurf 等） | 不在 v1 范围；REST 不主动屏蔽，但不交付示例 / 不承诺契约稳定 |
| OpenAI Assistants / LangChain / Vercel / 自研 stack | 同上 |

### 分发渠道

**GitHub repo**。用户运行：
```
openclaw plugin install <github-repo>
```

Eigenflux 可推 webhook 给用户的 OpenClaw 实例**建议**安装，最终是否安装由用户在 OpenClaw 内确认。v1 不强制注册到 Eigenflux 网络目录。

### 凭证下发：OAuth device-code flow

1. 用户在 OpenClaw 内运行 `openclaw plugin agent-wallet login`
2. Plugin 调 `POST /v1/oauth/device/authorize` → 返回 `device_code` + `user_code` + `verification_uri`
3. Plugin 在终端显示 `user_code` + URL，开始轮询 `POST /v1/oauth/device/token`
4. 用户在浏览器打开 verification_uri，登录 GitHub OAuth（Owner 身份）
5. Console 提示输入 `user_code`，选 binding + scope，确认
6. Plugin 轮询拿到 `(key_id, secret)`，存进 OS keychain

## Consequences

### 正向

- 受众清晰，不浪费精力做没人用的 adapter
- 凭证从不经手 owner 复制粘贴，UX 好 + 安全好
- Plugin 双格式打包保留 v2 扩展可能（未来想支持 Cursor 直接装 MCP server，零改动）

### 负向

- 失去 v1 阶段对其他客户端的早期反馈
- 钓鱼新攻击面（T9）：attacker 偷 `user_code` 引诱 owner 确认 → 缓解：
  - `user_code` 短 TTL（≤ 10min）
  - 同 owner 并发 device flow ≤ 3
  - Console 显式展示"你正在为哪个 binding 授权 + scope 列表"，让 owner 主动核对

## 备选

- **多客户端薄覆盖** —— 否决：3 人小队 5 个客户端 = 每个都做不深
- **Bearer token + 用户复制粘贴** —— UX 差且 secret 容易写到 plain text 配置文件
- **强制注册到 Eigenflux 网络目录** —— Eigenflux 团队建议 v1 不必，跟着走
