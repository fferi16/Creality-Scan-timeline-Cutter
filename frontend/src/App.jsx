import React from 'react';
import './App.css';
import RawEditor from './components/RawEditor';
import { useT } from './i18n';

export default function App() {
  const { t, lang, setLang } = useT();
  return (
    <>
      <header className="app-header">
        <div className="logo-container">
          <div className="logo-icon" />
          <h1 className="logo-text">{t('appName')}</h1>
        </div>
        <div className="header-right">
          <div className="lang-switch">
            <button className={lang === 'hu' ? 'active' : ''} onClick={() => setLang('hu')}>HU</button>
            <button className={lang === 'en' ? 'active' : ''} onClick={() => setLang('en')}>EN</button>
          </div>
          <div className="status-badge">
            <div className="status-dot" />
            {t('statusReady')}
          </div>
        </div>
      </header>

      <div className="app-container">
        <RawEditor />
      </div>
    </>
  );
}
