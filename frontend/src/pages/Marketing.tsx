import { useEffect, useState } from 'react';
import api from '../services/api';

type MarketingResponse = {
  connected?: boolean;
  shop_domain?: string | null;
  suggestions?: Array<{
    title?: string | null;
    message?: string | null;
    cta?: string | null;
  }> | null;
  seo?: {
    items?: Array<{
      product_title?: string | null;
      seo_title?: string | null;
      seo_description?: string | null;
    }> | null;
    note?: string | null;
  } | null;
  campaigns?: Array<{
    subject?: string | null;
    channel?: string | null;
    body?: string | null;
  }> | null;
  error?: string | null;
};

export default function Marketing() {
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [marketing, setMarketing] = useState<MarketingResponse | null>(null);
  const [productId, setProductId] = useState('');
  const [socialLoading, setSocialLoading] = useState(false);

  useEffect(() => {
    api.get('/auth/session')
      .then((res) => setConnected(!!res.data?.connected))
      .catch(() => setConnected(false));
  }, []);

  useEffect(() => {
    if (!connected) {
      setMarketing(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    api.get('/marketing/')
      .then((res) => {
        if (cancelled) return;
        setMarketing(res.data as MarketingResponse);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setMarketing(null);
          setError((err as Error)?.message || 'Failed to load marketing suggestions');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [connected]);

  const loadProductMarketing = async () => {
    if (!productId.trim()) return;

    setSocialLoading(true);
    setError(null);

    try {
      const res = await api.get(`/marketing/product/${encodeURIComponent(productId.trim())}`);
      setMarketing(res.data as MarketingResponse);
    } catch (err) {
      setError((err as Error)?.message || 'Failed to load product marketing');
    } finally {
      setSocialLoading(false);
    }
  };

  const loadSocialCopy = async () => {
    setSocialLoading(true);
    setError(null);

    try {
      const res = await api.get('/marketing/social');
      setMarketing(res.data as MarketingResponse);
    } catch (err) {
      setError((err as Error)?.message || 'Failed to load social copy');
    } finally {
      setSocialLoading(false);
    }
  };

  if (!connected) {
    return <p style={{ color: '#616161' }}>Connect Shopify to enable marketing features.</p>;
  }

  if (loading) {
    return <p>Loading...</p>;
  }

  if (error) {
    return <p style={{ color: '#d72c0d' }}>Error: {error}</p>;
  }

  const suggestions = marketing?.suggestions ?? [];
  const seoItems = marketing?.seo?.items ?? [];
  const campaigns = marketing?.campaigns ?? [];

  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 12 }}>Marketing</h1>

      <div style={{ marginBottom: 12 }}>
        <input
          value={productId}
          onChange={(event) => setProductId(event.target.value)}
          placeholder="Product ID for focused marketing"
          style={{ marginRight: 8, padding: 8, width: 320, border: '1px solid #e1e3e5', borderRadius: 6 }}
        />
        <button
          onClick={loadProductMarketing}
          disabled={socialLoading}
          style={{
            padding: '8px 12px',
            border: '1px solid #e1e3e5',
            borderRadius: 6,
            background: 'white',
            cursor: socialLoading ? 'not-allowed' : 'pointer',
            marginRight: 8,
          }}
        >
          {socialLoading ? 'Loading…' : 'Product marketing'}
        </button>
        <button
          onClick={loadSocialCopy}
          disabled={socialLoading}
          style={{
            padding: '8px 12px',
            border: '1px solid #e1e3e5',
            borderRadius: 6,
            background: 'white',
            cursor: socialLoading ? 'not-allowed' : 'pointer',
          }}
        >
          {socialLoading ? 'Loading…' : 'Social copy'}
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        <div style={{ padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Suggestions</h2>
          {(suggestions.length ?? 0) === 0 && <p style={{ color: '#616161' }}>No suggestions yet.</p>}
          {suggestions.map((suggestion, index) => (
            <div key={index} style={{ padding: '10px 0', borderBottom: '1px solid #f1f2f4' }}>
              <div style={{ fontWeight: 600 }}>{suggestion.title || 'Suggestion'}</div>
              <div style={{ color: '#616161' }}>{suggestion.message || ''}</div>
              <div style={{ color: '#005bd3', fontSize: 12 }}>{suggestion.cta || ''}</div>
            </div>
          ))}
        </div>

        <div style={{ padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>SEO</h2>
          {(seoItems.length ?? 0) === 0 && <p style={{ color: '#616161' }}>No SEO suggestions yet.</p>}
          {seoItems.map((item, index) => (
            <div key={index} style={{ padding: '10px 0', borderBottom: '1px solid #f1f2f4' }}>
              <div style={{ fontWeight: 600 }}>{item.product_title || 'Product'}</div>
              <div style={{ color: '#005bd3' }}>{item.seo_title || ''}</div>
              <div style={{ color: '#616161', fontSize: 12 }}>{item.seo_description || ''}</div>
            </div>
          ))}
        </div>

        <div style={{ padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Campaign drafts</h2>
          {(campaigns.length ?? 0) === 0 && <p style={{ color: '#616161' }}>No campaign drafts yet.</p>}
          {campaigns.map((campaign, index) => (
            <div key={index} style={{ padding: '10px 0', borderBottom: '1px solid #f1f2f4' }}>
              <div style={{ fontWeight: 600 }}>{String(campaign.subject ?? 'Campaign draft')}</div>
              <div style={{ color: '#616161', fontSize: 12 }}>{String(campaign.channel ?? '')}</div>
              <pre style={{ whiteSpace: 'pre-wrap', color: '#616161', fontSize: 12 }}>
                {String(campaign.body ?? '')}
              </pre>
            </div>
          ))}
        </div>

        <div style={{ padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Social copy</h2>
          <p style={{ color: '#616161', fontSize: 12 }}>Use the social copy button to refresh short product announcements.</p>
        </div>
      </div>
    </div>
  );
}
