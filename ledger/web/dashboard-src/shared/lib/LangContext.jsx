// LangContext: provides currentLang ('en' | 'zh'), setLang, and t(key) helper.
// t() reads from window.LANG_DICT (loaded by src/data/i18n.js).
// Falls back to the EN string if a key is missing in the requested lang,
// or to the key itself if the key is unknown (so missing keys are visible).

const LangContext = React.createContext(null);

function LangProvider({ children }) {
  const [lang, setLang] = React.useState('en');

  const t = React.useCallback(
    (key) => {
      const dict = window.LANG_DICT || {};
      const entry = dict[key];
      if (!entry) return key; // surface missing keys
      if (entry[lang] !== undefined) return entry[lang];
      if (entry.en !== undefined) return entry.en;
      return key;
    },
    [lang]
  );

  const toggleLang = React.useCallback(() => {
    setLang((l) => (l === 'en' ? 'zh' : 'en'));
  }, []);

  // Mirror lang to <html lang=...> + body data-lang for CSS selectors
  React.useEffect(() => {
    document.documentElement.setAttribute('lang', lang === 'zh' ? 'zh-Hans' : 'en');
    document.body.setAttribute('data-lang', lang);
  }, [lang]);

  return (
    <LangContext.Provider value={{ lang, setLang, toggleLang, t }}>
      {children}
    </LangContext.Provider>
  );
}

function useLang() {
  const ctx = React.useContext(LangContext);
  if (!ctx) throw new Error('useLang must be used inside LangProvider');
  return ctx;
}

// Convenience: just t() without destructuring lang/setLang.
function useT() {
  return useLang().t;
}

window.LangContext = LangContext;
window.LangProvider = LangProvider;
window.useLang = useLang;
window.useT = useT;
