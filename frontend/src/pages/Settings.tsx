import { useEffect, useState } from 'react';
import {
  Badge,
  BlockStack,
  Box,
  Button,
  Card,
  FormLayout,
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
  if (!session.connected) return <NotConnected title="Settings" />;

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
