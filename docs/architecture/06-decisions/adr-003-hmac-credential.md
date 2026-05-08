# ADR-003 — Chief Credential 走 HMAC-SHA256 签名（Eigenflux ID 不参与授权）

- **状态**：Accepted（2026-05-06）
- **决策者**：Chief 工程团队
- **相关文档**：design.md §4，威胁模型 T1/T2/T3

## Context

Eigenflux 不提供任何密码学认证（R.Q1）—— Agent ID 是公开标识，知道 ID 就能"声称是这个 agent"。如果 Chief 用 Eigenflux ID 做授权，**任何知道 ID 的攻击者就能花对应钱包的钱**。这是不可接受的 v1 安全洞。

## Decision

**Eigenflux Agent ID 仅用于识别 / 路由，不用于授权**。Chief 在 binding 创建时颁发自己的 (key_id, secret) 凭证，所有 agent → Chief 的请求用 **HMAC-SHA256 签名**。

### 签名格式

```
X-Chief-Key-Id:    <key_id>
X-Chief-Timestamp: <unix_ms>
X-Chief-Nonce:     <16 bytes hex>
X-Chief-Signature: hmac_sha256(secret, signing_string)

signing_string = METHOD || '\n' ||
                 PATH || '\n' ||
                 X-Chief-Timestamp || '\n' ||
                 X-Chief-Nonce || '\n' ||
                 sha256(BODY)
```

### 校验链

1. Timestamp 偏差 ≤ 5 分钟
2. Nonce 在 Redis 5 分钟内未出现过
3. Argon2id verify(secret, secret_hash) 通过
4. Key 未 expire / revoke
5. Scope 包含本接口需要的 scope
6. Owner 未 disable

任一不通过即拒绝。

### 凭证下发

仅通过 OAuth device-code flow（详见 ADR-004）。Secret 在响应中明文出现一次后立即清出内存；DB 仅存 argon2id hash。

## Consequences

### 正向

- Eigenflux ID 是公开还是私有不影响安全
- 标准 HMAC 流，不引入复杂 PKI / 证书链
- 短 TTL（90 天） + 旋转 + revoke 都简单
- 有 audit log 可追到每一笔授权

### 负向

- Plugin 必须妥善保存 secret（OS keychain，不是文件）
- Secret 一旦泄露需立即 revoke + 强制旋转所有相关 binding
- 比 mTLS 弱一个台阶（无证书链信任）—— 但 v1 不需要

## 备选

- **JWT** —— Eigenflux 不签发，我们自签的话和 HMAC 等价但增加 JWT 复杂度
- **mTLS** —— 客户端证书分发 / 旋转 UX 在 OpenClaw plugin 里很难做好
- **Eigenflux 加密码学认证后切换** —— 等 Eigenflux 上密码学时可以叠加为第二因子，但不阻塞 v1
