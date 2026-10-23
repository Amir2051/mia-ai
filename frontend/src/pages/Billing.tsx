import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  BlockStack,
  Box,
  Button,
  Card,
  InlineStack,
  Layout,
  List,
  Page,
  Text,
} from '@shopify/polaris';
import api from '../services/api';
import { ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage } from '../lib/format';
import { showToast } from '../lib/toast';
import { useSession } from '../lib/session';

type Plan = {
  id: string;
  name: string;
  amount: string;
  currency: string;
  interval: string;
  trial_days: number;
  features: string[];
};

type Subscription = {
  id: string;
  name: string;
  status: string;
  test: boolean;
  current_period_end?: string | null;
  pricing?: { price: { amount: string; currencyCode: string }; interval: string } | null;
} | null;

type BillingStatus = {
  connected: boolean;
  enforced: boolean;
  test_mode?: boolean;
  plans: Plan[];
  subscription: Subscription;
  entitled: boolean;
};

const intervalLabel = (interval: string) => (interval === 'ANNUAL' ? '/year' : '/month');

export default function Billing() {
  const session = useSession();
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.get('/billing/status');
      setStatus(res.data);
      setError('');
    } catch (err) {
      setError(errorMessage(err, 'Could not load billing status'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (session.connected) void load();
  }, [session.connected, load]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('billing') === 'return') {
      setLoading(true);
      showToast('Confirming your subscription...');
      void load();
    }
  }, [load]);

  const subscribe = async (planId: string) => {
    setBusy(planId);
    try {
      const res = await api.post('/billing/subscribe', { plan_id: planId });
      const url = res.data?.confirmation_url;
      if (!url) throw new Error('Shopify did not return a confirmation URL');
      window.open(url, '_top');
    } catch (err) {
      setError(errorMessage(err, 'Could not start checkout'));
      setBusy(null);
    }
  };

  if (session.loading || (session.connected && loading)) return <PageSkeleton title="Billing" />;
  if (!session.connected) return <NotConnected title="Billing" error={session.error} />;

  const sub = status?.subscription ?? null;
  const active = !!sub && String(sub.status).toUpperCase() === 'ACTIVE';

  return (
    <Page title="Billing" subtitle="Manage your Mia AI subscription">
      <Layout>
        <Layout.Section>
          <BlockStack gap="400">
            {error && <ErrorBanner message={error} />}
            {status?.test_mode && (
              <Box>
                <Badge tone="attention">Test billing mode - no real charges</Badge>
              </Box>
            )}

            {active && sub ? (
              <Card>
                <BlockStack gap="300">
                  <InlineStack align="space-between" blockAlign="center">
                    <Text as="h2" variant="headingMd">{sub.name}</Text>
                    <Badge tone="success">Active</Badge>
                  </InlineStack>
                  {sub.pricing && (
                    <Text as="p" tone="subdued">
                      {sub.pricing.price.amount} {sub.pricing.price.currencyCode}
                      {intervalLabel(sub.pricing.interval)}
                    </Text>
                  )}
                  {sub.current_period_end && (
                    <Text as="p" tone="subdued">
                      Renews {new Date(sub.current_period_end).toLocaleDateString()}
                    </Text>
                  )}
                  <Text as="p">
                    Your subscription is active. You can manage or cancel it anytime from your
                    Shopify admin under Settings &rarr; Billing.
                  </Text>
                </BlockStack>
              </Card>
            ) : (
              <BlockStack gap="400">
                {(status?.plans ?? []).map((plan) => (
                  <Card key={plan.id}>
                    <BlockStack gap="300">
                      <InlineStack align="space-between" blockAlign="center">
                        <Text as="h2" variant="headingMd">{plan.name}</Text>
                        <Text as="span" variant="headingLg">
                          {plan.amount} {plan.currency}
                          <Text as="span" tone="subdued">{intervalLabel(plan.interval)}</Text>
                        </Text>
                      </InlineStack>
                      {plan.trial_days > 0 && <Box><Badge tone="info">{`${plan.trial_days}-day free trial`}</Badge></Box>}
                      <List>
                        {plan.features.map((feature, i) => (
                          <List.Item key={i}>{feature}</List.Item>
                        ))}
                      </List>
                      <Button
                        variant="primary"
                        loading={busy === plan.id}
                        disabled={busy !== null}
                        onClick={() => subscribe(plan.id)}
                      >
                        {plan.trial_days > 0 ? `Start ${plan.trial_days}-day free trial` : 'Subscribe'}
                      </Button>
                      <Text as="p" tone="subdued" variant="bodySm">
                        Billed securely through Shopify. You will approve the charge in your Shopify admin.
                      </Text>
                    </BlockStack>
                  </Card>
                ))}
              </BlockStack>
            )}
          </BlockStack>
        </Layout.Section>
      </Layout>
    </Page>
  );
}
