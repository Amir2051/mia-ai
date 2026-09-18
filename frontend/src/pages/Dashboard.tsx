import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';

type Session = {
  connected: boolean;
  shop_domain?: string | null;
  scopes?: string | null;
};

type DashboardSummary = {
  revenue?: number;
  orders?: number;
  customers?: number;
  average_order_value?: number;
  recent_sales?: Array<{
    id?: string | null;
    name?: string | null;
    created_at?: string | null;
    amount?: number | null;
    currency?: string | null;
    customer?: Record<string, unknown> | null;
  }>;
};

export default function Dashboard() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dashboard, setDashboard] = useState<DashboardSummary | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    api.get('/auth/session')
      .then((res) => {
        if (cancelled) return;
        setSession(res.data as Session);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setSession({ connected: false, shop_domain: null, scopes: null });
        setError((err as Error)?.message || 'Failed to load session');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!session?.connected) {
      setDashboard(null);
      return;
    }

    let cancelled = false;
    setDashboardLoading(true);
    setDashboardError(null);

    api.get('/analytics/')
      .then((res) => {
        if (cancelled) return;
        setDashboard(res.data as DashboardSummary);
        setDashboardError(null);
      })
      .catch(() => {
        if (cancelled) return;
        setDashboard(null);
        setDashboardError(
          "We could not load your Shopify sales right now. Please try again shortly.",
        );
      })
      .finally(() => {
        if (!cancelled) setDashboardLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [session?.connected, refreshKey]);

  const formatCurrency = (value?: number | null, currency = 'USD') => {
    if (typeof value !== 'number') return '—';
    try {
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency,
        maximumFractionDigits: 2,
      }).format(value);
    } catch {
      return `${currency} ${value.toFixed(2)}`;
    }
  };

  const formatDate = (value?: string | null) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
  };

  if (loading) {
    return <p>Loading...</p>;
  }

  const connectionError = error && !session?.connected;

  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Dashboard</h1>

      {connectionError && (
        <div
          style={{
            padding: 12,
            borderRadius: 6,
            background: '#fff4f4',
            color: '#d72c0d',
            marginBottom: 16,
          }}
        >
          {error}
        </div>
      )}

      <div
        style={{
          padding: 16,
          border: '1px solid #e1e3e5',
          borderRadius: 8,
          marginBottom: 16,
        }}
      >
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>Connection status</h2>
        <p>
          {session?.connected
            ? `Shopify connected: ${session.shop_domain || 'Unknown'}`
            : 'Mia AI is not yet connected to Shopify.'}
        </p>
        {session?.scopes && (
          <p style={{ color: '#616161', fontSize: 12 }}>
            Permissions: {session.scopes}
          </p>
        )}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: 16,
          marginBottom: 16,
        }}
      >
        {[
          { label: 'Revenue', value: formatCurrency(dashboard?.revenue ?? 0) },
          { label: 'Orders', value: dashboard?.orders ?? 0 },
          { label: 'Average order value', value: formatCurrency(dashboard?.average_order_value ?? 0) },
        ].map((item) => (
          <div
            key={item.label}
            style={{
              padding: 16,
              border: '1px solid #e1e3e5',
              borderRadius: 8,
            }}
          >
            <h3 style={{ fontSize: 14, color: '#616161', marginBottom: 8 }}>
              {item.label}
            </h3>
            <p style={{ fontSize: 20, fontWeight: 600 }}>
              {dashboardLoading ? '...' : item.value}
            </p>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        <div
          style={{
            padding: 16,
            border: '1px solid #e1e3e5',
            borderRadius: 8,
          }}
        >
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Recent sales</h2>
          {dashboardLoading && <p>Loading...</p>}
          {dashboardError && (
            <div
              style={{
                padding: 12,
                borderRadius: 6,
                background: '#fff4f4',
                color: '#d72c0d',
                marginBottom: 16,
              }}
            >
              {dashboardError}
              <button
                type="button"
                onClick={() => setRefreshKey((value) => value + 1)}
                style={{ display: "block", marginTop: 8 }}
              >
                Try again
              </button>
            </div>
          )}
          {session?.connected && !dashboardLoading && !dashboardError && !dashboard && (
            <div
              style={{
                padding: 16,
                border: '1px solid #e1e3e5',
                borderRadius: 8,
                color: '#616161',
              }}
            >
              Connect Shopify to see real sales data.
            </div>
          )}
          {session?.connected && !dashboardLoading && !dashboardError && dashboard && (
            <>
              {(dashboard?.recent_sales?.length ?? 0) === 0 && (
                <p style={{ color: '#616161' }}>No recent sales.</p>
              )}
              {(dashboard?.recent_sales ?? []).map((sale) => (
                <div
                  key={sale.id}
                  style={{
                    padding: '10px 0',
                    borderBottom: '1px solid #f1f2f4',
                  }}
                >
                  <div style={{ fontWeight: 600 }}>{sale.name || 'Untitled order'}</div>
                  <div style={{ color: '#616161', fontSize: 12 }}>
                    {formatDate(sale.created_at)}
                  </div>
                  <div style={{ color: '#616161', fontSize: 12 }}>
                    {formatCurrency(sale.amount, sale.currency || 'USD')}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>

        <div
          style={{
            padding: 16,
            border: '1px solid #e1e3e5',
            borderRadius: 8,
          }}
        >
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Quick actions</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Link to="/products" style={{ color: '#005bd3', textDecoration: 'none' }}>
              Manage products
            </Link>
            <Link to="/orders" style={{ color: '#005bd3', textDecoration: 'none' }}>
              View orders
            </Link>
            <Link to="/customers" style={{ color: '#005bd3', textDecoration: 'none' }}>
              Review customers
            </Link>
            <Link to="/imports" style={{ color: '#005bd3', textDecoration: 'none' }}>
              Import products
            </Link>
            <Link to="/marketing" style={{ color: '#005bd3', textDecoration: 'none' }}>
              Marketing assistant
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
