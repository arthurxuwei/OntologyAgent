// Three-state toggle that swaps mock data underneath the dashboard.
// Sits in the right edge of the nav row. Mono small-caps styling matches
// the editorial signature; active option is amber-underlined.

function MockStateToggle() {
  const t = window.useT();
  const { mockState, setMockState } = window.useAppState();
  const states = [
    { id: 'empty',  label: t('dash.mockstate.empty') },
    { id: 'day1',   label: t('dash.mockstate.day1') },
    { id: 'mature', label: t('dash.mockstate.mature') },
  ];

  return (
    <div
      className="flex items-baseline"
      style={{ gap: '14px' }}
      role="tablist"
      aria-label="Mock state"
    >
      <span
        className="smallcaps-mono"
        style={{ color: 'var(--ink-tertiary)', fontSize: '10px' }}
      >
        {t('dash.mockstate.label')}
      </span>
      {states.map((s) => {
        const active = s.id === mockState;
        return (
          <button
            key={s.id}
            type="button"
            onClick={() => setMockState(s.id)}
            className="smallcaps-mono"
            style={{
              background: 'transparent',
              border: 'none',
              padding: '2px 0',
              cursor: 'pointer',
              fontSize: '10px',
              color: active ? 'var(--accent-amber)' : 'var(--ink-secondary)',
              borderBottom: active
                ? '1px solid var(--accent-amber)'
                : '1px solid transparent',
              transition: 'color 120ms, border-color 120ms',
            }}
            aria-pressed={active}
          >
            {s.label}
          </button>
        );
      })}
    </div>
  );
}

window.MockStateToggle = MockStateToggle;
