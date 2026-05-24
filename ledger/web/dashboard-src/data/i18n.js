// MVP i18n dictionary. Merges onto window.LANG_DICT alongside the main
// project's dictionary (../src/data/i18n.js, loaded before this file).
//
// We REUSE main keys where the copy is identical (ui.now, chrome.*,
// some m6.withdraw.* labels). New mvp.* keys cover MVP-specific surfaces.
// Both EN and ZH are populated up-front — no follow-up bilingual pass needed.

(function () {
  const MVP_DICT = {
    // ── Chrome ───────────────────────────────────────────────────────────
    'mvp.chrome.title':            { en: 'Agent Wallet · MVP', zh: 'Agent Wallet · MVP' },
    'mvp.chrome.dashboard_link':   { en: 'Open dashboard →',   zh: '打开 dashboard →' },
    'mvp.chrome.demo_link':        { en: 'Open demo →',        zh: '打开 demo →' },

    // ── Stage labels (drive LedgerFrame top-rule text) ───────────────────
    'mvp.stage.s1.label': { en: 'CHAT ATTACH',       zh: '对话装钱包' },
    'mvp.stage.s2.label': { en: 'OWNER CLAIM',       zh: '所有者认领' },
    'mvp.stage.s3.label': { en: 'LEDGER',            zh: '账户视图' },
    'mvp.stage.s4.label': { en: 'WITHDRAW · PICK',   zh: '提款 · 填写' },
    'mvp.stage.s5.label': { en: 'WITHDRAW · SETTLED',zh: '提款 · 已到账' },

    // ── S1 · Chat attach ──────────────────────────────────────────────────
    'mvp.s1.eyebrow':       { en: 'YOUR AGENT · LIVE ON EIGENFLUX', zh: '你的 agent · 已经跑在 EigenFlux 上' },
    'mvp.s1.header':        { en: 'A wallet, attached by the agent itself.', zh: 'agent 自己装的钱包。' },
    'mvp.s1.subhead': {
      en: 'No new install. Your existing agent reaches out, asks permission, and wires the wallet in — owner is bound by claim link, not by setup ceremony.',
      zh: '不用装新东西。你现在用的那个 agent 主动开口跟你说,你点头同意就装好了。所有权靠 claim 链接挂上去,免去注册开户那一套。',
    },
    'mvp.s1.agent_label':   { en: 'agentA',  zh: 'agentA' },
    'mvp.s1.you_label':     { en: 'you',     zh: '你' },
    'mvp.s1.timestamp_now': { en: 'now',     zh: '刚刚' },
    'mvp.s1.timestamp_seconds_ago': { en: '14s ago', zh: '14 秒前' },

    'mvp.s1.msg_1_agent': {
      en: 'Quick one — I noticed I could now hold and move USDC directly. Want me to attach a wallet to myself? Takes one click on your end.',
      zh: '顺便问一下,我现在可以直接收发和存 USDC 了。要不要我给自己装个钱包?你这边点一下就好。',
    },
    'mvp.s1.msg_2_user': {
      en: 'Sounds good — do it.',
      zh: '行,装吧。',
    },
    'mvp.s1.msg_3_agent_lead': {
      en: 'Done. Claim it from this link to bind ownership to your email:',
      zh: '装好了。点这个链接,把所有权认到你邮箱上:',
    },
    'mvp.s1.msg_3_meta': {
      en: 'expires in 10:00 · re-issuable via `chief claim refresh`',
      zh: '10 分钟内有效 · 过期了跑 `chief claim refresh` 重发',
    },
    'mvp.s1.msg_3_followup': {
      en: 'Same code reaches the same dashboard if you prefer the CLI.',
      zh: '换成 CLI 也一样,同一串码,同一个 dashboard。',
    },

    'mvp.s1.preview_label':         { en: 'CHIEF · DASHBOARD PREVIEW', zh: 'CHIEF · DASHBOARD 预览' },
    'mvp.s1.preview_empty':         { en: 'agentA · wallet pending',   zh: 'agentA · 待装钱包' },
    'mvp.s1.preview_attached_cue':  { en: '✓ Wallet attached',         zh: '✓ 钱包已装上' },
    'mvp.s1.preview_top_note': {
      en: 'Your dashboard will show every agent and every transaction. You can claim ownership whenever you want.',
      zh: 'Dashboard 会列出你名下每个 agent、每笔交易。什么时候认领,你自己说了算。',
    },

    // ── S2 · Claim ─────────────────────────────────────────────────────────
    'mvp.s2.crumb_root':       { en: 'Chief',        zh: 'Chief' },
    'mvp.s2.crumb_agent':      { en: 'agentA',       zh: 'agentA' },
    'mvp.s2.crumb_current':    { en: 'Claim',        zh: '认领' },
    'mvp.s2.modal_eyebrow':    { en: 'EMAIL · william@example.com', zh: '邮箱 · william@example.com' },
    'mvp.s2.modal_headline':   { en: 'Claim this wallet for your email?', zh: '把这个钱包认到你邮箱上?' },
    'mvp.s2.modal_body': {
      en: 'Ownership stays with you — withdrawal, limits, and revocation are gated by your email. The agent keeps operating it.',
      zh: '所有权归你,提款、限额、停用都看你邮箱。agent 平时用钱包不受影响。',
    },
    'mvp.s2.modal_button':     { en: 'Claim wallet',   zh: '认领钱包' },
    'mvp.s2.toast_success':    { en: '✓ Wallet claimed · agentA bound to william@example.com', zh: '✓ 认领成功 · agentA 已绑定 william@example.com' },

    // ── S3 · Ledger ───────────────────────────────────────────────────────
    'mvp.s3.balance_label':     { en: 'BALANCE',         zh: '余额' },
    'mvp.s3.tx_label':          { en: 'TRANSACTIONS',    zh: '交易记录' },
    'mvp.s3.tx_caption': {
      en: 'A single onramp deposit. The same view powers the dashboard’s Overview tab.',
      zh: '目前只有这一笔充值。dashboard 的 Overview 看到的也是这一份。',
    },
    'mvp.s3.tx_counterparty_onramp': { en: 'Coinbase Onramp', zh: 'Coinbase 充值' },

    // ── S4 · Withdraw pick ────────────────────────────────────────────────
    'mvp.s4.eyebrow':       { en: 'AGENT WALLET · WITHDRAW', zh: '钱包 · 提款' },
    'mvp.s4.headline':      { en: 'Move USDC to an external wallet.', zh: '把 USDC 提到外部钱包。' },
    'mvp.s4.subhead': {
      en: 'On-chain USDC only for now. Card / bank offramp via Coinbase ships in phase 2.',
      zh: '当前只支持链上 USDC。法币 / 银行卡出金走 Coinbase,二阶段再上。',
    },
    'mvp.s4.field_destination': { en: 'Destination address', zh: '目标地址' },
    'mvp.s4.field_amount':      { en: 'Amount (USDC)',       zh: '金额(USDC)' },
    'mvp.s4.amount_min_hint':   { en: 'Min 1 USDC · Network: Base · Est. gas ~$0.01', zh: '最低 1 USDC · 链:Base · 预估 gas ~$0.01' },
    'mvp.s4.button_max':        { en: 'max',                 zh: '最大' },
    'mvp.s4.button_confirm':    { en: 'Confirm withdrawal',  zh: '确认提款' },
    'mvp.s4.button_cancel':     { en: 'Cancel',              zh: '取消' },

    // ── S5 · Withdraw settled ─────────────────────────────────────────────
    'mvp.s5.status_pending':    { en: 'pending · broadcasting to network', zh: '处理中 · 正在广播到链上' },
    'mvp.s5.status_settled':    { en: '✓ settled on Base · 4s',           zh: '✓ 已到账 · Base · 4 秒' },
    'mvp.s5.headline':          { en: 'Withdrawal complete.',              zh: '提款已完成。' },
    'mvp.s5.summary_label':     { en: 'RECEIPT',                           zh: '凭证' },
    'mvp.s5.balance_label':     { en: 'NEW BALANCE',                       zh: '提款后余额' },
    'mvp.s5.tx_label':          { en: 'LEDGER',                            zh: '账户流水' },
    'mvp.s5.amount_label':      { en: 'Amount',                            zh: '金额' },
    'mvp.s5.destination_label': { en: 'To',                                zh: '目标' },
    'mvp.s5.network_label':     { en: 'Network',                           zh: '链' },
    'mvp.s5.gas_label':         { en: 'Network fee',                       zh: '链上费用' },
    'mvp.s5.tx_counterparty':   { en: 'External wallet',                   zh: '外部钱包' },
    'mvp.s5.footer_note': {
      en: 'Same flow lives in the dashboard’s Funding tab — available any time, not just inside this demo.',
      zh: '同一套流程在 dashboard 的 Funding tab 里也有,日常随时能走,不是只在 demo 里看看。',
    },

    // ── Stage labels (Act 2) ──────────────────────────────────────────────
    'mvp.stage.s6.label': { en: 'AUTONOMOUS PAY', zh: '自主结算' },
    'mvp.stage.s7.label': { en: 'WRAP',           zh: '收束' },

    // ── S6 · Autonomous pay (merged: intent + broadcast) ──────────────────
    // agentA finishes a batch, DECIDES on its own to settle 0.001 USDC to
    // agentB. The whole atomic flow (reasoning → fire → Chief broadcasts to
    // both → both ledgers update) lives in this one stage.
    'mvp.s6.col_agentA':      { en: 'agentA',                              zh: 'agentA' },
    'mvp.s6.col_agentA_role': { en: 'Document Workflow Assistant',         zh: '文档处理助手' },
    'mvp.s6.col_chief':       { en: 'Chief',                               zh: 'Chief' },
    'mvp.s6.col_chief_role':  { en: 'Off-chain settlement layer',          zh: '链下结算层' },
    'mvp.s6.col_agentB':      { en: 'agentB',                              zh: 'agentB' },
    'mvp.s6.col_agentB_role': { en: 'Data Provider · EigenFlux-verified',  zh: '数据提供方 · EigenFlux 认证' },

    'mvp.s6.agentA_thought_1': {
      en: 'batch_complete · 1,247 client docs processed · 87 data lookups consumed from agentB',
      zh: 'batch_complete · 已处理 1,247 篇客户文档 · 向 agentB 发起了 87 次数据查询',
    },
    'mvp.s6.agentA_thought_2': {
      en: '87 lookups × the agreed per-call rate = 0.001 USDC owed to agentB. Batch is shipped — invoice is due, paying it.',
      zh: '87 次查询 × 既定单价 = 应付 agentB 0.001 USDC。活儿交了,账单到期,把账结了。',
    },
    'mvp.s6.agentA_sdk_call': {
      en: 'chief.pay({ to: agentB, amount: 0.001 USDC, memo: "data_batch_2026_05" })',
      zh: 'chief.pay({ to: agentB, amount: 0.001 USDC, memo: "data_batch_2026_05" })',
    },
    'mvp.s6.agentA_sdk_return': {
      en: 'receipt: r_M9P4 · status: settled · gas: 0 (off-chain)',
      zh: 'receipt: r_M9P4 · status: settled · gas: 0(链下)',
    },
    'mvp.s6.agentB_thought_idle': {
      en: 'Service delivered — 87 lookups this batch. Awaiting settlement at the per-call rate.',
      zh: '服务已交付,本轮 87 次查询。按既定单价等结算。',
    },
    'mvp.s6.agentB_received': {
      en: 'Receipt pushed by chief — r_M9P4 · +0.001 USDC from agentA.',
      zh: 'chief 推来凭证:r_M9P4 · 来自 agentA · +0.001 USDC。',
    },
    'mvp.s6.agentB_thought_after': {
      en: 'Ledger updated. Amount matches the invoice. Back to work.',
      zh: '账本已更新。金额与账单一致。继续干活。',
    },

    // Two co-existing banners under the broadcast moment:
    //   initiated_*  — sits in agentA's column, makes the "agent agency" point
    //   broadcast_*  — sits in centre column, makes the "Chief → both" point
    'mvp.s6.initiated_label': { en: 'AGENT-INITIATED · NO HUMAN IN THE LOOP', zh: 'AGENT 自主发起 · 无人工介入' },
    'mvp.s6.initiated_body': {
      en: 'No prompt, no approval modal. The agent owns its budget and settles its own debts within the per-trade cap.',
      zh: '没 prompt,没审批弹窗。agent 在单笔限额内自己管预算、自己清账。',
    },
    'mvp.s6.broadcast_label': { en: 'BROADCAST · CHIEF → BOTH', zh: '广播 · CHIEF → 双方' },
    'mvp.s6.broadcast_body': {
      en: 'One push, two receipts. EigenFlux delivers the same record to payer and payee simultaneously — no agent-to-agent forwarding, no verify round-trip.',
      zh: '一次推送,双方同时收到。EigenFlux 把同一份凭证下发给付款方和收款方,中间不用 agent 互相转发,也不用 verify 来回校验。',
    },

    // ── S7 · Wrap ─────────────────────────────────────────────────────────
    'mvp.s7.eyebrow':  { en: 'WHAT JUST HAPPENED', zh: '刚刚发生了什么' },
    'mvp.s7.headline': { en: 'Four moments. One agent. Zero ceremony.', zh: '四个瞬间。一个 agent。零繁文缛节。' },
    'mvp.s7.recap_1_label': { en: '01 · ATTACH',   zh: '01 · 装' },
    'mvp.s7.recap_1_body': {
      en: 'agentA, already running on EigenFlux, attached a wallet to itself — owner bound by claim link, not by signup.',
      zh: '本来就在 EigenFlux 上跑着的 agentA,自己给自己装上了钱包。所有权靠 claim 链接挂上去,不用走开户那一套。',
    },
    'mvp.s7.recap_2_label': { en: '02 · OPERATE',  zh: '02 · 用' },
    'mvp.s7.recap_2_body': {
      en: 'Balance, ledger, and withdraw all on a single dashboard — usable by the agent and visible to the owner.',
      zh: '余额、流水、提款都在同一个 dashboard 里。agent 自己用,所有者随时能看。',
    },
    'mvp.s7.recap_3_label': { en: '03 · WITHDRAW', zh: '03 · 提' },
    'mvp.s7.recap_3_body': {
      en: 'USDC out to an external wallet on Base. Min 1 USDC, gas a fraction of a cent — Coinbase offramp follows in phase 2.',
      zh: 'USDC 提到 Base 上的外部钱包。最低 1 USDC,gas 几分钱;法币出金走 Coinbase,二阶段再上。',
    },
    'mvp.s7.recap_4_label': { en: '04 · SETTLE',   zh: '04 · 清' },
    'mvp.s7.recap_4_body': {
      en: 'agentA decided on its own to pay agentB 0.001 USDC for the batch — no prompt, no approval, no on-chain transaction.',
      zh: 'agentA 自己决定给 agentB 付了 0.001 USDC 完成结算。没 prompt、没审批、也没上链。',
    },
    'mvp.s7.pull_quote': {
      en: 'Money becomes the agent’s — not in the abstract, but in the everyday motion of paying, getting paid, and showing the work.',
      zh: '钱真正归 agent。不是嘴上的归属,是它每天付款、收款、把账目一笔笔摊开来的那种归属。',
    },
    'mvp.s7.final_brand':    { en: 'Agent Wallet · MVP',      zh: 'Agent Wallet · MVP' },
    'mvp.s7.final_tagline':  { en: 'Two weeks. Real flow. Real users.', zh: '两周。真实流程。真实用户。' },
    'mvp.s7.final_button':   { en: 'Restart demo',            zh: '重新播放' },
    'mvp.s7.final_dashboard':{ en: 'Open dashboard →',        zh: '打开 dashboard →' },

    // ── Onboarding · GitHub OAuth and claim-code ownership ────────────────
    'mvp.dash.auth.slug':                  { en: 'CHIEF · SIGN IN', zh: 'CHIEF · 登录' },
    'mvp.dash.auth.headline':              { en: 'Sign in to your agent wallet.', zh: '登录你的 agent 钱包。' },
    'mvp.dash.auth.subhead': {
      en: 'Continue with GitHub. Chief uses your account email to record claims after you enter a claim code.',
      zh: '使用 GitHub 登录。输入 claim code 后,Chief 会用当前账号邮箱记录认领关系。',
    },
    'mvp.dash.auth.github_button':         { en: 'Continue with GitHub',  zh: '用 GitHub 继续' },
    'mvp.dash.auth.error':                 { en: 'GitHub authorization was cancelled or failed. Please try again.', zh: 'GitHub 授权已取消或失败,请重试。' },
    'mvp.dash.auth.checking':              { en: 'CHECKING SESSION',      zh: '正在检查登录状态' },
    'mvp.dash.auth.note': {
      en: 'No email entry here: ownership comes from GitHub OAuth.',
      zh: '这里不手动输入邮箱: 所有权来自 GitHub OAuth。',
    },
    'mvp.dash.auth.consent_subdomain':     { en: 'authorize',             zh: 'authorize' },
    'mvp.dash.auth.consent_title':         { en: 'Authorize Chief',       zh: '授权 Chief' },
    'mvp.dash.auth.consent_app': {
      en: 'Chief wants to bind this browser session to your ledger email.',
      zh: 'Chief 请求把这个浏览器会话绑定到你的 ledger 邮箱。',
    },
    'mvp.dash.auth.consent_scope_label':   { en: 'Personal user data',    zh: '个人账号信息' },
    'mvp.dash.auth.consent_scope_profile': { en: 'Read your public profile', zh: '读取公开 profile' },
    'mvp.dash.auth.consent_scope_email':   { en: 'Read your email address', zh: '读取你的邮箱地址' },
    'mvp.dash.auth.consent_authorize':     { en: 'Authorize Chief',       zh: '授权 Chief' },
    'mvp.dash.auth.consent_cancel':        { en: 'Cancel',                zh: '取消' },
    'mvp.dash.auth.authorizing':           { en: 'Authorizing…',          zh: '正在授权…' },

    // ── Onboarding · Claim from real email-scoped agents ──────────────────
    'mvp.dash.claim.slug':                  { en: 'CHIEF · CLAIM',                            zh: 'CHIEF · 认领' },
    'mvp.dash.claim.initial_headline':      { en: 'Claim your first agent.',                  zh: '认领你的第一个 agent。' },
    'mvp.dash.claim.initial_subhead': {
      en: 'Your agent printed a claim code when it attached its wallet. Paste it below to bind ownership to your account.',
      zh: 'agent 装钱包的时候打了一串 claim code 给你。把它粘到下面,把所有权认到你账号上。',
    },
    'mvp.dash.claim.add_title':              { en: 'Add another agent',                       zh: '添加另一个 agent' },
    'mvp.dash.claim.input_help_initial': {
      en: "Paste the code from your agent's chat. It looks like clm_…",
      zh: '把 agent 在 chat 里打给你的那串 code 粘进来,通常是 clm_… 开头。',
    },
    'mvp.dash.claim.input_help_add': {
      en: 'Paste the claim code printed by the agent you want to attach.',
      zh: '把要添加的那个 agent 打出来的 claim code 粘进来。',
    },
    'mvp.dash.claim.code_label':             { en: 'CLAIM CODE',                              zh: '认领码' },
    'mvp.dash.claim.code_placeholder':       { en: 'clm_…',                                   zh: 'clm_…' },
    'mvp.dash.claim.validate_button':        { en: 'Validate code',                           zh: '验证' },
    'mvp.dash.claim.error_loading':          { en: 'Still loading claim codes. Try again in a moment.', zh: 'claim code 还在加载,稍后再试。' },
    'mvp.dash.claim.error_load_failed':      { en: 'Unable to load claim codes. Try again after the ledger service is available.', zh: '暂时加载不了 claim code,等 ledger 服务恢复后再试。' },
    'mvp.dash.claim.error_owner_mismatch':   { en: 'This claim code belongs to a different GitHub email. Sign out and use the owner account.', zh: '这串 claim code 属于另一个 GitHub 邮箱。请退出后使用 owner 账号登录。' },
    'mvp.dash.claim.error_not_found':        { en: 'Code not recognized. Check for typos.',   zh: '没识别到这串 code,检查一下有没有打错。' },
    'mvp.dash.claim.error_already_claimed':  { en: "You've already claimed this agent.",      zh: '这个 agent 已经认领过了。' },
    'mvp.dash.claim.no_agents':              { en: 'No unclaimed agents available.',          zh: '当前没有可认领的 agent。' },
    'mvp.dash.claim.confirm_validated':      { en: 'Code validated',                          zh: '验证通过' },
    'mvp.dash.claim.confirm_agent_label':    { en: 'AGENT',                                  zh: 'AGENT' },
    'mvp.dash.claim.confirm_wallet_label':   { en: 'WALLET',                                 zh: '钱包' },
    'mvp.dash.claim.confirm_owner_label':    { en: 'OWNER',                                  zh: '所有者' },
    'mvp.dash.claim.confirm_code_label':     { en: 'CODE',                                   zh: '认领码' },
    'mvp.dash.claim.claim_button':           { en: 'Claim wallet',                           zh: '认领钱包' },
    'mvp.dash.claim.use_different':          { en: 'Use a different code',                   zh: '换一串 code' },

    // ── Settings · Account card ───────────────────────────────────────────
    'mvp.dash.settings.account_signed_in_via': { en: 'SIGNED IN VIA',     zh: '登录方式' },
    'mvp.dash.settings.account_provider_github': { en: 'GitHub',          zh: 'GitHub' },
    'mvp.dash.settings.account_provider_email':  { en: 'Email',           zh: '邮箱' },
    'mvp.dash.settings.account_sign_out':        { en: 'Sign out',        zh: '退出登录' },

    // ── UI shared (small overrides on top of main dict) ───────────────────
    'mvp.ui.now':                 { en: 'now',  zh: '刚刚' },
    'mvp.ui.just_now':            { en: 'just now', zh: '刚刚' },

    // ── Dashboard chrome ──────────────────────────────────────────────────
    'mvp.dash.nav.portfolio':     { en: 'PORTFOLIO',    zh: '资产总览' },
    'mvp.dash.nav.overview':      { en: 'OVERVIEW',     zh: '概览' },
    'mvp.dash.nav.transactions':  { en: 'TRANSACTIONS', zh: '交易记录' },
    'mvp.dash.nav.funding':       { en: 'FUNDING',      zh: '资金' },
    'mvp.dash.nav.settings':      { en: 'SETTINGS',     zh: '设置' },
    'mvp.dash.reset':             { en: 'RESET',        zh: '重置' },
    'mvp.dash.reset_title':       { en: 'Clear MVP dashboard state and return to registration', zh: '清空 MVP dashboard 状态,回到注册页' },

    // ── Portfolio view ────────────────────────────────────────────────────
    'mvp.dash.portfolio.slug':       { en: 'PORTFOLIO',                       zh: '资产总览' },
    'mvp.dash.portfolio.add_agent':  { en: 'Add agent',                       zh: '添加 agent' },
    'mvp.dash.portfolio.empty':      { en: 'No agents yet. Add one to start.',zh: '还没有 agent,先添加一个吧。' },
    'mvp.dash.portfolio.just_claimed':{en: 'just claimed',                    zh: '刚认领' },
    'mvp.dash.portfolio.no_activity':{en: 'no activity yet',                  zh: '暂无活动' },
    'mvp.dash.portfolio.tx_suffix':  { en: 'tx',                              zh: '笔交易' },

    // ── Overview view ─────────────────────────────────────────────────────
    'mvp.dash.overview.slug':         { en: 'OVERVIEW',                        zh: '概览' },
    'mvp.dash.overview.balance_label':{ en: 'BALANCE',                         zh: '余额' },
    'mvp.dash.overview.kpi_in':       { en: 'LIFETIME IN',                     zh: '历史流入' },
    'mvp.dash.overview.kpi_out':      { en: 'LIFETIME OUT',                    zh: '历史流出' },
    'mvp.dash.overview.kpi_escrow':   { en: 'ESCROWED',                        zh: '锁定中' },
    'mvp.dash.overview.recent_label': { en: 'RECENT TRANSACTIONS',             zh: '最近交易' },
    'mvp.dash.overview.view_all':     { en: 'View all',                        zh: '查看全部' },
    'mvp.dash.overview.empty':        { en: 'No transactions yet.',            zh: '还没有交易。' },

    // ── Transactions view ─────────────────────────────────────────────────
    'mvp.dash.transactions.slug':              { en: 'TRANSACTIONS', zh: '交易记录' },
    'mvp.dash.transactions.empty':             { en: 'No transactions yet.', zh: '还没有交易。' },
    'mvp.dash.transactions.row_count_suffix':  { en: 'transactions',        zh: '笔交易' },
    'mvp.dash.status.info_aria':               { en: 'View explanation', zh: '查看说明' },
    'mvp.dash.status.pending_settle.label':    { en: 'SETTLING', zh: '待入账' },
    'mvp.dash.status.pending_settle.tooltip': {
      en: 'Received by Circle Gateway, waiting for the next batch settlement to land on-chain. Will move to your available balance once settled.',
      zh: '已被 Circle Gateway 接收,正在等待下一次批量结算上链。结算完成后会进入可用余额。',
    },
    'mvp.dash.status.pending_inbound_chain.label': { en: 'CREDITING', zh: '入账中' },
    'mvp.dash.status.pending_inbound_chain.tooltip': {
      en: 'Funds have left the exchange and are being confirmed on Base. Usually around 20 min, depending on network conditions.',
      zh: '资金已离开交易所,正在 Base 链确认。通常约 20 分钟,实际取决于链上情况。',
    },
    'mvp.dash.status.released.label':           { en: 'RELEASED', zh: '已释放' },
    'mvp.dash.status.withdrawn.label':          { en: 'WITHDRAWN', zh: '已提现' },
    'mvp.dash.status.credited.label':           { en: 'CREDITED', zh: '已入账' },

    // ── Funding view ──────────────────────────────────────────────────────
    'mvp.dash.funding.slug':                      { en: 'FUNDING',                 zh: '资金' },
    'mvp.dash.funding.balance_label':             { en: 'BALANCE',                 zh: '余额' },
    'mvp.dash.funding.pending_topup_label':        { en: 'PENDING TOP-UP',          zh: '充值处理中' },
    'mvp.dash.funding.add_label':                 { en: 'ADD FUNDS',               zh: '充值' },
    'mvp.dash.funding.add_description': {
      en: "Send USDC to this agent's wallet from any wallet on Base — exchange or self-custody.",
      zh: '从任意支持 Base 链的钱包(交易所或自管钱包)给这个 agent 发 USDC。',
    },
    'mvp.dash.funding.receive_eyebrow': { en: "THIS AGENT'S WALLET · BASE", zh: '本 agent 的收款地址 · BASE' },
    'mvp.dash.funding.gateway_explainer': {
      en: 'Funds that arrive on Base are automatically swept into our settlement layer (Circle Gateway), so agent-to-agent transactions carry no extra fees. Looking this address up on Basescan may show 0 USDC — the balance shown above is authoritative.',
      zh: '资金到达 Base 后会自动转入我们的结算层(Circle Gateway),确保 agent 之间的每笔交易不产生额外手续费。直接在 Basescan 查询此地址可能显示 0 USDC,实际余额以上方为准。',
    },
    'mvp.dash.funding.onramp_coming_soon': {
      en: 'Buying USDC with card or bank transfer is on the way — Coinbase Onramp lands in an upcoming release.',
      zh: '银行卡 / 银行转账买 USDC 的入口正在路上,会在后续版本里上线。',
    },
    'mvp.dash.funding.onramp_title':              { en: 'Buy USDC',                 zh: '直接购买' },
    'mvp.dash.funding.onramp_button': { en: 'Open Coinbase onramp', zh: '打开 Coinbase 充值' },

    // ── Coinbase onramp modal ─────────────────────────────────────────────
    'mvp.dash.onramp.amount_label':         { en: 'YOU PAY',                  zh: '支付' },
    'mvp.dash.onramp.receive_label':        { en: 'YOU RECEIVE',              zh: '到账' },
    'mvp.dash.onramp.fee_label':            { en: 'Fee',                      zh: '手续费' },
    'mvp.dash.onramp.min_max_hint':         { en: '5 USD minimum · 1,000 USD maximum', zh: '最低 5 USD · 最高 1,000 USD' },
    'mvp.dash.onramp.payment_method_label': { en: 'PAYMENT METHOD',           zh: '支付方式' },
    'mvp.dash.onramp.method_apple_pay':     { en: 'Apple Pay',                zh: 'Apple Pay' },
    'mvp.dash.onramp.method_card':          { en: 'Debit / Credit card',      zh: '借记卡 / 信用卡' },
    'mvp.dash.onramp.method_bank':          { en: 'Bank transfer',            zh: '银行转账' },
    'mvp.dash.onramp.method_bank_meta':     { en: '1-3 days',                 zh: '1-3 个工作日' },
    'mvp.dash.onramp.network_label':        { en: 'Network',                  zh: '链' },
    'mvp.dash.onramp.pay_button':           { en: 'Pay {amount} USD',         zh: '支付 {amount} USD' },
    'mvp.dash.onramp.processing_title':     { en: 'Opening Coinbase…',        zh: '正在打开 Coinbase…' },
    'mvp.dash.onramp.processing_body':      { en: 'Creating a hosted onramp session for this agent wallet.', zh: '正在为这个 Agent 钱包创建托管充值会话。' },
    'mvp.dash.onramp.success_title':        { en: 'Coinbase onramp opened',   zh: 'Coinbase 充值已打开' },
    'mvp.dash.onramp.success_body':         {
      en: 'Complete payment in Coinbase. The ledger will show the funding session while settlement completes.',
      zh: '请在 Coinbase 完成支付。结算期间，ledger 会显示这笔充值会话。',
    },
    'mvp.dash.onramp.error_title':          { en: 'Unable to open onramp',     zh: '无法打开充值' },
    'mvp.dash.onramp.error_body':           { en: 'Check that this agent has a full Base wallet address, then try again.', zh: '请确认这个 Agent 已有完整 Base 钱包地址，然后重试。' },
    'mvp.dash.funding.transfer_title':             { en: 'Transfer from exchange', zh: '从交易所转入' },
    'mvp.dash.funding.copy':       { en: 'COPY',     zh: '复制' },
    'mvp.dash.funding.copied':     { en: '✓ COPIED', zh: '✓ 已复制' },
    'mvp.dash.funding.transfer_step_1': { en: 'From your wallet or exchange, choose to send USDC on Base.', zh: '在你的钱包或交易所,选择把 USDC 通过 Base 链发出。' },
    'mvp.dash.funding.transfer_step_2': { en: 'Paste this agent\'s address as the destination.',           zh: '把上方地址粘贴为收款地址。' },
    'mvp.dash.funding.transfer_step_3': { en: 'Confirm — funds land here within a minute.',                zh: '确认转账,通常一分钟内到账。' },
    'mvp.dash.funding.qr_caption': {
      en: 'Scan from your\nexchange app\nUSDC · Base',
      zh: '用交易所 app\n扫码\nUSDC · Base',
    },

    'mvp.dash.funding.withdraw_label':              { en: 'WITHDRAW',                 zh: '提款' },
    'mvp.dash.funding.field_destination':           { en: 'Destination address', zh: '目标地址' },
    'mvp.dash.funding.field_amount':                { en: 'Amount (USDC)',       zh: '金额(USDC)' },
    'mvp.dash.funding.max':                         { en: 'MAX',                 zh: '最大' },
    'mvp.dash.funding.min_hint':                    { en: 'Min 1 USDC · Network: Base · Est. gas ~0.01', zh: '最低 1 USDC · 链:Base · 预估 gas ~0.01' },
    'mvp.dash.funding.term_network':                { en: 'Network',                                      zh: '链' },
    'mvp.dash.funding.term_fee':                    { en: 'Network fee',                                  zh: '链上费用' },
    'mvp.dash.funding.term_net_destination':        { en: 'Net to destination',                           zh: '目标到账' },
    'mvp.dash.funding.term_eta':                    { en: 'ETA',                                          zh: '预计时间' },
    'mvp.dash.funding.eta_under_minute':            { en: 'Usually under a minute after submission',       zh: '提交后通常一分钟内完成' },
    'mvp.dash.funding.fee_covered':                 { en: 'Covered by Chief',                             zh: '由 Chief 承担' },
    'mvp.dash.funding.exceeds_balance':             { en: 'Amount exceeds available balance.',            zh: '金额超过可用余额。' },
    'mvp.dash.funding.confirm':                     { en: 'Confirm withdrawal',                           zh: '确认提款' },
    'mvp.dash.funding.pending':                     { en: 'Broadcasting…',                                zh: '广播中…' },
    'mvp.dash.funding.settled':                     { en: '✓ Settled on Base',                            zh: '✓ Base 上已到账' },
    'mvp.dash.funding.withdraw_failed':             { en: 'Withdrawal failed. Try again.',                 zh: '提款失败,请重试。' },
    'mvp.dash.funding.another':                     { en: 'Withdraw another',                             zh: '再来一笔' },
    'mvp.dash.funding.new_row':                     { en: 'NEW LEDGER ROW',                               zh: '新增流水' },

    // ── Address book ──────────────────────────────────────────────────────
    'mvp.dash.addr_book.placeholder':   { en: 'Select a wallet…',         zh: '选择钱包…' },
    'mvp.dash.addr_book.empty_inline':  { en: 'No saved addresses',       zh: '还没有保存的地址' },
    'mvp.dash.addr_book.empty_title':   { en: 'No saved addresses yet',   zh: '还没有保存的地址' },
    'mvp.dash.addr_book.empty_cta':     { en: '+ Add your first wallet',  zh: '+ 添加第一个钱包' },
    'mvp.dash.addr_book.add_new':       { en: 'Add new address',          zh: '添加新地址' },

    'mvp.dash.addr_add.title':              { en: 'Add wallet address',                     zh: '添加钱包地址' },
    'mvp.dash.addr_add.instruction':        { en: 'How would you like to add it?',          zh: '通过哪种方式添加?' },
    'mvp.dash.addr_add.method_scan':        { en: 'Scan QR',                                zh: '扫描二维码' },
    'mvp.dash.addr_add.method_scan_desc':   { en: 'Camera or upload image',                 zh: '摄像头扫码或上传图片' },
    'mvp.dash.addr_add.method_paste':       { en: 'Paste / type',                           zh: '粘贴 / 输入' },
    'mvp.dash.addr_add.method_paste_desc':  { en: 'Enter the 0x address manually',          zh: '手动输入 0x 地址' },
    'mvp.dash.addr_add.back':               { en: '← Use a different method',               zh: '← 换一种方式' },
    'mvp.dash.addr_add.paste_label':        { en: 'Wallet address',                         zh: '钱包地址' },
    'mvp.dash.addr_add.paste_placeholder':  { en: '0x…',                                    zh: '0x…' },
    'mvp.dash.addr_add.paste_invalid':      { en: 'Address must be 0x followed by 40 hex characters.', zh: '地址必须是 0x 加 40 位十六进制字符。' },
    'mvp.dash.addr_add.continue':           { en: 'Continue',                               zh: '继续' },
    'mvp.dash.addr_add.captured_label':     { en: 'Address',                                zh: '地址' },
    'mvp.dash.addr_add.label_label':        { en: 'Label (optional)',                       zh: '名称(选填)' },
    'mvp.dash.addr_add.chain_label':        { en: 'Network',                                zh: '链' },
    'mvp.dash.addr_add.chain_disclaimer':   { en: 'USDC will arrive on Base. Make sure your destination wallet supports Base.', zh: 'USDC 将通过 Base 到账。请确认你的钱包支持 Base 链。' },
    'mvp.dash.addr_add.default_label':      { en: 'Set as default for withdrawals',         zh: '设为默认提款地址' },
    'mvp.dash.addr_add.duplicate':          { en: 'This address is already in your book.',  zh: '这个地址已经在地址簿里了。' },
    'mvp.dash.addr_add.save':               { en: 'Save wallet',                            zh: '保存' },
    'mvp.dash.addr_add.cancel':             { en: 'Cancel',                                 zh: '取消' },

    'mvp.dash.addr_scan.requesting':         { en: 'Requesting camera access…',            zh: '正在请求摄像头权限…' },
    'mvp.dash.addr_scan.denied':             { en: 'Camera permission denied.',            zh: '摄像头权限被拒绝。' },
    'mvp.dash.addr_scan.no_camera':          { en: 'No camera available on this device.',  zh: '此设备没有可用的摄像头。' },
    'mvp.dash.addr_scan.upload_instead':     { en: 'Use image upload below instead.',      zh: '请改用下方的图片上传。' },
    'mvp.dash.addr_scan.hint':               { en: 'Hold a QR code in view',               zh: '把二维码对准取景框' },
    'mvp.dash.addr_scan.or_upload':          { en: 'Or scan from an image:',               zh: '或扫描图片:' },
    'mvp.dash.addr_scan.upload':             { en: 'Upload image',                         zh: '上传图片' },
    'mvp.dash.addr_scan.retry':              { en: 'Try again',                            zh: '重试' },
    'mvp.dash.addr_scan.error_not_address':  { en: 'Scanned successfully — but no wallet address inside.', zh: '识别成功 —— 但里面没有钱包地址。' },
    'mvp.dash.addr_scan.error_no_qr':        { en: "Couldn't find a QR code in this image.", zh: '图片里没找到二维码。' },
    'mvp.dash.addr_scan.error_image_read':   { en: "Couldn't read that image.",            zh: '读不出图片内容。' },

    // ── Settings view ─────────────────────────────────────────────────────
    'mvp.dash.settings.slug':                  { en: 'SETTINGS',  zh: '设置' },
    'mvp.dash.settings.account_title':         { en: 'ACCOUNT',  zh: '账户' },
    'mvp.dash.settings.account_email_label':   { en: 'Owner email', zh: '所有者邮箱' },

    'mvp.dash.settings.wallets_title':         { en: 'EXTERNAL WALLETS',                                       zh: '外部钱包' },
    'mvp.dash.settings.wallets_description':   { en: 'Addresses you can withdraw USDC to. Saved across all your agents.', zh: '可以提款 USDC 过去的地址,在你所有 agent 之间共享。' },
    'mvp.dash.settings.wallets_empty':         { en: "You haven't saved any external wallets yet.",            zh: '还没有保存任何外部钱包。' },
    'mvp.dash.settings.wallets_add_first':     { en: 'Add your first wallet',                                  zh: '添加第一个钱包' },
    'mvp.dash.settings.wallets_add':           { en: 'Add wallet',                                             zh: '添加钱包' },
    'mvp.dash.settings.wallets_default_tag':   { en: 'DEFAULT',                                                zh: '默认' },
    'mvp.dash.settings.wallets_edit':          { en: 'Edit',                                                   zh: '改名' },
    'mvp.dash.settings.wallets_set_default':   { en: 'Set default',                                            zh: '设为默认' },
    'mvp.dash.settings.wallets_remove':        { en: 'Remove',                                                 zh: '删除' },
    'mvp.dash.settings.wallets_remove_confirm':{ en: 'Remove "{label}" from your address book?',               zh: '从地址簿删除 "{label}"?' },

    'mvp.dash.settings.limits_title':          { en: 'SPEND LIMITS', zh: '支出限额' },
    'mvp.dash.settings.limits_per_trade_label':{ en: 'Per-trade cap',          zh: '单笔上限' },
    'mvp.dash.settings.limits_save':           { en: 'Save',                   zh: '保存' },
    'mvp.dash.settings.limits_cancel':         { en: 'cancel',                 zh: '取消' },
    'mvp.dash.settings.limits_saved':          { en: 'SAVED',                  zh: '已保存' },
    'mvp.dash.settings.limits_help': {
      en: 'Any value between 0 and 100 USDC. Daily caps and per-counterparty limits arrive in phase 2.',
      zh: '0–100 USDC 之间任意取值。单日总限额、按对手方分设的限额,二阶段再上。',
    },
    'mvp.dash.settings.limits_invalid': {
      en: 'Enter a number between 0 and 100.',
      zh: '请输入 0 到 100 之间的数字。',
    },

  };

  if (!window.LANG_DICT) window.LANG_DICT = {};
  Object.assign(window.LANG_DICT, MVP_DICT);

  window.MVP_DICT = MVP_DICT;
})();
