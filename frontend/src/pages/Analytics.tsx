import { useState, useEffect } from 'react';
import api from '../services/api';

type AnalyticsSummary = {
  connected?: boolean;
  shop_domain?: string | null;
  revenue?: number | null;
  orders?: number | null;
  customers?: number | null;
  average_order_value?: number | null;
  top_products?: Array<{
    title?: string | null;
    sku?: string | null;
    units_sold?: number | null;
    revenue?: number | null;
  }> | null;
  recent_sales?: Array<{
    id?: string | null;
    name?: string | null;
    created_at?: string | null;
    amount?: number | null;
    currency?: string | null;
    customer?: Record<string, unknown> | null;
  }> | null;
  filtered?: {
    orders?: Array<Record<string, unknown>> | null;
    count?: number | null;
    revenue?: number | null;
  } | null;
};

export default function Analytics() {
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');

  useEffect(() => {
    api.get('/auth/session')
      .then((res) => setConnected(!!res.data?.connected))
      .catch(() => setConnected(false));
  }, []);

  useEffect(() => {
    if (!connected) {
      setData(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    const params: Record<string, string> = {};
    if (start) params.start = start;
    if (end) params.end = end;

    api.get('/analytics/', { params })
      .then((res) => {
        if (cancelled) return;
        setData((res.data as AnalyticsSummary) || null);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setData(null);
          setError((err as Error)?.message || 'Failed to load analytics');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [connected, start, end]);

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

  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 12 }}>Analytics</h1>

      {!connected && <p style={{ color: '#616161' }}>Analytics uses only real Shopify data.</p>}

      <div style={{ marginBottom: 16 }}>
        <input
          value={start}
          onChange={(event) => setStart(event.target.value)}
          placeholder="Start date"
          style={{ marginRight: 8, padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
        />
        <input
          value={end}
          onChange={(event) => setEnd(event.target.value)}
          placeholder="End date"
          style={{ padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
        />
      </div>

      {loading && <p>Loading...</p>}

      {error && <p style={{ color: '#d72c0d' }}>Error: {error}</p>}

      {connected && !loading && !error && data && (
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 16 }}>
            <div style={{ padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <h2 style={{ fontSize: 14, color: '#616161', marginBottom: 8 }}>Revenue</h2>
              <p style={{ fontSize: 20, fontWeight: 600 }}>{formatCurrency(data.revenue)}</p>
            </div>
            <div style={{ padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <h2 style={{ fontSize: 14, color: '#616161', marginBottom: 8 }}>Orders</h2>
              <p style={{ fontSize: 20, fontWeight: 600 }}>{data.orders ?? '—'}</p>
            </div>
            <div style={{ padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <h2 style={{ fontSize: 14, color: '#616161', marginBottom: 8 }}>Average order value</h2>
              <p style={{ fontSize: 20, fontWeight: 600 }}>{formatCurrency(data.average_order_value)}</p>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
            <div style={{ padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <h2 style={{ fontSize: 16, marginBottom: 8 }}>Top products</h2>
              {(data.top_products?.length ?? 0) === 0 && <p style={{ color: '#616161' }}>No product data yet.</p>}
              {(data.top_products ?? []).map((product) => (
                <div key={`${product.title}-${product.sku}`} style={{ padding: '10px 0', borderBottom: '1px solid #f1f2f4' }}>
                  <div style={{ fontWeight: 600 }}>{product.title || 'Unknown'}</div>
                  <div style={{ color: '#616161', fontSize: 12 }}>{product.sku || 'No SKU'} · {product.units_sold ?? 0} sold · {formatCurrency(product.revenue)}</div>
                </div>
              ))}
            </div>

            <div style={{ padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <h2 style={{ fontSize: 16, marginBottom: 8 }}>Recent sales</h2>
              {(data.recent_sales?.length ?? 0) === 0 && <p style={{ color: '#616161' }}>No sales data yet.</p>}
              {(data.recent_sales ?? []).map((sale) => (
                <div key={sale.id} style={{ padding: '10px 0', borderBottom: '1px solid #f1f2f4' }}>
                  <div style={{ fontWeight: 600 }}>{sale.name || 'Untitled order'}</div>
                  <div style={{ color: '#616161', fontSize: 12 }}>{formatDate(sale.created_at)} · {formatCurrency(sale.amount, sale.currency || 'USD')}</div>
                </div>
              ))}
            </div>
          </div>

          {data.filtered && (
            <div style={{ marginTop: 16, padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <h2 style={{ fontSize: 16, marginBottom: 8 }}>Filtered range</h2>
              <div style={{ color: '#616161' }}>Count: {data.filtered.count ?? '—'} · Revenue: {formatCurrency(data.filtered.revenue)}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
