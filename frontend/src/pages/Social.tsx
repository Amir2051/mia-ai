import { useEffect, useState } from 'react';
import api from '../services/api';

type Account = {
  id: number;
  provider: string;
  account_type: string;
  external_account_id: string;
  account_name: string;
  metadata?: Record<string, unknown>;
  token_expires_at?: string | null;
};

export default function Social() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [statusMessage, setStatusMessage] = useState('');
  const [monitoring, setMonitoring] = useState<Record<number, unknown>>({});
  const [link, setLink] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.get('/social/accounts');
      setAccounts(res.data?.accounts ?? []);
      setError('');
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Unable to load social accounts');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const params = new URLSearchParams(window.location.search);
    if (params.get('connected') === '1') {
      setStatusMessage('Meta account connected successfully.');
      window.history.replaceState({}, '', '/social');
    }
  }, []);

  const connectMeta = async () => {
    setError('');
    try {
      const res = await api.get('/social/meta/connect');
      const url = res.data?.authorization_url;
      if (!url) throw new Error('Meta authorization URL was not returned');
      window.location.href = url;
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Unable to start Meta connection');
    }
  };

  const publishFacebook = async (accountId: number) => {
    if (!message.trim()) return;
    setError('');
    try {
      await api.post('/social/meta/facebook/publish', {
        account_id: accountId,
        message: message.trim(),
        link: link.trim() || null,
      });
      setMessage('');
      setLink('');
      setStatusMessage('Facebook post published.');
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Facebook publish failed');
    }
  };

  const monitorFacebook = async (accountId: number) => {
    setError('');
    try {
      const res = await api.get(`/social/meta/facebook/${accountId}/monitor`);
      setMonitoring((prev) => ({ ...prev, [accountId]: res.data }));
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Unable to load Facebook activity');
    }
  };

  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 8 }}>Social Media</h1>
      <p style={{ color: '#616161', marginBottom: 16 }}>
        Connect Meta accounts to publish to Facebook Pages and linked Instagram professional accounts from Mia.
      </p>

      {error && <p style={{ color: '#d72c0d' }}>Error: {error}</p>}
      {statusMessage && <p style={{ color: '#008060' }}>{statusMessage}</p>}

      <button
        onClick={connectMeta}
        style={{ padding: '10px 16px', border: 'none', borderRadius: 6, background: '#1877f2', color: 'white', fontWeight: 600 }}
      >
        Connect Facebook / Instagram
      </button>

      <div style={{ marginTop: 20 }}>
        <h2 style={{ fontSize: 16 }}>Connected accounts</h2>
        {loading && <p>Loading...</p>}
        {!loading && accounts.length === 0 && <p style={{ color: '#616161' }}>No social accounts connected.</p>}
        {accounts.map((account) => (
          <div key={account.id} style={{ marginTop: 10, padding: 14, border: '1px solid #e1e3e5', borderRadius: 8 }}>
            <div style={{ fontWeight: 600 }}>{account.account_name}</div>
            <div style={{ color: '#616161', fontSize: 12 }}>{account.provider} · {account.account_type}</div>
            {account.account_type === 'facebook_page' && (
              <div style={{ marginTop: 12 }}>
                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Write a Facebook post..."
                  rows={4}
                  style={{ width: '100%', padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                />
                <input
                  value={link}
                  onChange={(e) => setLink(e.target.value)}
                  placeholder="Optional link"
                  style={{ width: '100%', marginTop: 8, padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                />
                <button
                  onClick={() => publishFacebook(account.id)}
                  style={{ marginTop: 8, padding: '9px 14px', border: '1px solid #e1e3e5', borderRadius: 6, background: 'white', fontWeight: 600 }}
                >
                  Publish to Facebook
                </button>
                <button
                  onClick={() => monitorFacebook(account.id)}
                  style={{ marginTop: 8, marginLeft: 8, padding: '9px 14px', border: '1px solid #e1e3e5', borderRadius: 6, background: 'white' }}
                >
                  Monitor activity
                </button>
                {monitoring[account.id] && (
                  <pre style={{ marginTop: 10, padding: 10, background: '#f6f6f7', overflow: 'auto', fontSize: 11 }}>
                    {JSON.stringify(monitoring[account.id], null, 2)}
                  </pre>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
