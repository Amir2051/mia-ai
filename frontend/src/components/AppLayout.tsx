import { Outlet, NavLink, useLocation } from 'react-router-dom';
import { useShopifyStatus } from '../hooks/useShopify';

export default function AppLayout() {
  const location = useLocation();
  const { data, loading } = useShopifyStatus();

  const connected = !!data?.connected;

  const hideConnectionBanner = ['/', '/api/auth/session'].includes(location.pathname);

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-name">Mia AI</div>

          <div className="brand-badge">
            {loading ? 'Checking…' : connected ? 'Connected' : 'Disconnected'}
          </div>

          {connected && data?.shop_domain && (
            <div style={{ fontSize: 12, color: '#616161', marginTop: 4 }}>
              {data.shop_domain}
            </div>
          )}
        </div>

        <nav className="nav">
          {[
            { to: '/dashboard', label: 'Dashboard' },
            { to: '/mia', label: '✦ Mia Intelligence' },
            { to: '/products', label: 'Products' },
            { to: '/imports', label: 'Imports' },
            { to: '/orders', label: 'Orders' },
            { to: '/customers', label: 'Customers' },
            { to: '/analytics', label: 'Analytics' },
            { to: '/marketing', label: 'Marketing' },
            { to: '/settings', label: 'Settings' },
          ].map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `nav-item ${isActive ? 'is-active' : ''}`
              }
              end={item.to === '/dashboard'}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="main">
        {!hideConnectionBanner && !loading && !connected && (
          <div className="connection-banner">
            Shopify is not connected yet. Go to Settings to connect your store.
          </div>
        )}

        <div className="main-content">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
