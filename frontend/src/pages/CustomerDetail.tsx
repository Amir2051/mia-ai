import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api from '../services/api';

type Customer = {
  id?: string | null;
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

export default function CustomerDetail() {
  const { id } = useParams();
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [customer, setCustomer] = useState<Customer | null>(null);

  useEffect(() => {
    api.get('/auth/session')
      .then((res) => setConnected(!!res.data?.connected))
      .catch(() => setConnected(false));
  }, []);

  useEffect(() => {
    if (!connected) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    api.get(`/customers/${id}`)
      .then((res) => {
        if (cancelled) return;
        setCustomer((res.data?.data as Customer) || null);
      })
      .catch((err) => {
        if (!cancelled) setError((err as Error)?.message || 'Failed to load customer');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [connected, id]);

  if (!connected) {
    return <p style={{ color: '#616161' }}>Connect Shopify to view live customer data.</p>;
  }

  if (loading) {
    return <p>Loading...</p>;
  }

  if (error) {
    return <p style={{ color: '#d72c0d' }}>Error: {error}</p>;
  }

  if (!customer) {
    return <p style={{ color: '#616161' }}>Customer not found.</p>;
  }

  const recentOrders = customer.orders?.edges ?? [];
  const totalAmount = customer.amountSpent?.amount;
  const currency = customer.amountSpent?.currencyCode || 'USD';

  const formatCurrency = (amount?: string | null, currencyCode = currency) => {
    if (!amount) return '—';
    const value = Number(amount);
    if (!Number.isFinite(value)) return '—';
    try {
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currencyCode,
        maximumFractionDigits: 2,
      }).format(value);
    } catch {
      return `${currencyCode} ${value.toFixed(2)}`;
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
      <h1 style={{ fontSize: 24, marginBottom: 12 }}>{customer.displayName || customer.email || 'Customer'}</h1>
      <div style={{ marginBottom: 12 }}>
        <Link to="/customers" style={{ color: '#005bd3' }}>Back to customers</Link>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Details</h2>
          <div style={{ color: '#616161' }}>{customer.email || '—'}</div>
          <div style={{ color: '#616161' }}>{customer.phone ? `Phone: ${customer.phone}` : 'No phone'}</div>
          <div style={{ color: '#616161' }}>Orders: {customer.numberOfOrders ?? '—'}</div>
          <div style={{ color: '#616161' }}>Spent: {formatCurrency(totalAmount, currency)}</div>
        </div>

        <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Recent orders</h2>
          {recentOrders.length === 0 && <p style={{ color: '#616161' }}>No orders found.</p>}
          {recentOrders.map((orderEdge) => (
            <div key={orderEdge.node.id || Math.random()} style={{ padding: '10px 0', borderBottom: '1px solid #f1f2f4' }}>
              <Link to={`/orders/${orderEdge.node.id}`} style={{ color: '#005bd3', textDecoration: 'none', fontWeight: 600 }}>
                {orderEdge.node.name || 'Untitled'}
              </Link>
              <div style={{ color: '#616161', fontSize: 12 }}>
                {formatDate(orderEdge.node.createdAt)} · {formatCurrency(orderEdge.node.totalPriceSet?.shopMoney?.amount, orderEdge.node.totalPriceSet?.shopMoney?.currencyCode || 'USD')}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
