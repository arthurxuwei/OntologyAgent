// MVP Portfolio — multi-agent overview grid. Each card surfaces the agent's
// balance and a one-line activity hint; click → set active + jump to Overview.
// "+ Add agent" is the explicit affordance for the multi-agent flow.

(function () {
  function MvpPortfolioView({ effectiveRoster, displayedAgents, onSelectAgent, onAddAgent }) {
    const t = window.useT();
    const agentIds = displayedAgents || [];

    return (
      <div>
        <div className="flex items-baseline justify-between" style={{ marginBottom: '32px' }}>
          <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)' }}>
            {t('mvp.dash.portfolio.slug')}
          </div>
          <button
            type="button"
            onClick={onAddAgent}
            className="font-body"
            style={{
              padding: '10px 18px',
              background: 'transparent',
              border: '1px solid var(--stroke-rule)',
              color: 'var(--ink-primary)',
              fontSize: '13px',
              cursor: 'pointer',
              letterSpacing: '0.02em',
            }}
          >
            + {t('mvp.dash.portfolio.add_agent')}
          </button>
        </div>

        {agentIds.length === 0 ? (
          <div
            className="font-body"
            style={{
              padding: '64px 24px',
              textAlign: 'center',
              color: 'var(--ink-tertiary)',
              border: '1px dashed var(--stroke-rule)',
            }}
          >
            {t('mvp.dash.portfolio.empty')}
          </div>
        ) : (
          <div
            className="grid"
            style={{
              gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))',
              gap: '20px',
            }}
          >
            {agentIds.map((id) => (
              <AgentCard
                key={id}
                id={id}
                data={effectiveRoster[id]}
                pendingSettlement={effectiveRoster[id].balance?.pendingSettlement || 0}
                onSelect={() => onSelectAgent(id)}
                t={t}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  function AgentCard({ id, data, pendingSettlement = 0, onSelect, t }) {
    if (!data) return null;
    const { agent, balance, transactions } = data;
    const txCount = transactions ? transactions.length : 0;
    const lastTx = transactions && transactions[0];

    return (
      <button
        type="button"
        onClick={onSelect}
        style={{
          padding: '24px',
          background: 'var(--surface-card)',
          border: '1px solid var(--stroke-rule)',
          textAlign: 'left',
          cursor: 'pointer',
          transition: 'border-color 120ms',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-amber)'; }}
        onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--stroke-rule)'; }}
      >
        <div className="flex items-baseline justify-between" style={{ marginBottom: '4px' }}>
          <div className="font-display" style={{ fontSize: '24px', color: 'var(--ink-primary)' }}>
            {agent.name}
          </div>
          <div
            className="font-mono"
            style={{ fontSize: '10px', color: 'var(--ink-tertiary)', letterSpacing: '0.06em' }}
          >
            {agent.claimedDaysAgo === 0
              ? t('mvp.dash.portfolio.just_claimed')
              : `${agent.claimedDaysAgo}d`}
          </div>
        </div>
        <div className="font-body" style={{ fontSize: '13px', color: 'var(--ink-secondary)', marginBottom: '20px' }}>
          {agent.role}
        </div>
        <div
          className="font-mono"
          style={{ fontSize: '28px', color: 'var(--ink-primary)', letterSpacing: '-0.01em' }}
        >
          {window.formatAmount(balance.available)}
          <span className="font-mono" style={{ fontSize: '11px', color: 'var(--ink-tertiary)', marginLeft: '6px' }}>
            USDC
          </span>
        </div>
        <window.PendingBalanceLine
          amount={pendingSettlement}
          size="sm"
        />
        <div
          className="font-mono"
          style={{
            fontSize: '11px',
            color: 'var(--ink-tertiary)',
            marginTop: '16px',
            letterSpacing: '0.04em',
          }}
        >
          {txCount === 0
            ? t('mvp.dash.portfolio.no_activity')
            : `${txCount} ${t('mvp.dash.portfolio.tx_suffix')} · ${lastTx.timestamp}`}
        </div>
      </button>
    );
  }

  window.MvpPortfolioView = MvpPortfolioView;
})();
