// MVP Transactions — flat ledger of every transaction for the active agent.
// No filtering / search in MVP (the dataset is small); each row reuses the
// shared TransactionRow component from the main project.

(function () {
  function MvpTransactionsView({ data }) {
    const t = window.useT();
    if (!data) return null;
    const { agent, transactions } = data;
    const txs = transactions || [];

    return (
      <div>
        <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)', marginBottom: '24px' }}>
          {t('mvp.dash.transactions.slug')} · {agent.name}
        </div>

        {txs.length === 0 ? (
          <div
            className="font-body"
            style={{
              padding: '64px 24px',
              textAlign: 'center',
              color: 'var(--ink-tertiary)',
              border: '1px dashed var(--stroke-rule)',
            }}
          >
            {t('mvp.dash.transactions.empty')}
          </div>
        ) : (
          <>
            <div className="rule-t" />
            {txs.map((tx) => (
              <window.TransactionRow
                key={tx.id}
                receiptId={tx.id}
                counterparty={tx.counterparty}
                amount={tx.amount}
                direction={tx.direction}
                status={tx.status}
                timestamp={tx.timestamp}
                countdown={tx.countdown}
                role={tx.role}
                gasFee={tx.gasFee}
                gasFeeAtomic={tx.gasFeeAtomic}
                netAmount={tx.netAmount}
                netAmountAtomic={tx.netAmountAtomic}
                txHash={tx.txHash}
                network={tx.network}
              />
            ))}
            <div
              className="font-mono"
              style={{
                marginTop: '32px',
                fontSize: '11px',
                color: 'var(--ink-tertiary)',
                letterSpacing: '0.04em',
              }}
            >
              {txs.length} {t('mvp.dash.transactions.row_count_suffix')}
            </div>
          </>
        )}
      </div>
    );
  }

  window.MvpTransactionsView = MvpTransactionsView;
})();
