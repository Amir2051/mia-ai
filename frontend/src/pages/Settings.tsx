import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  Banner,
  BlockStack,
  Box,
  Button,
  Card,
  FormLayout,
  InlineGrid,
  InlineStack,
  Layout,
  Modal,
  Page,
  Select,
  Tag,
  Text,
} from '@shopify/polaris';
import api from '../services/api';
import { ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage } from '../lib/format';
import { showToast } from '../lib/toast';
import { useSession } from '../lib/session';

const DUPLICATE_OPTIONS = [
  { label: 'Skip it', value: 'skip' },
  { label: 'Update the existing product', value: 'update' },
  { label: 'Create it anyway', value: 'create' },
];
const STATUS_OPTIONS = [
  { label: 'Draft', value: 'DRAFT' },
  { label: 'Active', value: 'ACTIVE' },
];

type Form = { markup: string; duplicate: string; status: string };
type SalesChannel = { id: string; name: string; supportsFuturePublishing?: boolean };
type HealthProvider = {
  provider: string;
  label: string;
  status: string;
  configured: boolean;
  saved_accounts: number;
  message: string;
};
type HealthReport = {
  connected: boolean;
  overall_status: string;
  database: { status: string };
  shopify: {
    status: string;
    shop_domain?: string;
    shop_name?: string | null;
    granted_scopes?: string[];
    missing_scopes?: string[];
    error?: string | null;
  };
  ai: { status: string; provider?: string; model?: string; message?: string };
  social: HealthProvider[];
  checked_at?: string;
};

function healthTone(status: string): 'success' | 'info' | 'warning' | 'critical' {
  if (status === 'healthy' || status === 'verified') return 'success';
  if (status === 'configured_not_tested') return 'info';
  if (status === 'unavailable') return 'critical';
  return 'warning';
}

function healthLabel(status: string): string {
  const labels: Record<string, string> = {
    healthy: 'Healthy',
    verified: 'Live check passed',
    verified_with_permission_gaps: 'Connected · permissions need review',
    configured_not_tested: 'Configured · not live-tested',
    saved_account_not_verified: 'Account saved · not verified',
    not_configured: 'Not configured',
    needs_reauth: 'Session needs refresh',
    permission_issue: 'Permission issue',
    unavailable: 'Unavailable',
    not_connected: 'Not connected',
    not_checked: 'Not checked',
  };
  return labels[status] ?? status.replace(/_/g, ' ');
}

function fromSettings(settings: Record<string, unknown>): Form {
  const duplicate = settings.duplicate_product_action === 'overwrite' ? 'update' : String(settings.duplicate_product_action || 'skip');
  return {
    markup: settings.default_markup_percent != null ? String(settings.default_markup_percent) : '',
    duplicate: DUPLICATE_OPTIONS.some((option) => option.value === duplicate) ? duplicate : 'skip',
    status: settings.import_default_status === 'ACTIVE' ? 'ACTIVE' : 'DRAFT',
  };
}

