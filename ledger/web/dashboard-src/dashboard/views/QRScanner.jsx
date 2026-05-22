// MVP QRScanner — captures a QR code from the device camera or an uploaded
// image and emits the parsed wallet address via `onDecode(address)`.
//
// Standalone component: renders the scan surface only — no modal chrome.
// AddAddressModal (Step 4) embeds this inside its own dialog. For isolated
// verification, see `mvp/qr_test.html`.
//
// Address parser accepts three input shapes:
//   1. Raw hex:      0x[40 hex chars]
//   2. EIP-681 URI:  ethereum:0x... or base:0x... (with optional @chainId / ?params)
//   3. Fallback:     any string containing a 0x[40 hex] substring
//
// Camera lifecycle: getUserMedia({ facingMode: 'environment' }) on mount,
// rAF loop draws frames to a hidden canvas and runs jsQR on the pixel
// buffer. Stream + rAF are torn down on unmount, on success, and while
// showing the error overlay (resumed via "Try again").

(function () {
  const ADDRESS_RE      = /^0x[a-fA-F0-9]{40}$/;
  const URI_PREFIX_RE   = /^(?:ethereum|base):(0x[a-fA-F0-9]{40})/i;
  const ADDRESS_GREP_RE = /0x[a-fA-F0-9]{40}/;

  function parseAddress(text) {
    if (!text) return null;
    const trimmed = String(text).trim();
    if (ADDRESS_RE.test(trimmed)) return trimmed;
    const uriMatch = trimmed.match(URI_PREFIX_RE);
    if (uriMatch) return uriMatch[1];
    const grepMatch = trimmed.match(ADDRESS_GREP_RE);
    if (grepMatch) return grepMatch[0];
    return null;
  }

  function QRScanner({ onDecode }) {
    const t = window.useT();
    const videoRef     = React.useRef(null);
    const canvasRef    = React.useRef(null);
    const streamRef    = React.useRef(null);
    const rafRef       = React.useRef(null);
    const fileInputRef = React.useRef(null);

    // 'requesting' | 'scanning' | 'denied' | 'no-camera' | 'error'
    const [status, setStatus]     = React.useState('requesting');
    const [errorKey, setErrorKey] = React.useState('');

    const stopStream = React.useCallback(() => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((trk) => trk.stop());
        streamRef.current = null;
      }
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    }, []);

    const handleDecodedText = React.useCallback((text) => {
      const addr = parseAddress(text);
      if (!addr) {
        setErrorKey('mvp.dash.addr_scan.error_not_address');
        setStatus('error');
        return;
      }
      stopStream();
      if (onDecode) onDecode(addr);
    }, [onDecode, stopStream]);

    // Start camera on mount (and whenever we return to 'requesting' via retry).
    React.useEffect(() => {
      if (status !== 'requesting') return;
      let cancelled = false;
      async function start() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          if (!cancelled) setStatus('no-camera');
          return;
        }
        try {
          const stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: 'environment' },
            audio: false,
          });
          if (cancelled) {
            stream.getTracks().forEach((trk) => trk.stop());
            return;
          }
          streamRef.current = stream;
          const video = videoRef.current;
          if (video) {
            video.srcObject = stream;
            video.setAttribute('playsinline', 'true');
            await video.play().catch(() => { /* autoplay blocked is harmless; we still read frames */ });
          }
          setStatus('scanning');
        } catch (e) {
          if (cancelled) return;
          // NotAllowedError → user denied. OverconstrainedError / NotFoundError
          // → no camera that matches. Treat both as "use upload instead".
          const name = e && e.name;
          setStatus(name === 'NotAllowedError' ? 'denied' : 'no-camera');
        }
      }
      start();
      return () => {
        cancelled = true;
      };
    }, [status]);

    // rAF scan loop — runs only while scanning.
    React.useEffect(() => {
      if (status !== 'scanning') return;
      const video  = videoRef.current;
      const canvas = canvasRef.current;
      if (!video || !canvas) return;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      if (!ctx) return;

      const tick = () => {
        if (video.readyState >= video.HAVE_ENOUGH_DATA && video.videoWidth > 0) {
          canvas.width  = video.videoWidth;
          canvas.height = video.videoHeight;
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          try {
            const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const result = typeof window.jsQR === 'function'
              ? window.jsQR(imageData.data, canvas.width, canvas.height, { inversionAttempts: 'dontInvert' })
              : null;
            if (result && result.data) {
              handleDecodedText(result.data);
              return;
            }
          } catch { /* keep scanning */ }
        }
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
      return () => {
        if (rafRef.current) {
          cancelAnimationFrame(rafRef.current);
          rafRef.current = null;
        }
      };
    }, [status, handleDecodedText]);

    // Tear down the stream on unmount.
    React.useEffect(() => () => stopStream(), [stopStream]);

    const handleUpload = async (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      try {
        const dataUrl = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload  = () => resolve(reader.result);
          reader.onerror = reject;
          reader.readAsDataURL(file);
        });
        const img = await new Promise((resolve, reject) => {
          const i = new Image();
          i.onload  = () => resolve(i);
          i.onerror = reject;
          i.src = dataUrl;
        });
        // Use an offscreen canvas so we don't disturb the live-scan canvas.
        const off = document.createElement('canvas');
        off.width  = img.naturalWidth;
        off.height = img.naturalHeight;
        const offCtx = off.getContext('2d', { willReadFrequently: true });
        offCtx.drawImage(img, 0, 0);
        const imageData = offCtx.getImageData(0, 0, off.width, off.height);
        const result = typeof window.jsQR === 'function'
          ? window.jsQR(imageData.data, off.width, off.height)
          : null;
        if (!result || !result.data) {
          setErrorKey('mvp.dash.addr_scan.error_no_qr');
          setStatus('error');
          return;
        }
        handleDecodedText(result.data);
      } catch {
        setErrorKey('mvp.dash.addr_scan.error_image_read');
        setStatus('error');
      } finally {
        // Reset so user can re-pick the same file after a failed attempt.
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
    };

    const handleRetry = () => {
      setErrorKey('');
      // If the stream is still alive, resume scanning; otherwise re-request.
      if (streamRef.current) {
        setStatus('scanning');
      } else {
        setStatus('requesting');
      }
    };

    const showVideo = status === 'scanning' || status === 'error';

    return (
      <div style={{ width: '100%' }}>
        {/* Scan surface — square-ish viewport. */}
        <div
          style={{
            position: 'relative',
            width: '100%',
            aspectRatio: '1 / 1',
            background: 'var(--surface-deep, #1A1A1A)',
            border: '1px solid var(--stroke-rule, rgba(26,26,26,0.20))',
            overflow: 'hidden',
          }}
        >
          {/* Video stays mounted across scanning/error so the stream survives. */}
          <video
            ref={videoRef}
            muted
            autoPlay
            playsInline
            style={{
              width: '100%',
              height: '100%',
              objectFit: 'cover',
              display: showVideo ? 'block' : 'none',
            }}
          />
          {/* Canvas is offscreen — used only for pixel reads. */}
          <canvas ref={canvasRef} style={{ display: 'none' }} />

          {/* Status overlays — only shown when not scanning. */}
          {status === 'requesting' && (
            <Overlay tone="info">
              <div className="font-body" style={{ fontSize: '13px', color: 'rgba(245,241,234,0.78)' }}>
                {t('mvp.dash.addr_scan.requesting')}
              </div>
            </Overlay>
          )}
          {status === 'denied' && (
            <Overlay tone="warn">
              <div className="font-body" style={{ fontSize: '13px', color: 'rgba(245,241,234,0.92)', marginBottom: '6px' }}>
                {t('mvp.dash.addr_scan.denied')}
              </div>
              <div className="font-body" style={{ fontSize: '11px', color: 'rgba(245,241,234,0.6)' }}>
                {t('mvp.dash.addr_scan.upload_instead')}
              </div>
            </Overlay>
          )}
          {status === 'no-camera' && (
            <Overlay tone="warn">
              <div className="font-body" style={{ fontSize: '13px', color: 'rgba(245,241,234,0.92)', marginBottom: '6px' }}>
                {t('mvp.dash.addr_scan.no_camera')}
              </div>
              <div className="font-body" style={{ fontSize: '11px', color: 'rgba(245,241,234,0.6)' }}>
                {t('mvp.dash.addr_scan.upload_instead')}
              </div>
            </Overlay>
          )}
          {status === 'error' && (
            <Overlay tone="warn">
              <div className="font-body" style={{ fontSize: '13px', color: 'rgba(245,241,234,0.92)', marginBottom: '10px', textAlign: 'center', padding: '0 12px' }}>
                {t(errorKey)}
              </div>
              <button
                type="button"
                onClick={handleRetry}
                className="font-body"
                style={{
                  background: 'transparent',
                  border: '1px solid rgba(245,241,234,0.6)',
                  color: 'rgba(245,241,234,0.92)',
                  padding: '6px 14px',
                  fontSize: '12px',
                  cursor: 'pointer',
                  letterSpacing: '0.04em',
                }}
              >
                {t('mvp.dash.addr_scan.retry')}
              </button>
            </Overlay>
          )}

          {/* Subtle scan-area hint when scanning. */}
          {status === 'scanning' && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                pointerEvents: 'none',
                display: 'flex',
                alignItems: 'flex-end',
                justifyContent: 'center',
                padding: '14px',
              }}
            >
              <div
                className="font-body"
                style={{
                  background: 'rgba(0,0,0,0.55)',
                  color: 'rgba(245,241,234,0.92)',
                  padding: '4px 10px',
                  fontSize: '11px',
                  letterSpacing: '0.04em',
                }}
              >
                {t('mvp.dash.addr_scan.hint')}
              </div>
            </div>
          )}
        </div>

        {/* Upload fallback — always available, regardless of camera state. */}
        <div
          style={{
            marginTop: '12px',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            justifyContent: 'space-between',
          }}
        >
          <div
            className="font-body"
            style={{ fontSize: '11px', color: 'var(--ink-tertiary, #8B847A)', letterSpacing: '0.02em' }}
          >
            {t('mvp.dash.addr_scan.or_upload')}
          </div>
          <label
            className="font-body"
            style={{
              padding: '6px 12px',
              background: 'transparent',
              border: '1px solid var(--stroke-rule, rgba(26,26,26,0.20))',
              color: 'var(--ink-primary, #1A1A1A)',
              fontSize: '12px',
              cursor: 'pointer',
              letterSpacing: '0.02em',
            }}
          >
            {t('mvp.dash.addr_scan.upload')}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              onChange={handleUpload}
              style={{ display: 'none' }}
            />
          </label>
        </div>
      </div>
    );
  }

  function Overlay({ tone, children }) {
    // tone is ornamental; the dark video backdrop already carries the look.
    return (
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: tone === 'warn'
            ? 'rgba(26,26,26,0.88)'
            : 'rgba(26,26,26,0.7)',
          padding: '16px',
          textAlign: 'center',
        }}
      >
        {children}
      </div>
    );
  }

  window.QRScanner = QRScanner;
  // Exported for tests / debugging.
  window.parseQRAddress = parseAddress;
})();
