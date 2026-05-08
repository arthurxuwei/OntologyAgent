# ADR-005 — v1 Onramp = Coinbase Onramp

- **状态**：Accepted（2026-05-06）
- **决策者**：Chief 工程团队
- **相关文档**：design.md §5.2，brainstorm §3

## Context

v1 需要法币入金通道（保留原型 M3 故事 + 让邀请制 dev 真信用卡能跑通端到端）。三个候选：

- MoonPay
- Stripe Crypto
- Coinbase Onramp

法币提现明确推 v2，v1 提现仅链上 BYO。

## Decision

**采用 Coinbase Onramp**（[docs.cdp.coinbase.com/onramp](https://docs.cdp.coinbase.com/onramp/)）。

### 集成形态

- **托管 widget**：Coinbase 提供前端 SDK，Owner Console 嵌入
- **Session API**：后端通过 `POST /v1/onramp/sessions` 创建，返回 widget URL
- **资金路径**：用户付款 → Coinbase 把 USDC 直接打到指定 Circle Gateway 地址
- **Chief 不接触卡号、不做 KYC**（KYC 由 Coinbase 承担）

### Webhook 边界

- Coinbase webhook 用于 **UI 进度展示**（"处理中" / "已完成"）
- Deposit 状态机以 **Circle webhook 为唯一权威触发**，不依赖 Coinbase webhook 推进资金状态

> 这一刀避免双源对账：Circle 才是资金事实源，Coinbase 只是 UI 显示信号。

## Consequences

### 正向

- KYC / 反洗钱 / 卡组织合规全部 Coinbase 承担
- 单一权威资金源（Circle），不会出现"Coinbase 报成功但 Circle 没到"的歧义
- v1 直接拿到生产可用的法币通道
- Coinbase Onramp 在加密圈接受度高，dev 用户不抗拒

### 负向

- 厂商绑定（v1.1 切换到其他 onramp 需要改 wallet-api 的 OnrampSvc 模块）
- Coinbase 服务费（~1–3.5%）由用户承担（可接受，业内通行）
- 用户体验受 Coinbase widget 限制（我们不能定制太多）

## 备选

- **MoonPay** —— 服务费略高，集成深度类似，无明显胜出
- **Stripe Crypto** —— 美国地域限制更紧；Stripe 品牌好但产品成熟度不及 Coinbase
- **Circle Mint（直接法币入 Circle）** —— 需要 KYB + MSB 评估，6 周以上 + 外部审批，超出 v1 窗口
- **完全 BYO USDC（无 onramp）** —— 保住合规但丢掉 M3 故事，否决
