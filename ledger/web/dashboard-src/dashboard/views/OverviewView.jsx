// MVP Overview — single-agent landing page. View-only (no CTAs to operate);
// the operate affordances (Add funds / Withdraw) live in the Funding tab.
//
// Layout:
//   - Big balance number (USDC)
//   - 2-up KPI row (lifetime in / out).
//   - Recent 5 transactions (full list lives in Transactions tab)
//   - "View all →" link to jump to Transactions

(function () {
  function MvpOverviewView({ data, ownerEmail, onJumpTo }) {
    const t = window.useT();
    if (!data) return null;
    const { agent, balance, transactions } = data;
    const recent = (transactions || []).slice(0, 5);
    const isEmpty = recent.length === 0;

    return (
      <div>
        <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)', marginBottom: '8px' }}>
          {t('mvp.dash.overview.slug')} · {agent.name}
        </div>

        {/* Balance */}
        <div className="font-display" style={{ fontSize: '12px', color: 'var(--ink-tertiary)', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: '4px' }}>
          {t('mvp.dash.overview.balance_label')}
        </div>
        <div className="font-mono" style={{ fontSize: '56px', color: 'var(--ink-primary)', letterSpacing: '-0.02em', lineHeight: 1 }}>
          {window.formatAmount(balance.available)}
          <span style={{ fontSize: '18px', color: 'var(--ink-tertiary)', marginLeft: '12px', letterSpacing: '0.04em' }}>USDC</span>
        </div>
        <window.PendingBalanceLine
          amount={balance.pendingSettlement || 0}
        />

        {/* KPI row */}
        <div
          className="grid"
          style={{
            gridTemplateColumns: 'repeat(2, minmax(0, 280px))',
            gap: '48px',
            marginTop: '40px',
            paddingTop: '24px',
            borderTop: '1px solid var(--stroke-hairline)',
          }}
        >
          <Kpi label={t('mvp.dash.overview.kpi_in')}  value={window.formatAmount(balance.lifetimeIn)} />
          <Kpi label={t('mvp.dash.overview.kpi_out')} value={window.formatAmount(balance.lifetimeOut)} />
        </div>

        {/* Recent transactions */}
        <div style={{ marginTop: '48px' }}>
          <div className="flex items-baseline justify-between" style={{ marginBottom: '12px' }}>
            <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)' }}>
              {t('mvp.dash.overview.recent_label')}
            </div>
            {!isEmpty && (
              <button
                type="button"
                onClick={() => onJumpTo && onJumpTo('transactions')}
                className="font-body"
                style={{
                  background: 'transparent',
                  border: 'none',
                  fontSize: '12px',
                  color: 'var(--accent-amber)',
                  cursor: 'pointer',
                  letterSpacing: '0.02em',
                }}
              >
                {t('mvp.dash.overview.view_all')} →
              </button>
            )}
          </div>
          <div className="rule-t" />
          {isEmpty ? (
            <div
              className="font-body"
              style={{
                padding: '48px 0',
                textAlign: 'center',
                color: 'var(--ink-tertiary)',
                fontSize: '13px',
              }}
            >
              {t('mvp.dash.overview.empty')}
            </div>
          ) : (
            recent.map((tx) => (
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
            ))
          )}
        </div>
      </div>
    );
  }

  function Kpi({ label, value }) {
    return (
      <div>
        <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)', marginBottom: '4px' }}>
          {label}
        </div>
        <div className="font-mono" style={{ fontSize: '20px', color: 'var(--ink-primary)', letterSpacing: '-0.01em' }}>
          {value}
        </div>
      </div>
    );
  }

  window.MvpOverviewView = MvpOverviewView;
})();
