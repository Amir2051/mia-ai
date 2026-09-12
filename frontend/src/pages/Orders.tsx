import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';

type LineItem = {
  title?: string | null;
  quantity?: number | null;
  originalUnitPriceSet?: {
    shopMoney?: {
      amount?: string | null;
      currencyCode?: string | null;
    } | null;
  } | null;
};

type OrderEdge = {
  node: {
    id: string;
    name: string;
    createdAt: string;
    financialStatus?: string | null;
    fulfillmentStatus?: string | null;
    customer?: {
      displayName?: string | null;
      email?: string | null;
    } | null;
    lineItems?: {
      edges: Array<{
        node: LineItem;
      }>;
    };
    totalPriceSet?: {
      shopMoney?: {
        amount?: string | null;
        currencyCode?: string | null;
      } | null;
    } | null;
  };
};

export default function Orders() {
  const [items, setItems] = useState<OrderEdge[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  useEffect(() => {
    api.get('/auth/session')
      .then((res) => setConnected(!!res.data?.connected))
      .catch(() => setConnected(false));
  }, []);

  useEffect(() => {
    if (!connected) {
      setItems([]);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    api.get(`/orders/?query=${encodeURIComponent(query)}`)
      .then((res) => {
        if (cancelled) return;
        const edges = (res.data?.data?.orders?.edges ?? []) as OrderEdge[];
        setItems(edges);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setItems([]);
          setError((err as Error)?.message || 'Failed to load orders');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [connected, query]);

  const formatCurrency = (amount?: string | null, currency = 'USD') => {
    if (!amount) return '—';
    const value = Number(amount);
    if (!Number.isFinite(value)) return '—';
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
      <h1 style={{ fontSize: 24, marginBottom: 12 }}>Orders</h1>

      <div style={{ marginBottom: 12 }}>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search orders"
          style={{ padding: 8, width: 320, border: '1px solid #e1e3e5', borderRadius: 6 }}
        />
      </div>

      {!connected && <p style={{ color: '#616161' }}>Connect Shopify to view live order data.</p>}

      {loading && <p>Loading...</p>}

      {error && <p style={{ color: '#d72c0d' }}>Error: {error}</p>}

      {connected && !loading && (
        <div>
          {items.length === 0 && <p style={{ color: '#616161' }}>No orders found.</p>}
          {items.map((edge) => {
            const order = edge.node;
            const lineItems = order.lineItems?.edges ?? [];
            const totalAmount = order.totalPriceSet?.shopMoney?.amount;
            const currency = order.totalPriceSet?.shopMoney?.currencyCode || 'USD';

            return (
              <div
                key={order.id}
                style={{
                  padding: 16,
                  border: '1px solid #e1e3e5',
                  borderRadius: 8,
                  marginBottom: 10,
                }}
              >
                <div style={{ fontWeight: 600 }}>
                  <Link to={`/orders/${order.id}`} style={{ color: '#005bd3', textDecoration: 'none' }}>
                    {order.name || 'Untitled order'}
                  </Link>
                </div>
                <div style={{ color: '#616161' }}>
                  {formatDate(order.createdAt)} · {formatCurrency(totalAmount, currency)}
                </div>
                <div style={{ color: '#616161' }}>
                  {order.customer?.displayName || order.customer?.email || 'Guest'} · {lineItems.length} line items
                </div>
                <div style={{ color: '#616161' }}>
                  Financial: {order.financialStatus || '—'} · Fulfillment: {order.fulfillmentStatus || '—'}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
