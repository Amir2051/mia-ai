import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';

const waitForAppBridge = (timeoutMs = 5000): Promise<boolean> => {
  if (typeof window === 'undefined') return Promise.resolve(false);
  if (window.shopify) return Promise.resolve(true);
  return new Promise((resolve) => {
    const start = Date.now();
    const check = () => {
      if (window.shopify) resolve(true);
      else if (Date.now() - start >= timeoutMs) resolve(false);
      else setTimeout(check, 50);
    };
    check();
  });
};

const bootstrapShopifySession = async (): Promise<boolean> => {
  try {
    if (!window.shopify?.idToken) return false;
    const token = await window.shopify.idToken();
    if (!token) return false;

    const response = await fetch('/api/auth/session', {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` },
      credentials: 'include',
    });

    if (!response.ok) {
      console.error('Shopify session bootstrap failed:', response.status);
      return false;
    }

    return true;
  } catch (error) {
    console.error('Shopify session bootstrap error:', error);
    return false;
  }
};

waitForAppBridge().then(async (ready) => {
  const rootElement = document.getElementById('root');
  if (!rootElement) return;

  if (!ready) {
    rootElement.innerHTML = '<p>Loading Shopify…</p>';
    return;
  }

  await bootstrapShopifySession();

  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </React.StrictMode>,
  );
});
