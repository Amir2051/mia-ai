import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles.css';

const waitForAppBridge = (timeoutMs = 15000): Promise<boolean> => {
  if (typeof window === 'undefined') return Promise.resolve(false);
  if (window.shopify?.idToken) return Promise.resolve(true);
  return new Promise((resolve) => {
    const start = Date.now();
    const check = () => {
      if (window.shopify?.idToken) resolve(true);
      else if (Date.now() - start >= timeoutMs) resolve(false);
      else setTimeout(check, 50);
    };
    check();
  });
};

const getShopifyIdToken = async (attempts = 8, delayMs = 250): Promise<string | null> => {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      if (window.shopify?.idToken) {
        const token = await window.shopify.idToken();
        if (token) return token;
      }
    } catch (error) {
      if (attempt === attempts - 1) console.error('Shopify ID token unavailable:', error);
    }
    await new Promise((resolve) => setTimeout(resolve, delayMs * Math.min(attempt + 1, 4)));
  }
  return null;
};

const bootstrapShopifySession = async (): Promise<boolean> => {
  const token = await getShopifyIdToken();
  if (!token) return false;

  try {
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

  const sessionReady = await bootstrapShopifySession();

  if (!sessionReady) {
    rootElement.innerHTML = '<p>Waiting for Shopify session…</p>';
    setTimeout(() => window.location.reload(), 1500);
    return;
  }

  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </React.StrictMode>,
  );
});
