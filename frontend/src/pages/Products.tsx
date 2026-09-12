import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';

type ProductEdge = {
  node: {
    id: string;
    title: string;
    status: string;
    totalInventory: number | null;
    variants?: {
      edges: Array<{
        node: {
          sku?: string | null;
          price?: string | null;
          inventoryQuantity?: number | null;
        };
      }>;
    };
  };
};

export default function Products() {
  const [items, setItems] = useState<ProductEdge[]>([]);
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

    api.get(`/products/?query=${encodeURIComponent(query)}`)
      .then((res) => {
        if (cancelled) return;
        const edges = (res.data?.data?.products?.edges ?? []) as ProductEdge[];
        setItems(edges);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setItems([]);
          setError((err as Error)?.message || 'Failed to load products');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [connected, query]);

  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 12 }}>Products</h1>

      <div style={{ marginBottom: 12 }}>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search products"
          style={{ padding: 8, width: 320, border: '1px solid #e1e3e5', borderRadius: 6 }}
        />
      </div>

      {!connected && (
        <p style={{ color: '#616161' }}>Product management requires Shopify OAuth and an access token.</p>
      )}

      {loading && <p>Loading...</p>}

      {error && <p style={{ color: '#d72c0d' }}>Error: {error}</p>}

      {connected && !loading && (
        <div>
          {items.length === 0 && <p style={{ color: '#616161' }}>No products found.</p>}
          {items.map((edge) => {
            const product = edge.node;
            const firstVariant = product.variants?.edges?.[0]?.node;
            const price = firstVariant?.price;
            const sku = firstVariant?.sku;

            return (
              <div
                key={product.id}
                style={{
                  padding: 12,
                  border: '1px solid #e1e3e5',
                  borderRadius: 8,
                  marginBottom: 8,
                }}
              >
                <div style={{ fontWeight: 600 }}>
                  <Link to={`/products/${product.id}`} style={{ color: '#005bd3', textDecoration: 'none' }}>
                    {product.title || 'Untitled'}
                  </Link>
                </div>
                <div style={{ color: '#616161' }}>
                  {product.status} · {product.totalInventory ?? '—'} in inventory
                </div>
                <div style={{ color: '#616161' }}>
                  {price ? `Price: ${price}` : ''}{price && sku ? ' · ' : ''}{sku ? `SKU: ${sku}` : ''}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
