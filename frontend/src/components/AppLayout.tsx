import { useEffect } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';

/**
 * Navigation lives in the Shopify admin sidebar (App Bridge ui-nav-menu),
 * the same place merchants find every other app's pages.
 */
export default function AppLayout() {
  const navigate = useNavigate();

  useEffect(() => {
    // App Bridge fires shopify:navigate for nav-menu clicks; route them
    // client-side so the app doesn't reload.
    const onNavigate = (event: Event) => {
      const href = (event.target as HTMLElement | null)?.getAttribute?.('href');
      if (href) navigate(href);
    };
    document.addEventListener('shopify:navigate', onNavigate);
    return () => document.removeEventListener('shopify:navigate', onNavigate);
  }, [navigate]);

  return (
    <>
      <ui-nav-menu>
        <a href="/dashboard" rel="home">
          Dashboard
        </a>
        <a href="/mia">Ask Mia</a>
        <a href="/marketing">SEO &amp; marketing</a>
        <a href="/products">Products</a>
        <a href="/imports">Imports</a>
        <a href="/orders">Orders</a>
        <a href="/customers">Customers</a>
        <a href="/analytics">Analytics</a>
        <a href="/social">Social</a>
        <a href="/settings">Settings</a>
      </ui-nav-menu>
      <Outlet />
    </>
  );
}
