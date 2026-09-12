import { useEffect, useState } from 'react';
import api from '../services/api';

type Session = {
  connected: boolean;
  shop_domain?: string | null;
  scopes?: string | null;
};

type ShopifyStatus = {
  configured: boolean;
};

export default function Settings() {
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<ShopifyStatus | null>(null);
  const [shopDomain, setShopDomain] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [markupPercent, setMarkupPercent] = useState('');
  const [duplicateAction, setDuplicateAction] = useState('skip');
  const [importStatus, setImportStatus] = useState('DRAFT');
  const [savingSettings, setSavingSettings] = useState(false);

  useEffect(() => {
    api.get('/auth/session')
      .then((res) => {
        const data = res.data as Session;
        setSession(data);

        if (data.shop_domain) {
          setShopDomain(data.shop_domain);
        }
      })
      .catch(() => {
        setSession({
          connected: false,
          shop_domain: null,
          scopes: null,
        });
      });

    api.get('/shopify/status')
      .then((res) => setStatus(res.data))
      .catch(() => {});
  }, []);

  const connectShopify = async () => {
    setError(null);

    const normalizedShop = shopDomain
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, '')
      .replace(/\/+$/, '');

    if (!normalizedShop) {
      setError('Enter your Shopify store domain first.');
      return;
    }

    if (!/^[a-z0-9][a-z0-9-]*\.myshopify\.com$/.test(normalizedShop)) {
      setError(
        'Enter a valid Shopify domain, for example: mystore.myshopify.com'
      );
      return;
    }

    setConnecting(true);

    try {
      const res = await api.get('/auth/install', {
        params: {
          shop: normalizedShop,
        },
      });

      const authorizationUrl = res.data?.authorization_url;

      if (!authorizationUrl) {
        throw new Error('Shopify authorization URL was not returned.');
      }

      window.location.href = authorizationUrl;
    } catch (err) {
      setConnecting(false);
      setError(
        (err as Error)?.message ||
        'Unable to start Shopify authorization.'
      );
    }
  };

  const disconnectShopify = async () => {
    setError(null);

    try {
      await api.post('/auth/logout');

      setSession({
        connected: false,
        shop_domain: null,
        scopes: null,
      });
    } catch (err) {
      setError(
        (err as Error)?.message ||
        'Unable to disconnect Shopify.'
      );
    }
  };

  const loadSettings = async () => {
    if (!session?.connected) {
      return;
    }

    try {
      const res = await api.get('/settings/');
      const data = res.data || {};
      const settings = (data.settings || {}) as Record<string, any>;
      setMarkupPercent(settings.default_markup_percent != null ? String(settings.default_markup_percent) : '');
      setDuplicateAction(settings.duplicate_product_action || 'skip');
      setImportStatus(settings.import_default_status || 'DRAFT');
    } catch {
      // ignore settings load failure
    }
  };

  useEffect(() => {
    loadSettings();
  }, [session?.connected]);

  const saveSettings = async () => {
    if (!session?.connected) {
      return;
    }

    setSavingSettings(true);
    setError(null);

    try {
      await api.post('/settings/', {
        settings: {
          default_markup_percent: markupPercent ? Number(markupPercent) : null,
          duplicate_product_action: duplicateAction,
          import_default_status: importStatus,
        },
      });
    } catch (err) {
      setError((err as Error)?.message || 'Failed to save settings');
    } finally {
      setSavingSettings(false);
    }
  };

  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 12 }}>
        Settings
      </h1>

      <div style={{ maxWidth: 560 }}>
        <div
          style={{
            marginBottom: 16,
            padding: 20,
            border: '1px solid #e1e3e5',
            borderRadius: 8,
          }}
        >
          <h2 style={{ fontSize: 18, marginBottom: 8 }}>
            Shopify connection
          </h2>

          {session?.connected ? (
            <>
              <div
                style={{
                  padding: 12,
                  borderRadius: 6,
                  background: '#f0fdf4',
                  marginBottom: 16,
                }}
              >
                <strong>Shopify connected</strong>

                <div style={{ marginTop: 6, color: '#616161' }}>
                  Store: {session.shop_domain || 'Unknown'}
                </div>

                {session.scopes && (
                  <div
                    style={{
                      marginTop: 6,
                      color: '#616161',
                      fontSize: 12,
                    }}
                  >
                    Permissions: {session.scopes}
                  </div>
                )}
              </div>

              <button
                onClick={disconnectShopify}
                style={{
                  padding: '9px 14px',
                  border: '1px solid #d72c0d',
                  borderRadius: 6,
                  background: 'white',
                  color: '#d72c0d',
                  cursor: 'pointer',
                }}
              >
                Disconnect Shopify
              </button>
            </>
          ) : (
            <>
              <p
                style={{
                  color: '#616161',
                  marginBottom: 16,
                }}
              >
                Connect your Shopify store to let Mia AI access
                products, orders, customers, inventory, and marketing
                data.
              </p>

              <label
                htmlFor="shop-domain"
                style={{
                  display: 'block',
                  fontSize: 14,
                  fontWeight: 600,
                  marginBottom: 6,
                }}
              >
                Shopify store domain
              </label>

              <input
                id="shop-domain"
                type="text"
                value={shopDomain}
                onChange={(e) => setShopDomain(e.target.value)}
                placeholder="mystore.myshopify.com"
                disabled={connecting}
                style={{
                  width: '100%',
                  boxSizing: 'border-box',
                  padding: '10px 12px',
                  border: '1px solid #c9cccf',
                  borderRadius: 6,
                  marginBottom: 12,
                  fontSize: 14,
                }}
              />

              <button
                onClick={connectShopify}
                disabled={connecting}
                style={{
                  padding: '10px 16px',
                  border: 'none',
                  borderRadius: 6,
                  background: connecting ? '#8c9196' : '#008060',
                  color: 'white',
                  cursor: connecting ? 'default' : 'pointer',
                  fontWeight: 600,
                }}
              >
                {connecting ? 'Connecting…' : 'Connect Shopify'}
              </button>

              {error && (
                <div
                  style={{
                    marginTop: 12,
                    padding: 10,
                    borderRadius: 6,
                    background: '#fff4f4',
                    color: '#d72c0d',
                    fontSize: 14,
                  }}
                >
                  {error}
                </div>
              )}
            </>
          )}

          {status && (
            <div
              style={{
                marginTop: 20,
                paddingTop: 16,
                borderTop: '1px solid #e1e3e5',
                fontSize: 12,
                color: '#616161',
              }}
            >
              Mia AI Shopify integration:{' '}
              {status.configured ? 'Configured' : 'Not configured'}
            </div>
          )}
        </div>

        <div
          style={{
            padding: 20,
            border: '1px solid #e1e3e5',
            borderRadius: 8,
          }}
        >
          <h2 style={{ fontSize: 18, marginBottom: 8 }}>
            Application settings
          </h2>

          {session?.connected ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                saveSettings();
              }}
              style={{ display: 'grid', gap: 12 }}
            >
              <label style={{ fontSize: 14, fontWeight: 600 }}>
                Default markup percent
                <input
                  type="number"
                  value={markupPercent}
                  onChange={(e) => setMarkupPercent(e.target.value)}
                  style={{ marginLeft: 8, padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                />
              </label>

              <label style={{ fontSize: 14, fontWeight: 600 }}>
                Duplicate product action
                <select
                  value={duplicateAction}
                  onChange={(e) => setDuplicateAction(e.target.value)}
                  style={{ marginLeft: 8, padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                >
                  <option value="skip">Skip</option>
                  <option value="overwrite">Overwrite</option>
                  <option value="create_variant">Create variant</option>
                </select>
              </label>

              <label style={{ fontSize: 14, fontWeight: 600 }}>
                Import status
                <select
                  value={importStatus}
                  onChange={(e) => setImportStatus(e.target.value)}
                  style={{ marginLeft: 8, padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                >
                  <option value="DRAFT">Draft</option>
                  <option value="ACTIVE">Active</option>
                  <option value="ARCHIVED">Archived</option>
                </select>
              </label>

              <button
                type="submit"
                disabled={savingSettings}
                style={{
                  padding: '9px 14px',
                  borderRadius: 6,
                  border: 'none',
                  background: savingSettings ? '#8c9196' : '#008060',
                  color: 'white',
                  cursor: savingSettings ? 'not-allowed' : 'pointer',
                  fontWeight: 600,
                }}
              >
                {savingSettings ? 'Saving…' : 'Save settings'}
              </button>
            </form>
          ) : (
            <p style={{ color: '#616161' }}>
              Connect Shopify to persist import settings and margin defaults.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
