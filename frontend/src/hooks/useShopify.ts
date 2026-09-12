import { useEffect, useState } from 'react';
import api from '../services/api';

type ShopifyStatus = {
  connected: boolean;
  shop_domain?: string | null;
  scopes?: string | null;
} | null;

export function useShopifyStatus() {
  const [data, setData] = useState<ShopifyStatus>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    setLoading(true);

    api.get('/auth/session')
      .then((res) => {
        if (cancelled) return;

        setData({
          connected: !!res.data?.connected,
          shop_domain: res.data?.shop_domain ?? null,
          scopes: res.data?.scopes ?? null,
        });
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;

        setData({
          connected: false,
          shop_domain: null,
          scopes: null,
        });
        setError((err as Error)?.message || 'Failed to load Shopify session');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return { data, error, loading };
}
