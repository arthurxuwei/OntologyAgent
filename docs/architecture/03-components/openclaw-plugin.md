# 03 — Component / `OpenClaw plugin`

## 这张图回答什么

**部署在用户机器上的 plugin 内部由哪些模块组成？凭证在哪里 / 怎么落？**

这个 container 不在 AWS 里跑，但代码归我们维护，是 v1 唯一面向终端用户的产物。

## 图

```mermaid
graph TB
    subgraph openclaw["OpenClaw 运行时（用户机器）"]
        OC_CLI["OpenClaw CLI"]
        OC_Host["OpenClaw Plugin Host<br/>(管理 plugin 生命周期)"]
    end

    subgraph plugin["Chief Agent Wallet Plugin（用户机器）"]
        Manifest["<b>plugin manifest</b><br/>同时声明<br/>MCP server + native plugin"]
        MCPServer["<b>MCP Server Mode</b><br/>(stdio + HTTP)"]
        NativeMode["<b>Native Plugin Mode</b><br/>(OpenClaw 原生协议)"]
        DispatchUnify["<b>Unified Dispatch</b><br/>归一两种入口"]

        DeviceCodeFlow["<b>Device-code Login Flow</b><br/>开浏览器 / 显示 user_code /<br/>轮询 token"]
        CredStore["<b>Local Credential Store</b><br/>OS keychain (macOS Keychain /<br/>Windows DPAPI / Linux libsecret)"]
        Signer["<b>HMAC Signer</b><br/>生成请求签名"]
        APIClient["<b>Chief API Client</b><br/>带签名调 skill-server"]

        ReturnTaxonomy["<b>5-tier Return Handler</b><br/>success / pending_approval /<br/>failed_retryable / failed_terminal /<br/>unknown"]

        UI["<b>Terminal UI Helpers</b><br/>device-code 显示 /<br/>pending_approval 等待提示 /<br/>error 渲染"]
    end

    subgraph chief["Chief 服务（AWS）"]
        SkillSrv[skill-server]
        WalletAPI[wallet-api<br/>(device-code endpoints)]
    end

    subgraph user_browser["用户浏览器"]
        Browser["Owner Console<br/>(grant 页面)"]
    end

    OC_CLI -->|"openclaw plugin install <repo>"| OC_Host
    OC_Host -->|"读"| Manifest
    OC_Host -->|"启 MCP 模式 (Cursor 风)"| MCPServer
    OC_Host -->|"或 启 native 模式"| NativeMode
    MCPServer --> DispatchUnify
    NativeMode --> DispatchUnify

    DispatchUnify -->|"首次调用 / 凭证缺失"| DeviceCodeFlow
    DispatchUnify -->|"已有凭证"| Signer

    DeviceCodeFlow -->|"POST /v1/oauth/device/authorize"| WalletAPI
    DeviceCodeFlow -->|"轮询 POST /v1/oauth/device/token"| WalletAPI
    DeviceCodeFlow -.->|"用户开浏览器<br/>访问 verification_uri"| Browser
    Browser -->|"owner 输入 user_code + 同意"| WalletAPI
    DeviceCodeFlow -->|"得到 (key_id, secret)"| CredStore

    CredStore --> Signer
    Signer --> APIClient
    APIClient -->|"HMAC-signed"| SkillSrv
    APIClient --> ReturnTaxonomy
    ReturnTaxonomy --> UI

    classDef oc fill:#f5f0e6,stroke:#1A1A1A;
    classDef plugin fill:#fff,stroke:#A8590D,stroke-width:2px;
    classDef store fill:#fff5e8,stroke:#8B6914,stroke-width:2px;
    classDef chief fill:#FBF8F2,stroke:#A8590D;
    classDef ui fill:#fff,stroke:#5A5651;
    class OC_CLI,OC_Host oc;
    class Manifest,MCPServer,NativeMode,DispatchUnify,Signer,APIClient,ReturnTaxonomy plugin;
    class CredStore,DeviceCodeFlow store;
    class SkillSrv,WalletAPI chief;
    class UI,Browser ui;
```

## 关键说明

### 双格式打包

manifest 同时声明 MCP server 入口和 OpenClaw native plugin 入口。OpenClaw 自己决定用哪一种 —— 无论选哪种，背后跑的逻辑完全一致（经过 `Unified Dispatch` 归一）。

理由：保留 v2 把 plugin 暴露给其他 MCP 客户端的可能性，无需重写。

### Credential Store 的本地安全

绝不写明文到磁盘文件。各 OS 用各自的 secure storage：
- macOS：Keychain
- Windows：DPAPI (Credential Manager)
- Linux：libsecret (gnome-keyring / kwallet)

这是基础线 —— 一台 OpenClaw 用户机器被偷或 home 目录被打包外发，credential 不能裸暴露。

### Device-code 登录流程触发点

**两个时机**触发 device-code flow：
1. **首次调用任何花钱 skill 时**（懒触发，UX 好）
2. **用户主动 `openclaw plugin agent-wallet login`**（显式登录）

不在 plugin install 阶段强制登录，因为用户可能装好但暂时不用。

### 5-tier Return Handler 的硬契约

| Code | Plugin 行为 |
|---|---|
| `success` | 透传给 OpenClaw 业务流程 |
| `pending_approval` | **不能 retry**；展示"已提交，等 owner 在 Console 审批"；轮询查询接口 |
| `failed_retryable` | 指数退避，最多 3 次，全失败后转 `failed_terminal` |
| `failed_terminal` | 立即报错给 OpenClaw |
| `unknown` | 用 `request_id` 调查询接口确认实际状态后再决定 |

这是 plugin 层最容易出问题的地方 —— `pending_approval` 被错误重试会触发 owner 多次审批通知，是 v1 必须严卡的 UX bug。

### 不在 plugin 里的事

- Owner 操作（钱包创建 / 绑定 / 审批 / 提现 / kill-switch）—— 全部走 Web Console
- Eigenflux 网络消息收发 —— OpenClaw 自己负责（plugin 只关心钱）
- Escrow 状态机 —— 仅查询，状态在 ledger
