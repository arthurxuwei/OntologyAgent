// MVP · simulated Coinbase Onramp modal.
//
// Not the real Coinbase widget — we don't load their SDK in MVP. This is a
// stylised stand-in that hits the visual beats users will recognise from
// the real thing: Coinbase blue header + wordmark, payment-method picker
// (Apple Pay / Card / Bank), a USD amount input that converts to USDC at a
// fixed 1:1 (plus a transparent fee line), and a Pay button.
//
// State machine: form → processing → success → close. On success the
// caller's `onComplete(usdcAmount, sourceLabel)` is invoked so FundingView
// can credit the local balance + prepend a transaction row.

(function () {
  function CoinbaseOnrampModal({ open, onClose, agentId, destinationAddress, t }) {
    const [amount, setAmount] = React.useState('25');
    const [method, setMethod] = React.useState('apple_pay');
    const [stage, setStage]   = React.useState('form'); // form | processing | success | error
    const [errorMessage, setErrorMessage] = React.useState('');

    // Reset state when reopening so a previous run doesn't bleed in.
    React.useEffect(() => {
      if (open) {
        setAmount('25');
        setMethod('apple_pay');
        setStage('form');
        setErrorMessage('');
      }
    }, [open]);

    if (!open) return null;

    // Pricing: $1.00/USDC, plus a 1.99% fee (typical for card onramps).
    // Numbers are rounded for display; the credited USDC excludes the fee.
    const amountNum   = parseFloat(amount) || 0;
    const feePct      = 0.0199;
    const fee         = amountNum * feePct;
    const usdcReceive = Math.max(0, amountNum - fee);
    const validAmount = amountNum >= 5 && amountNum <= 1000;

    const handlePay = async () => {
      if (!validAmount) return;
      if (!agentId || !/^0x[0-9a-fA-F]{40}$/.test(String(destinationAddress || '').trim())) {
        setErrorMessage(t('mvp.dash.onramp.error_body'));
        setStage('error');
        return;
      }
      setStage('processing');
      setErrorMessage('');
      let coinbaseWindow = null;
      try {
        coinbaseWindow = window.open('about:blank', '_blank');
      } catch {
        coinbaseWindow = null;
      }
      try {
        const response = await fetch('/onramp/sessions', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            agentId,
            destinationAddress,
            paymentAmount: amountNum.toFixed(2),
            idempotencyKey: `dashboard-${agentId}-${Date.now()}`,
          }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload?.detail || `HTTP ${response.status}`);
        }
        if (!payload?.onrampUrl) {
          throw new Error('Coinbase session did not include an onramp URL');
        }
        if (coinbaseWindow) {
          coinbaseWindow.location.href = payload.onrampUrl;
        } else {
          window.open(payload.onrampUrl, '_blank', 'noopener,noreferrer');
        }
        setStage('success');
      } catch (error) {
        if (coinbaseWindow) {
          coinbaseWindow.close();
        }
        setErrorMessage(error?.message || t('mvp.dash.onramp.error_body'));
        setStage('error');
      }
    };

    return (
      <div
        onClick={(e) => { if (e.target === e.currentTarget && stage === 'form') onClose(); }}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(26, 26, 26, 0.55)',
          zIndex: 100,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '32px',
        }}
      >
        <div
          style={{
            width: '440px',
            maxWidth: '100%',
            background: '#FFFFFF',
            borderRadius: '12px',
            overflow: 'hidden',
            boxShadow: '0 24px 64px rgba(0,0,0,0.24)',
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          }}
        >
          {/* Header — Coinbase blue */}
          <div
            style={{
              background: '#0052FF',
              color: '#FFFFFF',
              padding: '20px 24px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <CoinbaseWordmark />
            {stage !== 'processing' && (
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: '#FFFFFF',
                  fontSize: '20px',
                  lineHeight: 1,
                  cursor: 'pointer',
                  padding: '4px 8px',
                  opacity: 0.85,
                }}
              >
                ×
              </button>
            )}
          </div>

          {/* Body */}
          <div style={{ padding: '24px' }}>
            {stage === 'form' && (
              <FormState
                t={t}
                amount={amount}
                setAmount={setAmount}
                method={method}
                setMethod={setMethod}
                fee={fee}
                usdcReceive={usdcReceive}
                validAmount={validAmount}
                onPay={handlePay}
              />
            )}
            {stage === 'processing' && (
              <CenteredMessage
                title={t('mvp.dash.onramp.processing_title')}
                subtitle={t('mvp.dash.onramp.processing_body')}
              />
            )}
            {stage === 'success' && (
              <CenteredMessage
                title={`✓ ${t('mvp.dash.onramp.success_title')}`}
                subtitle={t('mvp.dash.onramp.success_body')}
                accent="#0E8345"
              />
            )}
            {stage === 'error' && (
              <CenteredMessage
                title={t('mvp.dash.onramp.error_title')}
                subtitle={errorMessage || t('mvp.dash.onramp.error_body')}
                accent="#7A2620"
              />
            )}
          </div>
        </div>
      </div>
    );
  }

  function FormState({ t, amount, setAmount, method, setMethod, fee, usdcReceive, validAmount, onPay }) {
    return (
      <>
        {/* Amount input */}
        <div style={{ marginBottom: '20px' }}>
          <Label>{t('mvp.dash.onramp.amount_label')}</Label>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              background: '#F5F6F8',
              borderRadius: '8px',
              padding: '14px 16px',
              gap: '8px',
            }}
          >
            <input
              type="text"
              value={amount}
              onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ''))}
              style={{
                flex: 1,
                background: 'transparent',
                border: 'none',
                fontSize: '24px',
                fontWeight: 500,
                color: '#0A0B0D',
                outline: 'none',
                fontFamily: 'inherit',
              }}
              inputMode="decimal"
            />
            <span style={{ fontSize: '13px', color: '#8A94A6', fontWeight: 500 }}>USD</span>
          </div>
          <SubHint>{t('mvp.dash.onramp.min_max_hint')}</SubHint>
        </div>

        {/* You receive (USDC) */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            padding: '12px 16px',
            background: '#F5F6F8',
            borderRadius: '8px',
            marginBottom: '20px',
          }}
        >
          <div>
            <div style={{ fontSize: '11px', color: '#5B6B79', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
              {t('mvp.dash.onramp.receive_label')}
            </div>
            <div style={{ fontSize: '20px', fontWeight: 500, color: '#0A0B0D', marginTop: '2px' }}>
              {usdcReceive.toFixed(2)} <span style={{ fontSize: '13px', color: '#5B6B79', fontWeight: 400 }}>USDC</span>
            </div>
          </div>
          <div style={{ fontSize: '11px', color: '#5B6B79' }}>
            {t('mvp.dash.onramp.fee_label')}: {fee.toFixed(2)}
          </div>
        </div>

        {/* Payment method */}
        <div style={{ marginBottom: '24px' }}>
          <Label>{t('mvp.dash.onramp.payment_method_label')}</Label>
          {[
            { id: 'apple_pay',  label: t('mvp.dash.onramp.method_apple_pay'),  meta: '••••2847' },
            { id: 'card',       label: t('mvp.dash.onramp.method_card'),       meta: 'Visa ••••3119' },
            { id: 'bank',       label: t('mvp.dash.onramp.method_bank'),       meta: t('mvp.dash.onramp.method_bank_meta') },
          ].map((m) => (
            <PaymentRow
              key={m.id}
              active={method === m.id}
              onClick={() => setMethod(m.id)}
              label={m.label}
              meta={m.meta}
            />
          ))}
        </div>

        {/* Network reminder */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: '12px',
            color: '#5B6B79',
            padding: '10px 0',
            borderTop: '1px solid #E7E9EE',
            borderBottom: '1px solid #E7E9EE',
            marginBottom: '20px',
          }}
        >
          <span>{t('mvp.dash.onramp.network_label')}</span>
          <span style={{ color: '#0A0B0D' }}>Base</span>
        </div>

        {/* CTA */}
        <button
          type="button"
          onClick={onPay}
          disabled={!validAmount}
          style={{
            width: '100%',
            background: validAmount ? '#0052FF' : '#C5C7CD',
            color: '#FFFFFF',
            border: 'none',
            borderRadius: '8px',
            padding: '16px',
            fontSize: '15px',
            fontWeight: 600,
            cursor: validAmount ? 'pointer' : 'not-allowed',
            fontFamily: 'inherit',
          }}
        >
          {t('mvp.dash.onramp.pay_button').replace('{amount}', amount || '0')}
        </button>
      </>
    );
  }

  function PaymentRow({ active, onClick, label, meta }) {
    return (
      <button
        type="button"
        onClick={onClick}
        style={{
          width: '100%',
          background: 'transparent',
          border: `1px solid ${active ? '#0052FF' : '#E7E9EE'}`,
          borderRadius: '8px',
          padding: '12px 14px',
          marginTop: '8px',
          cursor: 'pointer',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          fontFamily: 'inherit',
          textAlign: 'left',
        }}
      >
        <span style={{ fontSize: '14px', fontWeight: 500, color: '#0A0B0D' }}>{label}</span>
        <span style={{ fontSize: '12px', color: '#5B6B79' }}>{meta}</span>
      </button>
    );
  }

  function CenteredMessage({ title, subtitle, accent }) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 16px' }}>
        <div
          style={{
            fontSize: '20px',
            fontWeight: 600,
            color: accent || '#0A0B0D',
            marginBottom: '8px',
          }}
        >
          {title}
        </div>
        <div style={{ fontSize: '14px', color: '#5B6B79', lineHeight: 1.55 }}>
          {subtitle}
        </div>
      </div>
    );
  }

  function Label({ children }) {
    return (
      <div
        style={{
          fontSize: '11px',
          color: '#5B6B79',
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
          marginBottom: '6px',
          fontWeight: 500,
        }}
      >
        {children}
      </div>
    );
  }

  function SubHint({ children }) {
    return (
      <div style={{ marginTop: '6px', fontSize: '11px', color: '#8A94A6' }}>
        {children}
      </div>
    );
  }

  // Inline SVG wordmark — uses the canonical Coinbase blue circle + 'coinbase'
  // wordmark in white. Not pixel-perfect to their brand, but unmistakable.
  function CoinbaseWordmark() {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
          <circle cx="16" cy="16" r="16" fill="#FFFFFF" />
          <rect x="11" y="11" width="10" height="10" rx="1.5" fill="#0052FF" />
        </svg>
        <span style={{ fontWeight: 600, fontSize: '15px', letterSpacing: '-0.01em' }}>
          coinbase
        </span>
      </div>
    );
  }

  window.MvpCoinbaseOnrampModal = CoinbaseOnrampModal;
})();
