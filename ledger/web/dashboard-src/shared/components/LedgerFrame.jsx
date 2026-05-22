// Signature visual: persistent horizontal rule across every stage at top:96px.
// Right side carries: stage index (e.g. 04 / 16) + lang switcher (EN | 中).

function LangSwitcher() {
  const { lang, setLang } = window.useLang();
  const btn = (code, label) => {
    const active = lang === code;
    return (
      <button
        type="button"
        onClick={() => setLang(code)}
        style={{
          background: 'transparent',
          border: 'none',
          padding: '2px 0',
          margin: 0,
          cursor: 'pointer',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: '11px',
          letterSpacing: '0.08em',
          color: active ? 'var(--accent-amber)' : 'var(--ink-tertiary)',
          borderBottom: active ? '1px solid var(--accent-amber)' : '1px solid transparent',
          transition: 'color 120ms ease, border-color 120ms ease',
        }}
        aria-pressed={active}
      >
        {label}
      </button>
    );
  };
  return (
    <div className="flex items-center" style={{ gap: '8px' }}>
      {btn('en', 'EN')}
      <span style={{ color: 'var(--ink-tertiary)', fontFamily: "'JetBrains Mono', monospace", fontSize: '11px' }}>|</span>
      {btn('zh', '中')}
    </div>
  );
}

function LedgerFrame({ stageLabel, stageNumber, totalStages, children, bare }) {
  // `bare` skips the rule (used by M1 pre-roll, M5.3 step-out, M6.2 closing).
  // `stageNumber` and `totalStages` props are kept for source compatibility
  // with the 16 base stages but ignored — actual values come from the engine
  // so internal-mode toggling reflects in 7 / 24 etc. without touching stages.
  const { currentStageIndex, totalStages: engineTotal, internalMode } =
    window.useDemoEngine();
  const padded = String(currentStageIndex + 1).padStart(2, '0');
  const total = String(engineTotal).padStart(2, '0');

  return (
    <div className="relative w-full" style={{ minHeight: 'calc(100vh - 32px)' }}>
      {!bare && (
        <div
          className="absolute left-0 right-0"
          style={{ top: '96px' }}
        >
          <div className="flex items-end justify-between pb-3" style={{ gap: '32px', padding: '0 48px' }}>
            <div className="smallcaps-mono" style={{ color: 'var(--ink-secondary)', whiteSpace: 'nowrap' }}>
              {stageLabel}
            </div>
            <div className="flex items-center" style={{ gap: '12px' }}>
              {internalMode && (
                <span
                  className="font-mono"
                  style={{
                    fontSize: '10px',
                    letterSpacing: '0.1em',
                    color: 'var(--ink-tertiary)',
                    whiteSpace: 'nowrap',
                  }}
                >
                  [INTERNAL]
                </span>
              )}
              <div className="font-mono text-xs" style={{ color: 'var(--ink-tertiary)', whiteSpace: 'nowrap' }}>
                {padded} / {total}
              </div>
            </div>
          </div>
          <div style={{ height: '1px', background: 'var(--stroke-rule)' }} />
        </div>
      )}
      {children}
    </div>
  );
}

window.LangSwitcher = LangSwitcher;
window.LedgerFrame = LedgerFrame;
