import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Banner, BlockStack, Box, Button, Card, Page, Text, TextField } from '@shopify/polaris';
import api from '../services/api';
import { errorMessage } from '../lib/format';
import { useSession } from '../lib/session';

export default function Login() {
  const session = useSession();
  const navigate = useNavigate();
  const [shop, setShop] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (session.connected) navigate('/dashboard', { replace: true });
  }, [session.connected, navigate]);

  const openShopify = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    let raw = shop.trim().toLowerCase();
    raw = raw.replace('https://', '').replace('http://', '');
    while (raw.endsWith('/')) raw = raw.slice(0, -1);
    const domain = raw.includes('.') ? raw : raw + '.myshopify.com';
    setLoading(true);
    try {
      const response = await api.get('/auth/login-url', { params: { shop: domain } });
      const loginUrl = response.data?.login_url;
      let target: URL;
      try {
        target = new URL(loginUrl);
      } catch {
        throw new Error('Mia did not return a valid Shopify login link.');
      }
      if (target.protocol !== 'https:' || !target.hostname.endsWith('.myshopify.com') || !target.pathname.startsWith('/admin/apps/')) {
        throw new Error('Mia could not create a valid Shopify login link.');
      }
      window.location.assign(loginUrl);
    } catch (err) {
      setError(errorMessage(err, 'Could not create your Shopify login link. Check the store domain and try again.'));
      setLoading(false);
    }
  };

  if (session.loading) {
    return <Page title="Sign in to Mia"><Card><Box padding="500"><Text as="p">Checking your Shopify session…</Text></Box></Card></Page>;
  }

  return (
    <Page title="Sign in to Mia" subtitle="Use the Shopify store where Mia is installed.">
      <Box maxWidth="640px">
        <BlockStack gap="400">
          {error && <Banner tone="critical" onDismiss={() => setError(null)}><p>{error}</p></Banner>}
          <Card>
            <form onSubmit={openShopify}>
              <BlockStack gap="400">
                <Text as="h2" variant="headingMd">Continue with Shopify</Text>
                <Text as="p" tone="subdued">Enter your store name or its myshopify.com domain. Mia will open through Shopify Admin, where your store session can be securely verified.</Text>
                <TextField
                  label="Shopify store"
                  value={shop}
                  onChange={setShop}
                  autoComplete="url"
                  placeholder="your-store.myshopify.com"
                  helpText="You can enter either your-store or your-store.myshopify.com."
                />
                <Button submit variant="primary" loading={loading} disabled={!shop.trim() || loading} fullWidth>Continue to Shopify</Button>
              </BlockStack>
            </form>
          </Card>
          <Text as="p" tone="subdued">If Mia is not installed yet, install it from your Shopify app listing first, then return here.</Text>
        </BlockStack>
      </Box>
    </Page>
  );
}
