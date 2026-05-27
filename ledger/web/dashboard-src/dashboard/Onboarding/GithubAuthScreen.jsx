// MVP onboarding · sign in via the real GitHub OAuth flow.
//
// The dashboard does not ask for an email directly. GitHub returns the owner
// identity to the ledger service, which stores it in an HttpOnly session cookie;
// MvpAppStateProvider hydrates the browser state from /auth/session.

(function () {
  function MvpGithubAuthScreen() {
    const t = window.useT();
    const params = React.useMemo(() => new URLSearchParams(window.location.search), []);
    const authError = params.get('auth_error');
    const returnTo = window.location.pathname + window.location.search;
    const githubLoginHref = `/auth/github/login?returnTo=${encodeURIComponent(returnTo)}`;

    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '48px',
          position: 'relative',
        }}
      >
        <div style={{ maxWidth: '440px', width: '100%' }}>
          <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)' }}>
            {t('mvp.dash.auth.slug')}
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
            {t('mvp.dash.auth.headline')}
          </h1>

          <div
            className="font-body"
            style={{
              marginTop: '16px',
              fontSize: '16px',
              lineHeight: 1.55,
              color: 'var(--ink-secondary)',
              maxWidth: '400px',
            }}
          >
            {t('mvp.dash.auth.subhead')}
          </div>

          <a
            href={githubLoginHref}
            className="font-body"
            style={{
              marginTop: '32px',
              padding: '12px 22px',
              background: '#1A1A1A',
              color: '#FFFFFF',
              border: '1px solid #1A1A1A',
              fontSize: '14px',
              fontWeight: 500,
              letterSpacing: '0.02em',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '10px',
              textDecoration: 'none',
            }}
          >
            <OctocatGlyph size={18} fill="#FFFFFF" />
            {t('mvp.dash.auth.github_button')}
          </a>

          {authError && (
            <div
              className="font-body"
              style={{
                marginTop: '18px',
                fontSize: '12px',
                color: 'var(--status-negative)',
                letterSpacing: '0.02em',
                lineHeight: 1.5,
              }}
            >
              {t('mvp.dash.auth.error')}
            </div>
          )}

          <div
            className="font-body"
            style={{
              marginTop: '40px',
              fontSize: '12px',
              color: 'var(--ink-tertiary)',
              lineHeight: 1.55,
              maxWidth: '380px',
            }}
          >
            {t('mvp.dash.auth.note')}
          </div>
        </div>
      </div>
    );
  }

  function ConsentModal({ t, authorizing, onAuthorize, onCancel }) {
    return (
      <div
        onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(13, 17, 23, 0.78)',
          zIndex: 200,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '32px',
        }}
      >
        <div
          style={{
            width: '420px',
            maxWidth: '100%',
            background: '#0D1117',          // GitHub dark canvas
            color: '#E6EDF3',                // GitHub default text
            border: '1px solid #30363D',
            borderRadius: '6px',
            boxShadow: '0 20px 60px rgba(0,0,0,0.45)',
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            overflow: 'hidden',
          }}
        >
          {/* Header bar with octocat */}
          <div
            style={{
              padding: '14px 20px',
              borderBottom: '1px solid #30363D',
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
            }}
          >
            <OctocatGlyph size={20} fill="#E6EDF3" />
            <span style={{ fontSize: '13px', fontWeight: 600, letterSpacing: '0.02em' }}>
              github.com
            </span>
            <span style={{ marginLeft: 'auto', fontSize: '11px', color: '#7D8590' }}>
              {t('mvp.dash.auth.consent_subdomain')}
            </span>
          </div>

          {/* Body */}
          <div style={{ padding: '24px 24px 20px' }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '18px',
                marginBottom: '18px',
              }}
            >
              <KovaloopMark />
              <div style={{ color: '#7D8590', fontSize: '18px', lineHeight: 1 }}>→</div>
              <div
                style={{
                  width: '40px',
                  height: '40px',
                  borderRadius: '50%',
                  background: '#21262D',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <OctocatGlyph size={22} fill="#E6EDF3" />
              </div>
            </div>

            <div
              style={{
                fontSize: '18px',
                fontWeight: 600,
                color: '#E6EDF3',
                textAlign: 'center',
                marginBottom: '8px',
                lineHeight: 1.3,
              }}
            >
              {t('mvp.dash.auth.consent_title')}
            </div>
            <div
              style={{
                fontSize: '12.5px',
                color: '#7D8590',
                textAlign: 'center',
                lineHeight: 1.55,
                marginBottom: '18px',
              }}
            >
              {t('mvp.dash.auth.consent_app')}
            </div>

            <div
              style={{
                background: '#161B22',
                border: '1px solid #30363D',
                borderRadius: '4px',
                padding: '12px 14px',
                marginBottom: '18px',
              }}
            >
              <div style={{ fontSize: '11px', color: '#7D8590', letterSpacing: '0.04em', textTransform: 'uppercase', marginBottom: '8px' }}>
                {t('mvp.dash.auth.consent_scope_label')}
              </div>
              <ScopeLine icon="✓">{t('mvp.dash.auth.consent_scope_profile')}</ScopeLine>
              <ScopeLine icon="✓">{t('mvp.dash.auth.consent_scope_email')}</ScopeLine>
            </div>

            <button
              type="button"
              onClick={onAuthorize}
              disabled={authorizing}
              style={{
                width: '100%',
                padding: '10px 16px',
                background: authorizing ? '#1F6F3E' : '#238636',  // GitHub green
                color: '#FFFFFF',
                border: '1px solid rgba(240, 246, 252, 0.10)',
                borderRadius: '6px',
                fontSize: '14px',
                fontWeight: 600,
                letterSpacing: '0.01em',
                cursor: authorizing ? 'wait' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '8px',
                fontFamily: 'inherit',
              }}
            >
              {authorizing && <Spinner />}
              {authorizing
                ? t('mvp.dash.auth.authorizing')
                : t('mvp.dash.auth.consent_authorize')}
            </button>

            <button
              type="button"
              onClick={onCancel}
              disabled={authorizing}
              style={{
                width: '100%',
                marginTop: '10px',
                padding: '8px 16px',
                background: 'transparent',
                color: authorizing ? '#484F58' : '#7D8590',
                border: 'none',
                fontSize: '13px',
                cursor: authorizing ? 'not-allowed' : 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {t('mvp.dash.auth.consent_cancel')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  function ScopeLine({ icon, children }) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: '8px',
          fontSize: '12.5px',
          color: '#E6EDF3',
          lineHeight: 1.6,
        }}
      >
        <span style={{ color: '#3FB950', fontSize: '12px', flexShrink: 0 }}>{icon}</span>
        <span>{children}</span>
      </div>
    );
  }

  // Kovaloop brand mark used inside the consent modal. Echoes the dashboard
  // chrome's wordmark style (Fraunces italic C) so users can verify which
  // app is asking for permission.
  function KovaloopMark() {
    return (
      <div
        style={{
          width: '40px',
          height: '40px',
          borderRadius: '50%',
          background: '#1A1A1A',
          color: '#F5F1EA',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: 'Fraunces, Georgia, serif',
          fontStyle: 'italic',
          fontSize: '22px',
          fontWeight: 500,
          letterSpacing: '-0.02em',
          lineHeight: 1,
        }}
      >
        C
      </div>
    );
  }

  function OctocatGlyph({ size = 20, fill = '#1A1A1A' }) {
    return (
      <svg
        viewBox="0 0 24 24"
        width={size}
        height={size}
        fill={fill}
        aria-hidden="true"
      >
        <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
      </svg>
    );
  }

  function Spinner() {
    // Inline CSS-only spinner — avoids any image / SVG-animation dependency.
    const sz = 13;
    return (
      <span
        aria-hidden="true"
        style={{
          width: sz,
          height: sz,
          borderRadius: '50%',
          border: '2px solid rgba(255,255,255,0.35)',
          borderTopColor: '#FFFFFF',
          display: 'inline-block',
          animation: 'mvpauth-spin 0.8s linear infinite',
          flexShrink: 0,
        }}
      />
    );
  }

  // Inject the spinner keyframes once.
  if (typeof document !== 'undefined' && !document.getElementById('mvpauth-spin-keyframes')) {
    const style = document.createElement('style');
    style.id = 'mvpauth-spin-keyframes';
    style.textContent = '@keyframes mvpauth-spin { to { transform: rotate(360deg); } }';
    document.head.appendChild(style);
  }

  window.MvpGithubAuthScreen = MvpGithubAuthScreen;
})();