export default function Settings() {
  const session = useSession();
  const [saved, setSaved] = useState<Form>({ markup: '', duplicate: 'skip', status: 'DRAFT' });
  const [form, setForm] = useState<Form>(saved);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [salesChannels, setSalesChannels] = useState<SalesChannel[]>([]);
  const [channelsLoading, setChannelsLoading] = useState(true);
  const [channelsError, setChannelsError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [healthError, setHealthError] = useState<string | null>(null);

  const runHealthChecks = useCallback(async () => {
    setHealthLoading(true);
    setHealthError(null);
    try {
      let response = await api.get('/settings/health');
      if (response.data?.shopify?.status === 'needs_reauth') {
        await api.get('/auth/session?force=true');
        response = await api.get('/settings/health');
      }
      setHealth(response.data as HealthReport);
    } catch (err) {
      setHealthError(errorMessage(err, 'Could not run integration health checks'));
    } finally {
      setHealthLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!session.connected) return;
    void runHealthChecks();
    api.get('/sales-channels')
      .then((res) => setSalesChannels((res.data?.sales_channels ?? []) as SalesChannel[]))
      .catch((err) => setChannelsError(errorMessage(err, 'Could not read Shopify sales channels')))
      .finally(() => setChannelsLoading(false));
  }, [session.connected, runHealthChecks]);

  useEffect(() => {
    if (!session.connected) return;
    api
      .get('/settings/')
      .then((res) => {
        const value = fromSettings(res.data?.settings || {});
        setSaved(value);
        setForm(value);
      })
      .catch((err) => setError(errorMessage(err, 'Could not load your settings')))
      .finally(() => setLoading(false));
  }, [session.connected]);

  if (session.loading || (session.connected && loading)) return <PageSkeleton title="Settings" />;
  if (!session.connected) return <NotConnected title="Settings" error={session.error} />;

  const dirty = JSON.stringify(form) !== JSON.stringify(saved);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.post('/settings/', {
        settings: {
          // Not editable yet (nothing uses it); send back what's stored.
          default_markup_percent: form.markup === '' ? null : Number(form.markup),
          duplicate_product_action: form.duplicate,
          import_default_status: form.status,
        },
      });
      setSaved(form);
      showToast('Settings saved');
    } catch (err) {
      setError(errorMessage(err, 'Could not save your settings'));
    } finally {
      setSaving(false);
    }
  };

  const disconnect = async () => {
    setDisconnecting(true);
    try {
      await api.post('/auth/logout');
      setDisconnectOpen(false);
      session.refresh();
    } catch (err) {
      setDisconnectOpen(false);
      setError(errorMessage(err, 'Could not disconnect'));
    } finally {
      setDisconnecting(false);
    }
  };

  const scopes = (session.scopes || '').split(',').map((scope) => scope.trim()).filter(Boolean);

  return (
    <Page
      title="Settings"
      primaryAction={{ content: 'Save', onAction: save, loading: saving, disabled: !dirty }}
      secondaryActions={dirty ? [{ content: 'Discard', onAction: () => setForm(saved) }] : undefined}
    >
      <BlockStack gap="500">
        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
        <Layout>
          <Layout.AnnotatedSection title="Import defaults" description="Used whenever you start a new CSV import. You can still change them per import.">
            <Card>
              <FormLayout>
                <Select label="If a product already exists" options={DUPLICATE_OPTIONS} value={form.duplicate} onChange={(value) => setForm({ ...form, duplicate: value })} />
                <Select
                  label="Status for imported products"
                  options={STATUS_OPTIONS}
                  value={form.status}
                  onChange={(value) => setForm({ ...form, status: value })}
                  helpText="Draft keeps new products hidden until you review them."
                />
              </FormLayout>
            </Card>
          </Layout.AnnotatedSection>

          <Layout.AnnotatedSection title="Shopify connection" description="Mia reads and updates your store only with the permissions you approved.">
            <Card>
              <BlockStack gap="300">
                <InlineStack align="space-between" blockAlign="center">
                  <Text as="span" fontWeight="semibold">{session.shopDomain}</Text>
                  <Badge tone="success">Connected</Badge>
                </InlineStack>
                {scopes.length > 0 && (
                  <BlockStack gap="100">
                    <Text as="span" variant="bodySm" tone="subdued">Permissions</Text>
                    <InlineStack gap="100" wrap>{scopes.map((scope) => <Tag key={scope}>{scope.replace(/_/g, ' ')}</Tag>)}</InlineStack>
                  </BlockStack>
                )}
                <Box paddingBlockStart="200">
                  <InlineStack>
                    <Text as="span" tone="subdued" variant="bodySm">
                      To remove Mia completely, uninstall it from Settings › Apps in your Shopify admin.{' '}
                    </Text>
                  </InlineStack>
                </Box>
                <InlineStack>
                  <Button variant="plain" tone="critical" onClick={() => setDisconnectOpen(true)}>
                    Sign Mia out of this store
                  </Button>
                </InlineStack>
              </BlockStack>
            </Card>
          </Layout.AnnotatedSection>

          <Layout.AnnotatedSection title="Integration health" description="Run read-only diagnostics. A green Shopify status means the app completed a live Shopify API check; optional providers are reported honestly as configured or saved, not assumed to be working.">
            <Card>
              <BlockStack gap="300">
                <InlineStack align="space-between" blockAlign="center">
                  <Text as="span" fontWeight="semibold">Connection diagnostics</Text>
                  <InlineStack gap="200" blockAlign="center">
                    {health && <Badge tone={healthTone(health.overall_status)}>{healthLabel(health.overall_status)}</Badge>}
                    <Button onClick={() => void runHealthChecks()} loading={healthLoading}>Run checks</Button>
                  </InlineStack>
                </InlineStack>
                {healthError && <ErrorBanner message={healthError} onRetry={() => void runHealthChecks()} />}
                {healthLoading && !health ? (
                  <Text as="p" tone="subdued">Checking database and Shopify connectivity…</Text>
                ) : health ? (
                  <BlockStack gap="300">
                    <InlineGrid columns={{ xs: 1, md: 3 }} gap="300">
                      <Card>
                        <BlockStack gap="200">
                          <Text as="h3" variant="headingSm">Database</Text>
                          <Badge tone={healthTone(health.database.status)}>{healthLabel(health.database.status)}</Badge>
                          <Text as="p" variant="bodySm" tone="subdued">Readiness query: SELECT 1</Text>
                        </BlockStack>
                      </Card>
                      <Card>
                        <BlockStack gap="200">
                          <Text as="h3" variant="headingSm">Shopify Admin API</Text>
                          <Badge tone={healthTone(health.shopify.status)}>{healthLabel(health.shopify.status)}</Badge>
                          <Text as="p" variant="bodySm">{health.shopify.shop_name || health.shopify.shop_domain || session.shopDomain}</Text>
                          {health.shopify.error && <Text as="p" variant="bodySm" tone="critical">{health.shopify.error}</Text>}
                          {(health.shopify.missing_scopes ?? []).length > 0 && (
                            <BlockStack gap="100">
                              <Text as="p" variant="bodySm" tone="subdued">Missing requested permissions</Text>
                              <InlineStack gap="100" wrap>
                                {health.shopify.missing_scopes?.map((scope) => <Tag key={scope}>{scope}</Tag>)}
                              </InlineStack>
                            </BlockStack>
                          )}
                        </BlockStack>
                      </Card>
                      <Card>
                        <BlockStack gap="200">
                          <Text as="h3" variant="headingSm">AI response provider</Text>
                          <Badge tone={healthTone(health.ai.status)}>{healthLabel(health.ai.status)}</Badge>
                          <Text as="p" variant="bodySm">{health.ai.model || 'Model not configured'}</Text>
                          <Text as="p" variant="bodySm" tone="subdued">{health.ai.message}</Text>
                        </BlockStack>
                      </Card>
                    </InlineGrid>
                    <Text as="h3" variant="headingSm">Social providers</Text>
                    <InlineGrid columns={{ xs: 1, md: 3 }} gap="300">
                      {health.social.map((provider) => (
                        <Card key={provider.provider}>
                          <BlockStack gap="200">
                            <Text as="h3" variant="headingSm">{provider.label}</Text>
                            <Badge tone={healthTone(provider.status)}>{healthLabel(provider.status)}</Badge>
                            <Text as="p" variant="bodySm">{provider.saved_accounts} saved account{provider.saved_accounts === 1 ? '' : 's'}</Text>
                            <Text as="p" variant="bodySm" tone="subdued">{provider.message}</Text>
                          </BlockStack>
                        </Card>
                      ))}
                    </InlineGrid>
                    <Banner tone="info" title="What these checks do">
                      <Text as="p">Shopify is tested with a live, read-only API request. OpenRouter is not called, to avoid spending credits. Social provider tokens are not probed here; use the Social page’s connection test before publishing.</Text>
                    </Banner>
                    {health.checked_at && <Text as="p" variant="bodySm" tone="subdued">Last checked: {new Date(health.checked_at).toLocaleString()}</Text>}
                  </BlockStack>
                ) : (
                  <Text as="p" tone="subdued">No diagnostic results yet.</Text>
                )}
              </BlockStack>
            </Card>
          </Layout.AnnotatedSection>

          <Layout.AnnotatedSection title="Sales channels" description="Channels Mia can read from Shopify. Facebook publishing additionally requires a connected Meta account and eligible Page permissions.">
            <Card>
              <BlockStack gap="300">
                <InlineStack align="space-between" blockAlign="center">
                  <Text as="span" fontWeight="semibold">Shopify sales channels</Text>
                  {channelsLoading ? <Badge>Loading</Badge> : <Badge tone={channelsError ? 'critical' : 'success'}>{channelsError ? 'Needs attention' : `${salesChannels.length} found`}</Badge>}
                </InlineStack>
                {channelsError && <ErrorBanner message={channelsError} onDismiss={() => setChannelsError(null)} />}
                {channelsLoading ? (
                  <Text as="p" tone="subdued">Reading sales channels from Shopify…</Text>
                ) : channelsError ? (
                  <Text as="p" tone="subdued">Check the Shopify permissions above, then reopen Mia from Shopify Admin to refresh its session.</Text>
                ) : salesChannels.length ? (
                  <InlineStack gap="200" wrap>
                    {salesChannels.map((channel) => <Tag key={channel.id}>{channel.name}</Tag>)}
                  </InlineStack>
                ) : (
                  <Text as="p" tone="subdued">Shopify returned no sales channels for this store.</Text>
                )}
              </BlockStack>
            </Card>
          </Layout.AnnotatedSection>
        </Layout>
      </BlockStack>

      <Modal
        open={disconnectOpen}
        onClose={() => setDisconnectOpen(false)}
        title="Sign Mia out of this store?"
        primaryAction={{ content: 'Sign out', destructive: true, onAction: disconnect, loading: disconnecting }}
        secondaryActions={[{ content: 'Cancel', onAction: () => setDisconnectOpen(false) }]}
      >
        <Modal.Section>
          <Text as="p">Mia stops reading your store until you open it again from your Shopify admin. Your settings are kept.</Text>
        </Modal.Section>
      </Modal>
    </Page>
  );
}
