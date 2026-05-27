// MVP Funding — the closed-loop core. Three blocks:
//
//   ADD FUNDS  — single receive-card:
//                  • Transfer from exchange — surfaces this agent's USDC
//                    receive address with a Copy button + a step-by-step guide.
//
//   WITHDRAW   — inline form (not a modal). USDC chain address + amount +
//                MAX + Confirm. Enforces:
//                  • destination must be a 0x… Base address (40 hex chars)
//                  • amount ≥ 5 USDC (the network-fee min documented in Q1)
//                  • amount ≤ available balance
//                After Confirm: the backend settles through the ledger
//                withdrawal endpoint, then returns the authoritative account
//                balance and ledger row for the local view.
//
// Below: a "Currently USDC on Base only" small-print line plus the phase-2
// Coinbase-offramp callout, both already part of the i18n contract.

(function () {
  function MvpFundingView({ data }) {
    const t = window.useT();
    const { wallets, defaultWalletId, ownerEmail } = window.useAppState();
    if (!data) return null;

    // Local view state — receive address visibility, withdraw form state.
    // These are intentionally view-local: a refresh re-renders them in the
    // initial state, which is fine for a demo.
    const [addrCopied, setAddrCopied] = React.useState(false);
    const [selectedWalletId, setSelectedWalletId] = React.useState(defaultWalletId);
    const [amount, setAmount]         = React.useState('');
    const [stage, setStage]           = React.useState('idle');      // idle | pending | settled
    const [withdrawError, setWithdrawError] = React.useState('');
    const [localBalance, setLocalBalance] = React.useState(data.balance.available);
    const [withdrawBalance, setWithdrawBalance] = React.useState(Number(data.balance.withdrawAvailable ?? data.balance.available ?? 0));
    const [localTxs, setLocalTxs]     = React.useState(data.transactions || []);
    const [addAddressOpen, setAddAddressOpen] = React.useState(false);

    // Reset when the agent (data) changes — view key in parent triggers
    // remount, but the safety net is cheap.
    React.useEffect(() => {
      setLocalBalance(data.balance.available);
      setWithdrawBalance(Number(data.balance.withdrawAvailable ?? data.balance.available ?? 0));
      setLocalTxs(data.transactions || []);
      setStage('idle');
      setAmount('');
      setWithdrawError('');
      setSelectedWalletId(defaultWalletId);
    }, [data, defaultWalletId]);

    const receiveAddress = data.agent.fullWalletAddress || data.agent.walletAddress;
    const MIN_WITHDRAW = 1;
    const WITHDRAW_GAS_USDC = 0.003;

    const selectedWallet = wallets.find((w) => w.id === selectedWalletId);
    const dest = selectedWallet?.address || '';
    const destValid   = !!selectedWallet && /^0x[0-9a-fA-F]{40}$/.test(dest.trim());
    const amountClean = amount.trim();
    const amountShapeValid = /^\d+(?:\.\d{0,6})?$/.test(amountClean);
    const amountNum   = amountShapeValid ? Number(amountClean) : NaN;
    const amountValid = amountShapeValid && !Number.isNaN(amountNum) && amountNum >= MIN_WITHDRAW && amountNum <= withdrawBalance;
    const netAmount = amountValid ? Math.max(0, amountNum - WITHDRAW_GAS_USDC) : null;
    const canConfirm  = destValid && amountValid && stage === 'idle';
    const belowMin    = !Number.isNaN(amountNum) && amountNum > 0 && amountNum < MIN_WITHDRAW;
    const overBalance = !Number.isNaN(amountNum) && amountNum > withdrawBalance;
    const creditedLinkedEntryIds = new Set(
      localTxs
        .filter((tx) => tx.status === 'credited')
        .map((tx) => tx.linkedEntryId || (tx.metadata && tx.metadata.linkedEntryId))
        .filter(Boolean)
    );
    const pendingDeposits = localTxs.filter((tx) => {
      if (tx.status !== 'pending_inbound_chain') return false;
      return !creditedLinkedEntryIds.has(tx.id);
    });

    const usdcToAtomic = (value) => {
      const raw = String(value || '').trim();
      const match = raw.match(/^(\d+)(?:\.(\d{0,6})?)?$/);
      if (!match) return '0';
      const whole = BigInt(match[1]);
      const fractional = BigInt((match[2] || '').padEnd(6, '0'));
      return String((whole * 1000000n) + fractional);
    };

    const atomicToUsdc = (value) => {
      const parsed = Number(value || 0);
      return Number.isFinite(parsed) ? parsed / 1000000 : 0;
    };

    const displayAmountAtomic = (row, fallbackAtomic) => {
      const meta = row.metadata || {};
      const availableAmount = atomicToUsdc(row.availableDeltaAtomic);
      const metadataAmount = meta.amountAtomic ? atomicToUsdc(meta.amountAtomic) : 0;
      return availableAmount > 0 ? row.availableDeltaAtomic : (metadataAmount > 0 ? meta.amountAtomic : fallbackAtomic);
    };

    const handleCopyAddr = () => {
      // navigator.clipboard might be unavailable in some headless contexts;
      // soft-fail to keep the demo from breaking.
      try { navigator.clipboard && navigator.clipboard.writeText(receiveAddress); } catch { /* noop */ }
      setAddrCopied(true);
      setTimeout(() => setAddrCopied(false), 1500);
    };

    const handleMax = () => {
      setWithdrawError('');
      setAmount(String(withdrawBalance.toFixed(2)));
    };

    const apiErrorMessage = (payload) => {
      if (payload && payload.detail && payload.detail.message) return payload.detail.message;
      if (payload && typeof payload.detail === 'string') return payload.detail;
      if (payload && payload.message) return payload.message;
      return t('mvp.dash.funding.withdraw_failed');
    };

    const handleConfirm = async () => {
      if (!canConfirm) return;
      const amountAtomic = usdcToAtomic(amount);
      setStage('pending');
      setWithdrawError('');
      try {
        const response = await fetch('/ledger/withdrawals', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            agentId: data.agent.id,
            ownerEmail,
            destinationAddress: dest.trim(),
            amountAtomic,
            reason: 'dashboard withdrawal',
            metadata: { source: 'dashboard' },
          }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(apiErrorMessage(payload));
        }
        const rows = Array.isArray(payload.entries) ? payload.entries : [payload.entry].filter(Boolean);
        const settledAmount = rows.reduce((total, row) => {
          const rowDelta = atomicToUsdc(row.availableDeltaAtomic);
          return total + (rowDelta !== 0 ? Math.abs(rowDelta) : 0);
        }, 0) || amountNum;
        const nextTxs = rows.map((row, index) => {
          const meta = row.metadata || {};
          const txAmountAtomic = displayAmountAtomic(row, amountAtomic);
          const statusOrder = meta.dashboardStatus === 'withdrawn' ? 2 : meta.dashboardStatus === 'withdraw_submitted' ? 1 : 0;
          return {
            id: row.entryId || meta.withdrawalId || `wd_${Date.now().toString(36).slice(-5)}`,
            counterparty: meta.counterparty || `External · ${dest.slice(0, 6)}…${dest.slice(-4)}`,
            amount: Math.abs(atomicToUsdc(txAmountAtomic)) || amountNum,
            direction: 'out',
            role: 'withdrawal',
            status: meta.dashboardStatus || 'withdrawn',
            timestamp: t('mvp.ui.just_now'),
            sortOrder: (statusOrder * 1000) - index,
            gasFee: meta.gasFee,
            gasFeeAtomic: meta.gasFeeAtomic,
            netAmount: meta.netAmount,
            netAmountAtomic: meta.netAmountAtomic,
            txHash: meta.txHash,
            network: meta.network || 'Base',
          };
        }).sort((a, b) => b.sortOrder - a.sortOrder);
        setStage('settled');
        setLocalBalance((b) => Math.max(0, b - settledAmount));
        setWithdrawBalance((b) => Math.max(0, b - settledAmount));
        setLocalTxs((prev) => [...nextTxs, ...prev]);
      } catch (error) {
        setStage('idle');
        setWithdrawError(error && error.message ? error.message : t('mvp.dash.funding.withdraw_failed'));
      }
    };

    const previewTxs = stage === 'settled' ? localTxs.slice(0, Math.min(2, localTxs.length)) : [];

    return (
      <div>
        <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)', marginBottom: '8px' }}>
          {t('mvp.dash.funding.slug')} · {data.agent.name}
        </div>

        {/* Big balance — anchors the closed loop visually. */}
        <div className="font-display" style={{ fontSize: '12px', color: 'var(--ink-tertiary)', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: '4px' }}>
          {t('mvp.dash.funding.balance_label')}
        </div>
        <div className="font-mono" style={{ fontSize: '48px', color: 'var(--ink-primary)', letterSpacing: '-0.02em', lineHeight: 1 }}>
          {window.formatAmount(localBalance)}
          <span style={{ fontSize: '16px', color: 'var(--ink-tertiary)', marginLeft: '12px', letterSpacing: '0.04em' }}>USDC</span>
        </div>

        {pendingDeposits.length > 0 && (
          <Section
            title={t('mvp.dash.funding.pending_topup_label')}
            titleSuffix={
              window.InfoTrigger && (
                <window.InfoTrigger
                  tooltipKey="mvp.dash.status.pending_inbound_chain.tooltip"
                  size="sm"
                />
              )
            }
          >
            {pendingDeposits.map((tx) => (
              <PendingDepositCard key={tx.id} tx={tx} t={t} />
            ))}
          </Section>
        )}

        {/* ADD FUNDS section */}
        <Section
          title={t('mvp.dash.funding.add_label')}
          description={t('mvp.dash.funding.add_description')}
        >
          <Card>
            <div className="grid" style={{ gridTemplateColumns: '1fr auto', gap: '28px', alignItems: 'start' }}>
              <div>
                <div className="flex items-center" style={{ gap: '6px', marginBottom: '8px' }}>
                  <span className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)' }}>
                    {t('mvp.dash.funding.receive_eyebrow')}
                  </span>
                  {window.InfoTrigger && (
                    <window.InfoTrigger
                      tooltipKey="mvp.dash.funding.gateway_explainer"
                      size="sm"
                    />
                  )}
                </div>
                <div
                  className="font-mono"
                  style={{
                    padding: '10px 14px',
                    background: 'var(--surface-paper)',
                    border: '1px solid var(--stroke-hairline)',
                    fontSize: '12px',
                    color: 'var(--ink-primary)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px',
                  }}
                >
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {receiveAddress}
                  </span>
                  <button
                    type="button"
                    onClick={handleCopyAddr}
                    className="smallcaps-mono"
                    style={{
                      background: 'transparent',
                      border: '1px solid var(--stroke-rule)',
                      padding: '2px 8px',
                      cursor: 'pointer',
                      color: addrCopied ? 'var(--status-positive)' : 'var(--ink-secondary)',
                      fontSize: '10px',
                    }}
                  >
                    {addrCopied ? t('mvp.dash.funding.copied') : t('mvp.dash.funding.copy')}
                  </button>
                </div>
                <ol
                  className="font-body"
                  style={{
                    marginTop: '14px',
                    paddingLeft: '20px',
                    fontSize: '12px',
                    color: 'var(--ink-secondary)',
                    lineHeight: 1.7,
                  }}
                >
                  <li>{t('mvp.dash.funding.transfer_step_1')}</li>
                  <li>{t('mvp.dash.funding.transfer_step_2')}</li>
                  <li>{t('mvp.dash.funding.transfer_step_3')}</li>
                </ol>
              </div>
              <QrPanel address={receiveAddress} t={t} />
            </div>
          </Card>

          <div className="font-body" style={{ marginTop: '14px', fontSize: '12px', color: 'var(--ink-secondary)', lineHeight: 1.55 }}>
            Deposits may take a few minutes while Base confirms and Kovaloop credits the Gateway Wallet.
          </div>

          <div
            className="font-body"
            style={{
              marginTop: '14px',
              fontSize: '11px',
              color: 'var(--ink-tertiary)',
              letterSpacing: '0.02em',
              lineHeight: 1.6,
            }}
          >
            {t('mvp.dash.funding.onramp_coming_soon')}
          </div>
        </Section>

        {/* WITHDRAW section */}
        <Section title={t('mvp.dash.funding.withdraw_label')}>
          <div style={{ maxWidth: '640px' }}>
            <Field label={t('mvp.dash.funding.field_destination')}>
              <window.AddressPicker
                selectedId={selectedWalletId}
                onSelect={(id) => { setWithdrawError(''); setSelectedWalletId(id); }}
                onAddNew={() => setAddAddressOpen(true)}
                disabled={stage !== 'idle'}
              />
            </Field>

            <Field label={t('mvp.dash.funding.field_amount')}>
              <div className="flex items-center" style={{ gap: '8px' }}>
                <input
                  type="text"
                  value={amount}
                  onChange={(e) => { setWithdrawError(''); setAmount(e.target.value); }}
                  placeholder="0.00"
                  disabled={stage !== 'idle'}
                  className="font-mono"
                  style={{
                    width: '180px',
                    padding: '10px 14px',
                    border: `1px solid ${belowMin || overBalance ? 'var(--status-negative)' : 'var(--stroke-rule)'}`,
                    background: 'var(--surface-paper)',
                    fontSize: '14px',
                    color: 'var(--ink-primary)',
                    outline: 'none',
                  }}
                />
                <button
                  type="button"
                  onClick={handleMax}
                  disabled={stage !== 'idle'}
                  className="smallcaps-mono"
                  style={{
                    padding: '8px 12px',
                    background: 'transparent',
                    border: '1px solid var(--stroke-rule)',
                    color: 'var(--ink-tertiary)',
                    fontSize: '11px',
                    cursor: 'pointer',
                  }}
                >
                  {t('mvp.dash.funding.max')}
                </button>
              </div>
              <div
                className="font-mono"
                style={{
                  marginTop: '8px',
                  fontSize: '11px',
                  color: (belowMin || overBalance) ? 'var(--status-negative)' : 'var(--ink-tertiary)',
                  letterSpacing: '0.02em',
                }}
              >
                {overBalance
                  ? t('mvp.dash.funding.exceeds_balance')
                  : t('mvp.dash.funding.min_hint')}
              </div>
              {withdrawError && (
                <div
                  className="font-mono"
                  style={{
                    marginTop: '10px',
                    fontSize: '11px',
                    color: 'var(--status-negative)',
                    letterSpacing: '0.02em',
                  }}
                >
                  {withdrawError}
                </div>
              )}
            </Field>

            <div
              style={{
                marginTop: '4px',
                marginBottom: '4px',
                padding: '10px 14px',
                background: 'var(--surface-card)',
                border: '1px solid var(--stroke-hairline)',
                display: 'grid',
                gridTemplateColumns: 'auto 1fr',
                rowGap: '6px',
                columnGap: '14px',
                fontSize: '12px',
              }}
            >
              <TermLabel>{t('mvp.dash.funding.term_network')}</TermLabel>
              <TermValue>{selectedWallet ? `${chainName(selectedWallet.chain)} · USDC` : 'Base · USDC'}</TermValue>
              <TermLabel>{t('mvp.dash.funding.term_fee')}</TermLabel>
              <TermValue>~0.003 USDC</TermValue>
              {netAmount !== null && (
                <>
                  <TermLabel>{t('mvp.dash.funding.term_net_destination')}</TermLabel>
                  <TermValue accent>{window.formatAmount(netAmount)} USDC</TermValue>
                </>
              )}
              <TermLabel>{t('mvp.dash.funding.term_eta')}</TermLabel>
              <TermValue>{t('mvp.dash.funding.eta_under_minute')}</TermValue>
            </div>

            <div className="flex" style={{ gap: '12px', marginTop: '24px' }}>
              <button
                type="button"
                onClick={handleConfirm}
                disabled={!canConfirm}
                className="font-body"
                style={{
                  padding: '12px 24px',
                  background: canConfirm ? 'var(--ink-primary)' : 'var(--stroke-hairline)',
                  border: `1px solid ${canConfirm ? 'var(--ink-primary)' : 'var(--stroke-rule)'}`,
                  color: canConfirm ? 'var(--ink-inverse)' : 'var(--ink-tertiary)',
                  cursor: canConfirm ? 'pointer' : 'not-allowed',
                  fontSize: '14px',
                  fontWeight: 500,
                  boxShadow: canConfirm ? '0 0 0 4px var(--accent-amber-soft)' : 'none',
                  transition: 'all 200ms',
                }}
              >
                {stage === 'pending'
                  ? t('mvp.dash.funding.pending')
                  : stage === 'settled'
                  ? t('mvp.dash.funding.settled')
                  : t('mvp.dash.funding.confirm')}
              </button>
              {stage === 'settled' && (
                <button
                  type="button"
                  onClick={() => { setStage('idle'); setAmount(''); }}
                  className="font-body"
                  style={{
                    padding: '12px 24px',
                    background: 'transparent',
                    border: '1px solid var(--stroke-rule)',
                    color: 'var(--ink-primary)',
                    fontSize: '14px',
                    cursor: 'pointer',
                  }}
                >
                  {t('mvp.dash.funding.another')}
                </button>
              )}
            </div>

          </div>

          {/* Live preview of the resulting ledger row when a withdraw has settled. */}
          {previewTxs.length > 0 && (
            <div className="fade-up" style={{ marginTop: '40px', maxWidth: '720px' }}>
              <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)', marginBottom: '8px' }}>
                {t('mvp.dash.funding.new_row')}
              </div>
              <div className="rule-t" />
              {previewTxs.map((tx) => (
                <window.TransactionRow
                  key={tx.id}
                  receiptId={tx.id}
                  counterparty={tx.counterparty}
                  amount={tx.amount}
                  direction={tx.direction}
                  status={tx.status}
                  timestamp={tx.timestamp}
                  role={tx.role}
                  gasFee={tx.gasFee}
                  gasFeeAtomic={tx.gasFeeAtomic}
                  netAmount={tx.netAmount}
                  netAmountAtomic={tx.netAmountAtomic}
                  txHash={tx.txHash}
                  network={tx.network}
                />
              ))}
            </div>
          )}
        </Section>

        <window.AddAddressModal
          open={addAddressOpen}
          onClose={() => setAddAddressOpen(false)}
        />
      </div>
    );
  }

  function PendingDepositCard({ tx, t }) {
    return (
      <Card>
        <div className="smallcaps-mono" style={{ color: 'var(--accent-amber)', marginBottom: '10px' }}>
          PENDING TOP-UP
        </div>
        <div className="font-body" style={{ color: 'var(--ink-primary)', fontSize: '13px', marginBottom: '12px', overflowWrap: 'anywhere' }}>
          +{window.formatAmount(tx.amount)} USDC · {tx.counterparty}
        </div>
        <ol className="font-body" style={{ margin: 0, paddingLeft: '18px', color: 'var(--ink-secondary)', fontSize: '12px', lineHeight: 1.8 }}>
          <li>Exchange withdrawn</li>
          <li>Base confirming</li>
          <li>Crediting Gateway Wallet</li>
        </ol>
      </Card>
    );
  }

  // Small QR rendered next to the receive address. Uses the global
  // `qrcode` factory from qrcode-generator (loaded via CDN script tag in
  // mvp/dashboard.html). Renders as inline SVG so it scales crisp at any
  // size and stays self-contained for the standalone build.
  function QrPanel({ address, t }) {
    const qrAddress = String(address || '').trim();
    const svgMarkup = React.useMemo(() => {
      if (!qrAddress) return null;
      if (typeof window.qrcode !== 'function') return null;
      try {
        // typeNumber=0 → auto-fit; errorCorrection 'M' is the standard balance.
        const qr = window.qrcode(0, 'M');
        qr.addData(qrAddress);
        qr.make();
        // Fixed-size raster: cellSize=4 with margin=2 → ~120-140px square
        // for a typical 0x… address. Scalable mode (viewBox + width=100%)
        // collapses to 0×0 inside an inline-block parent — fixed size avoids
        // that, and the printed size is plenty for a phone camera to scan.
        return qr.createSvgTag({ cellSize: 4, margin: 2 });
      } catch {
        return null;
      }
    }, [qrAddress]);

    if (!svgMarkup) {
      // Library not loaded — fall back to nothing so the address column
      // still works on its own. (Standalone-build edge case insurance.)
      return null;
    }

    return (
      <div
        key={qrAddress}
        data-qr-address={qrAddress}
        style={{ textAlign: 'center', minWidth: '136px' }}
      >
        <div
          style={{
            display: 'inline-block',
            padding: '8px',
            background: 'var(--surface-paper)',
            border: '1px solid var(--stroke-hairline)',
            lineHeight: 0,
          }}
          // qrcode-generator returns a self-contained <svg> string. Inject
          // it directly to avoid the cost of an extra wrapper component.
          dangerouslySetInnerHTML={{ __html: svgMarkup }}
        />
        <div
          className="smallcaps-mono"
          style={{
            marginTop: '8px',
            fontSize: '9px',
            color: 'var(--ink-tertiary)',
            letterSpacing: '0.06em',
            lineHeight: 1.4,
            whiteSpace: 'pre-line',
          }}
        >
          {t('mvp.dash.funding.qr_caption')}
        </div>
      </div>
    );
  }

  function Section({ title, titleSuffix, description, children }) {
    return (
      <section style={{ marginTop: '48px', paddingTop: '32px', borderTop: '1px solid var(--stroke-rule)' }}>
        <div className="flex items-center" style={{ gap: '6px', marginBottom: '4px' }}>
          <span className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)' }}>
            {title}
          </span>
          {titleSuffix}
        </div>
        {description && (
          <div className="font-body" style={{ fontSize: '13px', color: 'var(--ink-secondary)', marginBottom: '24px', maxWidth: '720px' }}>
            {description}
          </div>
        )}
        {children}
      </section>
    );
  }

  function Card({ children }) {
    return (
      <div
        style={{
          padding: '24px',
          background: 'var(--surface-card)',
          border: '1px solid var(--stroke-rule)',
        }}
      >
        {children}
      </div>
    );
  }

  function TermLabel({ children }) {
    return (
      <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)', fontSize: '10px', alignSelf: 'center' }}>
        {children}
      </div>
    );
  }

  function TermValue({ children, accent }) {
    return (
      <div className="font-body" style={{ color: accent ? 'var(--accent-amber)' : 'var(--ink-primary)', fontSize: '12px', fontWeight: accent ? 500 : 400 }}>
        {children}
      </div>
    );
  }

  function chainName(chain) {
    switch ((chain || 'base').toLowerCase()) {
      case 'base':     return 'Base';
      case 'arbitrum': return 'Arbitrum';
      case 'optimism': return 'Optimism';
      case 'polygon':  return 'Polygon';
      case 'solana':   return 'Solana';
      case 'ethereum': return 'Ethereum';
      default:         return chain;
    }
  }

  function Field({ label, children }) {
    return (
      <div style={{ marginBottom: '20px' }}>
        <div
          className="smallcaps-mono"
          style={{ color: 'var(--ink-secondary)', marginBottom: '6px' }}
        >
          {label}
        </div>
        {children}
      </div>
    );
  }

  window.MvpFundingView = MvpFundingView;
})();
