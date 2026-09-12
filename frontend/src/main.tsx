import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';

const waitForAppBridge = (timeoutMs = 3000): Promise<boolean> => {
  if (typeof window === 'undefined') {
    return Promise.resolve(false);
  }

  if (window.shopify) {
    return Promise.resolve(true);
  }

  return new Promise((resolve) => {
    const start = Date.now();

    const check = () => {
      if (window.shopify) {
        resolve(true);
      } else if (Date.now() - start >= timeoutMs) {
        resolve(false);
      } else {
        setTimeout(check, 50);
      }
    };

    check();
  });
};

waitForAppBridge().then((ready) => {
  const rootElement = document.getElementById('root');
  if (!rootElement) {
    return;
  }

  const content = ready ? (
    <React.StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </React.StrictMode>
  ) : (
    <p>Loading Shopify…</p>
  );

  ReactDOM.createRoot(rootElement).render(content);
});
