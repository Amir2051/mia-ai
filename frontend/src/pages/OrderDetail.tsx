import { useState, useEffect } from 'react';
import { Link, useParams } from 'react-router-dom';
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

type Order = {
  id?: string | null;
  name?: string | null;
  createdAt?: string | null;
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

export default function OrderDetail() {
  const { id } = useParams();
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [order, setOrder] = useState<Order | null>(null);

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

    api.get(`/orders/${id}`)
      .then((res) => {
        if (cancelled) return;
        setOrder((res.data?.data as Order) || null);
      })
      .catch((err) => {
        if (!cancelled) setError((err as Error)?.message || 'Failed to load order');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [connected, id]);

  if (!connected) {
    return <p style={{ color: '#616161' }}>Connect Shopify to view live order data.</p>;
  }

  if (loading) {
    return <p>Loading...</p>;
  }

  if (error) {
    return <p style={{ color: '#d72c0d' }}>Error: {error}</p>;
  }

  if (!order) {
    return <p style={{ color: '#616161' }}>Order not found.</p>;
  }

  const lineItems = order.lineItems?.edges ?? [];
  const totalAmount = order.totalPriceSet?.shopMoney?.amount;
  const currency = order.totalPriceSet?.shopMoney?.currencyCode || 'USD';

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
      <h1 style={{ fontSize: 24, marginBottom: 12 }}>{order.name || 'Order'}</h1>
      <div style={{ marginBottom: 12 }}>
        <Link to="/orders" style={{ color: '#005bd3' }}>Back to orders</Link>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Order</h2>
          <div style={{ color: '#616161' }}>Date: {formatDate(order.createdAt)}</div>
          <div style={{ color: '#616161' }}>Total: {formatCurrency(totalAmount, currency)}</div>
          <div style={{ color: '#616161' }}>Financial: {order.financialStatus || '—'}</div>
          <div style={{ color: '#616161' }}>Fulfillment: {order.fulfillmentStatus || '—'}</div>
        </div>

        <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Customer</h2>
          <div style={{ color: '#616161' }}>{order.customer?.displayName || order.customer?.email || 'Guest'}</div>
          <div style={{ color: '#616161' }}>{order.customer?.email || '—'}</div>
        </div>
      </div>

      <div style={{ marginTop: 16, border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>Line items</h2>
        {lineItems.length === 0 && <p style={{ color: '#616161' }}>No line items.</p>}
        {lineItems.map((edge, index) => {
          const item = edge.node;
          const itemTotal = item.originalUnitPriceSet?.shopMoney?.amount;
          const itemCurrency = item.originalUnitPriceSet?.shopMoney?.currencyCode || currency;

          return (
            <div key={`${item.title || 'item'}-${index}`} style={{ padding: '10px 0', borderBottom: '1px solid #f1f2f4' }}>
              <div style={{ fontWeight: 600 }}>{item.title || 'Item'}</div>
              <div style={{ color: '#616161' }}>Qty: {item.quantity ?? '—'}</div>
              <div style={{ color: '#616161' }}>{formatCurrency(itemTotal, itemCurrency)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
