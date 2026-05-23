// MVP · ClaimScreen — fullscreen wrapper around ClaimForm for the very
// first claim (right after GitHub sign-in, before any agent exists). Mirrors
// the editorial typography of GithubAuthScreen so the onboarding flow feels
// like one continuous surface, not two unrelated pages.
//
// Forks the main project's ClaimScreen (which uses the legacy "click to
// claim agentA" flow without any code input) — main keeps the older
// behaviour for its own dashboard demo, MVP routes here via DashboardApp.

(function () {
  function MvpClaimScreen() {
    const t = window.useT();

    const handleClaimed = () => {
      // claimAgent already fired inside ClaimForm; DashboardRouter sees
      // agents.length > 0 on the next render and moves to DashboardSurface.
      // No additional navigation needed here.
    };

    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '48px',
        }}
      >
        <div style={{ maxWidth: '520px', width: '100%' }}>
          <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)' }}>
            {t('mvp.dash.claim.slug')}
          </div>

          <h1
            className="font-display italic"
            style={{
              fontSize: '44px',
              fontWeight: 400,
              lineHeight: 1.05,
              letterSpacing: '-0.025em',
              margin: '20px 0 0',
              color: 'var(--ink-primary)',
            }}
          >
            {t('mvp.dash.claim.initial_headline')}
          </h1>

          <div
            className="font-body"
            style={{
              marginTop: '14px',
              marginBottom: '28px',
              fontSize: '15px',
              lineHeight: 1.55,
              color: 'var(--ink-secondary)',
              maxWidth: '440px',
            }}
          >
            {t('mvp.dash.claim.initial_subhead')}
          </div>

          <window.ClaimForm mode="initial" onClaimed={handleClaimed} />
        </div>
      </div>
    );
  }

  window.MvpClaimScreen = MvpClaimScreen;
})();
