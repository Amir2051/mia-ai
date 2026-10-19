import React, { forwardRef, type AnchorHTMLAttributes, type ReactNode } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Link as RouterLink } from 'react-router-dom';
import { AppProvider, type AppProviderProps } from '@shopify/polaris';
import enTranslations from '@shopify/polaris/locales/en.json';
import '@shopify/polaris/build/esm/styles.css';
import App from './App';
import { SessionProvider } from './lib/session';
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

/** Polaris links (Page backAction, Link url, Button url) route client-side. */
type AdapterLinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & {
  url: string;
  external?: boolean;
  children?: ReactNode;
};

const AdapterLink = forwardRef<HTMLAnchorElement, AdapterLinkProps>(function AdapterLink(
  { url, external, children, ...rest },
  ref,
) {
  if (external || /^https?:\/\//.test(url)) {
    return (
      <a ref={ref} href={url} target="_blank" rel="noopener noreferrer" {...rest}>
        {children}
      </a>
    );
  }
  return (
    <RouterLink ref={ref} to={url} {...rest}>
      {children}
    </RouterLink>
  );
});

const bridgeWait = window.self === window.top ? Promise.resolve(false) : waitForAppBridge();
bridgeWait.then(async (bridgeAvailable) => {
  const rootElement = document.getElementById('root');
  if (!rootElement) return;

  // Standalone browser visits should show /login immediately. Only wait for
  // App Bridge when the page is embedded inside Shopify Admin.
  const ready = window.self !== window.top && bridgeAvailable;
  if (ready) await bootstrapShopifySession();

  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <BrowserRouter>
        <AppProvider i18n={enTranslations} linkComponent={AdapterLink as AppProviderProps['linkComponent']}>
          <SessionProvider>
            <App />
          </SessionProvider>
        </AppProvider>
      </BrowserRouter>
    </React.StrictMode>,
  );
});
