import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';

type CustomerEdge = {
  node: {
    id: string;
    displayName?: string | null;
    email?: string | null;
    phone?: string | null;
    numberOfOrders?: number | null;
    amountSpent?: {
      amount?: string | null;
      currencyCode?: string | null;
    } | null;
    orders?: {
      edges: Array<{
        node: {
          id?: string | null;
          name?: string | null;
          createdAt?: string | null;
          totalPriceSet?: {
            shopMoney?: {
              amount?: string | null;
              currencyCode?: string | null;
            } | null;
          } | null;
        };
      }>;
    };
  };
};

export default function Customers() {
  const [items, setItems] = useState<CustomerEdge[]>([]);
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

    api.get(`/customers/?query=${encodeURIComponent(query)}`)
      .then((res) => {
        if (cancelled) return;
        const edges = (res.data?.data?.customers?.edges ?? []) as CustomerEdge[];
        setItems(edges);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setItems([]);
          setError((err as Error)?.message || 'Failed to load customers');
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

  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 12 }}>Customers</h1>

      <div style={{ marginBottom: 12 }}>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search customers"
          style={{ padding: 8, width: 320, border: '1px solid #e1e3e5', borderRadius: 6 }}
        />
      </div>

      {!connected && <p style={{ color: '#616161' }}>Connect Shopify to view live customer data.</p>}

      {loading && <p>Loading...</p>}

      {error && <p style={{ color: '#d72c0d' }}>Error: {error}</p>}

      {connected && !loading && (
        <div>
          {items.length === 0 && <p style={{ color: '#616161' }}>No customers found.</p>}
          {items.map((edge) => {
            const customer = edge.node;
            const orders = customer.orders?.edges ?? [];
            const total = customer.amountSpent;
            const totalAmount = total?.amount;
            const currency = total?.currencyCode || 'USD';
            const recent = orders.slice(0, 3);
            const displayName = customer.displayName || customer.email || 'Unnamed customer';

            return (
              <div
                key={customer.id}
                style={{
                  padding: 16,
                  border: '1px solid #e1e3e5',
                  borderRadius: 8,
                  marginBottom: 10,
                }}
              >
                <div style={{ fontWeight: 600 }}>
                  <Link to={`/customers/${customer.id}`} style={{ color: '#005bd3', textDecoration: 'none' }}>
                    {displayName}
                  </Link>
                </div>
                <div style={{ color: '#616161' }}>
                  {customer.email || '—'} · {customer.phone ? `Phone: ${customer.phone}` : 'No phone'}
                </div>
                <div style={{ color: '#616161' }}>
                  Orders: {customer.numberOfOrders ?? '—'} · Spent: {formatCurrency(totalAmount, currency)}
                </div>
                {recent.length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <div style={{ fontSize: 12, color: '#616161' }}>Recent orders:</div>
                    {recent.map((orderEdge) => (
                      <div key={orderEdge.node.id || Math.random()} style={{ fontSize: 12, color: '#616161' }}>
                        {orderEdge.node.name || 'Untitled'} · {orderEdge.node.createdAt || '—'} · {formatCurrency(orderEdge.node.totalPriceSet?.shopMoney?.amount, orderEdge.node.totalPriceSet?.shopMoney?.currencyCode || 'USD')}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
