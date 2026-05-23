// MVP ClaimForm fused with the real ledger dashboard: the surface keeps the
// prototype's validate/confirm shape, but candidates come from the logged-in
// claim code via /dashboard/claimable-agents.

(function () {
  function ClaimForm({ mode = 'initial', onClaimed, onDismiss }) {
    const t = window.useT();
    const { agents: claimedAgents, claimAgent, ownerEmail, currentUser, resetAll, claimToken } = window.useAppState();
    const [candidates, setCandidates] = React.useState([]);
    const [status, setStatus] = React.useState('loading');
    const [code, setCode] = React.useState(() =>
      ((mode === 'initial' || mode === 'add') && claimToken) ? claimToken : ''
    );
    const [step, setStep] = React.useState('input');
    const [matched, setMatched] = React.useState(null);
    const [errorKey, setErrorKey] = React.useState('');

    React.useEffect(() => {
      let cancelled = false;
      setStatus('loading');
      fetch('/dashboard/claimable-agents')
        .then((response) => {
          if (!response.ok) throw new Error(`claimable agents ${response.status}`);
          return response.json();
        })
        .then((payload) => {
          if (cancelled) return;
          const next = Array.isArray(payload.agents) ? payload.agents : [];
          setCandidates(next);
          setStatus(next.length ? 'ready' : 'empty');
        })
        .catch(() => {
          if (cancelled) return;
          setCandidates([]);
          setStatus('error');
        });
      return () => { cancelled = true; };
    }, [claimedAgents]);

    const trimmedCode = code.trim();
    const canValidate = trimmedCode.length > 0 && status !== 'loading';
    const handleValidate = () => {
      if (!canValidate) return;
      if (status === 'loading') {
        setMatched(null);
        setErrorKey('mvp.dash.claim.error_loading');
        setStep('input');
        return;
      }
      if (status === 'error') {
        setMatched(null);
        setErrorKey('mvp.dash.claim.error_load_failed');
        setStep('input');
        return;
      }
      const lowered = trimmedCode.toLowerCase();
      const next = candidates.find((candidate) => (
        String(candidate.claimCode || '').trim().toLowerCase() === lowered
      ));
      if (!next) {
        setMatched(null);
        setErrorKey('mvp.dash.claim.error_not_found');
        setStep('input');
        return;
      }
      if (claimedAgents.includes(next.agentId)) {
        setMatched(null);
        setErrorKey('mvp.dash.claim.error_already_claimed');
        setStep('input');
        return;
      }
      setErrorKey('');
      setMatched(next);
      setStep('confirm');
    };

    const handleClaim = (candidate) => {
      if (!candidate) return;
      fetch('/dashboard/claims', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agentId: candidate.agentId,
          claimCode: trimmedCode,
          email: ownerEmail,
        }),
      })
        .then((response) => {
          if (response.status === 403) throw new Error('owner_mismatch');
          if (!response.ok) throw new Error(`dashboard claim ${response.status}`);
          return response.json();
        })
        .then(() => {
          claimAgent(candidate.agentId);
          const cleanUrl = new URL(window.location.href);
          cleanUrl.searchParams.delete('claimCode');
          cleanUrl.searchParams.delete('claimcode');
          cleanUrl.searchParams.delete('claim_code');
          cleanUrl.searchParams.delete('code');
          cleanUrl.searchParams.delete('agentId');
          window.history.replaceState({}, '', cleanUrl.toString());
          if (onClaimed) onClaimed(candidate.agentId);
        })
        .catch((error) => {
          setErrorKey(error.message === 'owner_mismatch'
            ? 'mvp.dash.claim.error_owner_mismatch'
            : 'mvp.dash.claim.error_load_failed');
          setStep('input');
        });
    };

    const handleUseDifferent = () => {
      setStep('input');
      setMatched(null);
      setCode('');
      setErrorKey('');
    };

    const handleReturnToRegistration = () => {
      setStep('input');
      setMatched(null);
      setCode('');
      setErrorKey('');
      if (resetAll) resetAll();
    };

    if (step === 'confirm' && matched) {
      return (
        <ConfirmStep
          t={t}
          candidate={matched}
          code={trimmedCode}
          ownerHandle={ownerHandleFor(currentUser) || ownerEmail}
          onClaim={() => handleClaim(matched)}
          onUseDifferent={handleUseDifferent}
        />
      );
    }

    return (
      <InputStep
        t={t}
        code={code}
        onCodeChange={(value) => {
          setCode(value);
          if (errorKey) setErrorKey('');
        }}
        onValidate={handleValidate}
        canValidate={canValidate}
        errorKey={errorKey}
        status={status}
        ownerEmail={ownerEmail}
        mode={mode}
        onReturnToRegistration={handleReturnToRegistration}
      />
    );
  }

  function InputStep({ t, code, onCodeChange, onValidate, canValidate, errorKey, status, ownerEmail, mode, onReturnToRegistration }) {
    const handleKey = (e) => { if (e.key === 'Enter' && canValidate) onValidate(); };
    return (
      <>
        <div className="font-body" style={{ fontSize: '13px', color: 'var(--ink-secondary)', lineHeight: 1.55, marginBottom: '16px' }}>
          {t(mode === 'initial' ? 'mvp.dash.claim.input_help_initial' : 'mvp.dash.claim.input_help_add')}
        </div>
        <div className="smallcaps-mono" style={{ color: 'var(--ink-secondary)', marginBottom: '6px', fontSize: '10px' }}>
          {t('mvp.dash.claim.code_label')}
        </div>
        <input
          type="text"
          value={code}
          onChange={(e) => onCodeChange(e.target.value)}
          onKeyDown={handleKey}
          placeholder={t('mvp.dash.claim.code_placeholder')}
          autoFocus
          className="font-mono"
          style={{ width: '100%', padding: '10px 14px', border: `1px solid ${errorKey ? 'var(--status-negative)' : 'var(--stroke-rule)'}`, background: 'var(--surface-paper)', fontSize: '13px', color: 'var(--ink-primary)', outline: 'none', letterSpacing: '0.02em' }}
        />
        {errorKey && (
          <div className="font-body" style={{ marginTop: '6px', fontSize: '11px', color: 'var(--status-negative)' }}>{t(errorKey)}</div>
        )}
        <div style={{ marginTop: '18px' }}>
          <button type="button" onClick={onValidate} disabled={!canValidate} className="font-body" style={{ padding: '10px 22px', background: canValidate ? 'var(--ink-primary)' : 'var(--stroke-hairline)', border: `1px solid ${canValidate ? 'var(--ink-primary)' : 'var(--stroke-rule)'}`, color: canValidate ? 'var(--ink-inverse)' : 'var(--ink-tertiary)', fontSize: '13px', cursor: canValidate ? 'pointer' : 'not-allowed', letterSpacing: '0.02em' }}>
            {t('mvp.dash.claim.validate_button')} →
          </button>
        </div>
        {status === 'empty' && (
          <div className="font-body" style={{ marginTop: '18px', fontSize: '12px', color: 'var(--ink-tertiary)', lineHeight: 1.5 }}>
            {t('mvp.dash.claim.no_agents')}
            <div style={{ marginTop: '10px' }}>
              <UseDifferentButton onClick={onReturnToRegistration}>{t('dash.claim.return_to_registration')}</UseDifferentButton>
            </div>
          </div>
        )}
      </>
    );
  }

  function ConfirmStep({ t, candidate, code, ownerHandle, onClaim, onUseDifferent }) {
    const truncatedCode = code.length > 22
      ? `${code.slice(0, 14)}…${code.slice(-4)}`
      : code;
    const agentLabel = candidate.dashboard && candidate.dashboard.agent && candidate.dashboard.agent.role
      ? `${candidate.agentName || candidate.agentId} · ${candidate.dashboard.agent.role}`
      : candidate.agentName || candidate.agentId;
    return (
      <>
        <div className="font-body" style={{ display: 'inline-block', padding: '4px 10px', background: 'rgba(45, 95, 63, 0.08)', border: '1px solid rgba(45, 95, 63, 0.32)', color: 'var(--status-positive)', fontSize: '11px', letterSpacing: '0.04em', marginBottom: '20px' }}>
          ✓ {t('mvp.dash.claim.confirm_validated')}
        </div>
        <div style={{ padding: '20px 22px', background: 'var(--surface-card)', border: '1px solid var(--stroke-hairline)' }}>
          <Row label={t('mvp.dash.claim.confirm_agent_label')} value={agentLabel} />
          <Row label={t('mvp.dash.claim.confirm_wallet_label')} value={candidate.displayWalletAddress || candidate.walletAddress || '-'} mono />
          <Row label={t('mvp.dash.claim.confirm_owner_label')} value={ownerHandle || candidate.ownerEmail || '-'} mono />
          <Row label={t('mvp.dash.claim.confirm_code_label')} value={truncatedCode} mono last />
        </div>
        <div style={{ marginTop: '22px', display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
          <button type="button" onClick={onClaim} className="font-body" style={{ padding: '12px 24px', background: 'var(--accent-amber)', color: 'var(--ink-inverse)', border: 'none', fontSize: '14px', fontWeight: 500, letterSpacing: '0.02em', cursor: 'pointer', boxShadow: '0 0 0 4px var(--accent-amber-soft)' }}>
            {t('mvp.dash.claim.claim_button')}
          </button>
          <UseDifferentButton onClick={onUseDifferent}>{t('mvp.dash.claim.use_different')}</UseDifferentButton>
        </div>
      </>
    );
  }

  function UseDifferentButton({ children, onClick }) {
    return <button type="button" onClick={onClick} className="font-body" style={{ background: 'transparent', border: 'none', padding: '4px 0', color: 'var(--ink-tertiary)', fontSize: '13px', cursor: 'pointer', textDecoration: 'underline', textDecorationColor: 'var(--stroke-rule)', textUnderlineOffset: '4px' }}>{children}</button>;
  }

  function Row({ label, value, mono, last }) {
    return (
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '14px', padding: '8px 0', borderBottom: last ? 'none' : '1px solid var(--stroke-hairline)' }}>
        <span className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)', fontSize: '10px', letterSpacing: '0.08em', whiteSpace: 'nowrap' }}>{label}</span>
        <span className={mono ? 'font-mono' : 'font-body'} style={{ fontSize: mono ? '12px' : '13px', color: 'var(--ink-primary)', textAlign: 'right', wordBreak: 'break-all' }}>{value}</span>
      </div>
    );
  }

  function ownerHandleFor(user) {
    if (!user) return '';
    if (user.provider === 'github' && user.login) return '@' + user.login;
    return user.email || user.login || '';
  }

  window.ClaimForm = ClaimForm;
})();
