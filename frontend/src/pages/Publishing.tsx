import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Banner,
  BlockStack,
  Box,
  Button,
  Card,
  Checkbox,
  FormLayout,
  InlineStack,
  Layout,
  Page,
  Select,
  SkeletonBodyText,
  Text,
  TextField,
  type BadgeProps,
} from '@shopify/polaris';
import api from '../services/api';
import { ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, formatDateTime } from '../lib/format';
import { showToast } from '../lib/toast';
import { useSession } from '../lib/session';

type ActionType = 'shopify_product_channels' | 'meta_facebook' | 'meta_instagram';

type PublishingAction = {
  id: number;
  action_type: ActionType;
  status: string;
  idempotency_key: string;
  payload: Record<string, any>;
  result: Record<string, any> | null;
  error: string | null;
  attempt_count: number;
  created_at: string | null;
  approved_at: string | null;
  executed_at: string | null;
};

const ACTION_LABELS: Record<ActionType, string> = {
  shopify_product_channels: 'Shopify sales channels',
  meta_facebook: 'Facebook Page',
  meta_instagram: 'Instagram',
};

const STATUS_TONES: Record<string, BadgeProps['tone']> = {
  pending_approval: 'attention',
  approved: 'info',
  publishing: 'info',
  published: 'success',
  failed: 'critical',
  needs_review: 'warning',
};

function statusLabel(status: string): string {
  return status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'string' ? value : String(value);
}

function newKey(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return `req-${crypto.randomUUID()}`;
    }
  } catch {
    // fall through to a non-crypto key
  }
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function destinationSummary(a: PublishingAction): string {
  if (a.action_type === 'shopify_product_channels') {
    const count = asArray(a.payload.publication_ids).length;
    const verb = a.payload.publish === false ? 'Unpublish from' : 'Publish to';
    return `${verb} ${count} sales channel${count === 1 ? '' : 's'}`;
  }
  if (a.action_type === 'meta_facebook') return `Facebook Page · account #${str(a.payload.account_id)}`;
  return `Instagram · account #${str(a.payload.account_id)}`;
}

export default function Publishing() {
  const session = useSession();
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [actions, setActions] = useState<PublishingAction[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const busyRef = useRef(false);

  const [actionType, setActionType] = useState<ActionType>('shopify_product_channels');
  const [idempotencyKey, setIdempotencyKey] = useState(newKey());
  const [productId, setProductId] = useState('');
  const [publicationIds, setPublicationIds] = useState('');
  const [publish, setPublish] = useState(true);
  const [accountId, setAccountId] = useState('');
  const [message, setMessage] = useState('');
  const [link, setLink] = useState('');
  const [imageUrl, setImageUrl] = useState('');
  const [caption, setCaption] = useState('');
  const [formError, setFormError] = useState('');
  const [pages, setPages] = useState<Array<{ value: string; label: string }>>([]);

  const load = useCallback(async () => {
    try {
      const res = await api.get('/publishing/requests');
      const rows = res.data?.actions;
      setActions(Array.isArray(rows) ? (rows as PublishingAction[]) : []);
      try {
        const accRes = await api.get('/social/accounts');
        const accs = Array.isArray(accRes.data?.accounts) ? accRes.data.accounts : [];
        setPages(
          accs
            .filter((a: any) => a.account_type === 'facebook_page')
            .map((a: any) => ({ value: String(a.id), label: `${a.account_name ?? 'Facebook Page'} (#${a.id})` })),
        );
      } catch {
        setPages([]);
      }
      setListError('');
    } catch (err) {
      setListError(errorMessage(err, 'Could not load publishing requests'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (session.connected) void load();
  }, [session.connected, load]);

  const selected = useMemo(
    () => actions.find((a) => a.id === selectedId) ?? null,
    [actions, selectedId],
  );

  const refreshOne = useCallback(async (id: number): Promise<PublishingAction | null> => {
    const res = await api.get(`/publishing/requests/${id}`);
    const updated = res.data?.action as PublishingAction | undefined;
    if (updated) setActions((prev) => prev.map((a) => (a.id === id ? updated : a)));
    return updated ?? null;
  }, []);

  function buildPayload(): Record<string, any> | null {
    setFormError('');
    if (actionType === 'shopify_product_channels') {
      if (!productId.startsWith('gid://shopify/Product/')) {
        setFormError('Enter a Shopify product ID like gid://shopify/Product/123.');
        return null;
      }
      const ids = publicationIds.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
      if (ids.length < 1 || ids.length > 50) {
        setFormError('Enter between 1 and 50 sales-channel IDs.');
        return null;
      }
      if (ids.some((p) => !p.startsWith('gid://shopify/Publication/'))) {
        setFormError('Each sales channel must be a gid://shopify/Publication/… ID.');
        return null;
      }
      return { product_id: productId.trim(), publication_ids: ids, publish };
    }
    const acct = Number(accountId);
    if (!Number.isInteger(acct) || acct < 1) {
      setFormError('Select a connected Facebook Page.');
      return null;
    }
    if (actionType === 'meta_facebook') {
      if (!message.trim() || message.length > 63206) {
        setFormError('Enter a message (1–63,206 characters).');
        return null;
      }
      const body: Record<string, any> = { account_id: acct, message: message.trim() };
      if (link.trim()) {
        try {
          const u = new URL(link.trim());
          if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password) throw new Error('bad');
        } catch {
          setFormError('Link must be a valid HTTP(S) URL without embedded credentials.');
          return null;
        }
        body.link = link.trim();
      }
      return body;
    }
    let okUrl = false;
    try {
      const u = new URL(imageUrl.trim());
      okUrl = u.protocol === 'https:' && !u.username && !u.password;
    } catch {
      okUrl = false;
    }
    if (!okUrl || imageUrl.length > 2048) {
      setFormError('Instagram requires a public HTTPS image URL.');
      return null;
    }
    if (caption.length > 2200) {
      setFormError('Instagram captions must be 2,200 characters or fewer.');
      return null;
    }
    return { account_id: acct, image_url: imageUrl.trim(), caption };
  }

  const resetDraft = useCallback(() => {
    setProductId('');
    setPublicationIds('');
    setPublish(true);
    setAccountId('');
    setMessage('');
    setLink('');
    setImageUrl('');
    setCaption('');
    setIdempotencyKey(newKey());
    setFormError('');
  }, []);

  const submit = async () => {
    if (busyRef.current) return;
    const payload = buildPayload();
    if (!payload) return;
    busyRef.current = true;
    setBusyAction('create');
    try {
      const res = await api.post('/publishing/requests', {
        action_type: actionType,
        idempotency_key: idempotencyKey,
        payload,
      });
      const action = res.data?.action as PublishingAction | undefined;
      showToast(res.data?.created === false ? 'A matching request already exists.' : 'Request created — nothing published yet.');
      await load();
      if (action?.id) setSelectedId(action.id);
      resetDraft();
    } catch (err) {
      setFormError(errorMessage(err, 'Could not create the publishing request'));
    } finally {
      busyRef.current = false;
      setBusyAction(null);
    }
  };

  const approve = async (id: number) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusyAction(`approve-${id}`);
    try {
      const res = await api.post(`/publishing/requests/${id}/approve`);
      const updated = res.data?.action as PublishingAction | undefined;
      if (updated) setActions((prev) => prev.map((a) => (a.id === id ? updated : a)));
      showToast('Approved. Nothing is published until you run Execute.');
    } catch (err) {
      await refreshOne(id).catch(() => null);
      setListError(errorMessage(err, 'Could not approve the request'));
    } finally {
      busyRef.current = false;
      setBusyAction(null);
    }
  };

  const execute = async (id: number) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusyAction(`execute-${id}`);
    try {
      const res = await api.post(`/publishing/requests/${id}/execute`);
      const updated = res.data?.action as PublishingAction | undefined;
      if (updated) setActions((prev) => prev.map((a) => (a.id === id ? updated : a)));
      if (updated?.status === 'published') showToast('Published.');
    } catch (err) {
      // Execute failures (failed / needs_review / conflict) come back as errors.
      // Read the authoritative server status instead of assuming an outcome, and
      // never automatically retry an uncertain publish.
      const updated = await refreshOne(id).catch(() => null);
      if (updated?.status === 'needs_review') {
        setListError('The provider outcome could not be confirmed. Check the channel directly before any further attempt — Mia will not retry automatically.');
      } else {
        setListError(errorMessage(err, 'Execution did not complete'));
      }
    } finally {
      busyRef.current = false;
      setBusyAction(null);
    }
  };

  if (session.loading) return <PageSkeleton title="Publishing" />;
  if (!session.connected) return <NotConnected title="Publishing" error={session.error} />;

  const canApprove = selected?.status === 'pending_approval';
  const canExecute = selected?.status === 'approved';

  return (
    <Page title="Publishing" subtitle="Create a request, approve it, then publish to your connected channels">
      <Layout>
        <Layout.Section>
          <Card>
            <BlockStack gap="400">
              <Text as="h2" variant="headingMd">New publishing request</Text>
              {formError && <ErrorBanner message={formError} title="Check the request" />}
              <FormLayout>
                <Select
                  label="Destination"
                  options={[
                    { label: 'Shopify sales channels', value: 'shopify_product_channels' },
                    { label: 'Facebook Page', value: 'meta_facebook' },
                    { label: 'Instagram', value: 'meta_instagram' },
                  ]}
                  value={actionType}
                  onChange={(v) => { setActionType(v as ActionType); setFormError(''); }}
                />
                {actionType === 'shopify_product_channels' && (
                  <>
                    <TextField label="Product ID" value={productId} onChange={setProductId} autoComplete="off" placeholder="gid://shopify/Product/123" />
                    <TextField label="Sales channel IDs" helpText="One or more gid://shopify/Publication/… IDs, separated by commas." value={publicationIds} onChange={setPublicationIds} autoComplete="off" multiline={2} />
                    <Checkbox label="Publish to these channels (uncheck to unpublish)" checked={publish} onChange={setPublish} />
                  </>
                )}
                {actionType === 'meta_facebook' && (
                  <>
                    <Select label="Facebook Page" options={pages.length ? [{ label: 'Select a connected Page…', value: '' }, ...pages] : [{ label: 'No Pages connected — connect one in the Social tab', value: '' }]} value={accountId} onChange={setAccountId} disabled={pages.length === 0} helpText={pages.length ? 'Choose one of your connected Facebook Pages.' : 'Connect a Facebook Page in the Social tab first.'} />
                    <TextField label="Message" value={message} onChange={setMessage} autoComplete="off" multiline={4} maxLength={63206} showCharacterCount />
                    <TextField label="Link (optional)" value={link} onChange={setLink} autoComplete="off" placeholder="https://your-store.example/product" />
                  </>
                )}
                {actionType === 'meta_instagram' && (
                  <>
                    <Select label="Facebook Page" options={pages.length ? [{ label: 'Select a connected Page…', value: '' }, ...pages] : [{ label: 'No Pages connected — connect one in the Social tab', value: '' }]} value={accountId} onChange={setAccountId} disabled={pages.length === 0} helpText={pages.length ? 'Instagram publishes through its linked Facebook Page.' : 'Connect a Facebook Page in the Social tab first.'} />
                    <TextField label="Image URL" value={imageUrl} onChange={setImageUrl} autoComplete="off" placeholder="https://…/image.jpg" />
                    <TextField label="Caption (optional)" value={caption} onChange={setCaption} autoComplete="off" multiline={3} maxLength={2200} showCharacterCount />
                  </>
                )}
                <InlineStack gap="300" blockAlign="center">
                  <Button variant="primary" onClick={submit} loading={busyAction === 'create'} disabled={busyAction !== null}>Create request</Button>
                  <Text as="span" tone="subdued" variant="bodySm">Creating a request does not publish anything.</Text>
                </InlineStack>
              </FormLayout>
            </BlockStack>
          </Card>
        </Layout.Section>

        <Layout.Section>
          <Card>
            <BlockStack gap="400">
              <InlineStack align="space-between" blockAlign="center">
                <Text as="h2" variant="headingMd">Requests</Text>
                <Button onClick={() => { setLoading(true); void load(); }} disabled={loading} accessibilityLabel="Refresh requests">Refresh</Button>
              </InlineStack>
              {loading ? (
                <SkeletonBodyText lines={4} />
              ) : listError ? (
                <ErrorBanner message={listError} onRetry={() => { setListError(''); setLoading(true); void load(); }} />
              ) : actions.length === 0 ? (
                <Text as="p" tone="subdued">No publishing requests yet. Create one above; it waits here for your approval before anything is published.</Text>
              ) : (
                <BlockStack gap="300">
                  {actions.map((a) => (
                    <Box key={a.id} padding="300" borderWidth="025" borderColor="border" borderRadius="200">
                      <InlineStack align="space-between" blockAlign="center" gap="300" wrap={false}>
                        <BlockStack gap="050">
                          <Text as="span" variant="headingSm">{ACTION_LABELS[a.action_type]}</Text>
                          <Text as="span" tone="subdued" variant="bodySm">{destinationSummary(a)} · {formatDateTime(a.created_at)}</Text>
                        </BlockStack>
                        <InlineStack gap="300" blockAlign="center">
                          <Badge tone={STATUS_TONES[a.status]}>{statusLabel(a.status)}</Badge>
                          <Button onClick={() => setSelectedId(a.id)} accessibilityLabel={`Review request ${a.id}`}>Review</Button>
                        </InlineStack>
                      </InlineStack>
                    </Box>
                  ))}
                </BlockStack>
              )}
            </BlockStack>
          </Card>
        </Layout.Section>

        {selected && (
          <Layout.Section>
            <Card>
              <BlockStack gap="400">
                <InlineStack align="space-between" blockAlign="center">
                  <Text as="h2" variant="headingMd">Review request #{selected.id}</Text>
                  <Badge tone={STATUS_TONES[selected.status]}>{statusLabel(selected.status)}</Badge>
                </InlineStack>

                {selected.status === 'needs_review' && (
                  <Banner tone="warning" title="Provider outcome not confirmed">
                    <p>Mia could not confirm whether this was published. Check the channel directly before any further attempt. Mia will not retry automatically; if it did publish, creating a new request would post it again.</p>
                  </Banner>
                )}

                <BlockStack gap="200">
                  <Text as="span" variant="headingSm">Destination</Text>
                  <Text as="p">{ACTION_LABELS[selected.action_type]} — {destinationSummary(selected)}</Text>
                </BlockStack>

                <BlockStack gap="200">
                  <Text as="span" variant="headingSm">Content to be published</Text>
                  {selected.action_type === 'shopify_product_channels' ? (
                    <BlockStack gap="100">
                      <Text as="p"><Text as="span" fontWeight="semibold">Product: </Text>{str(selected.payload.product_id)}</Text>
                      <Text as="p"><Text as="span" fontWeight="semibold">Channels: </Text>{asArray(selected.payload.publication_ids).map(String).join(', ')}</Text>
                      <Text as="p"><Text as="span" fontWeight="semibold">Action: </Text>{selected.payload.publish === false ? 'Unpublish' : 'Publish'}</Text>
                    </BlockStack>
                  ) : selected.action_type === 'meta_facebook' ? (
                    <BlockStack gap="100">
                      <Text as="p" breakWord>{str(selected.payload.message)}</Text>
                      {selected.payload.link ? <Text as="p" tone="subdued" breakWord>{str(selected.payload.link)}</Text> : null}
                    </BlockStack>
                  ) : (
                    <BlockStack gap="100">
                      <Text as="p" tone="subdued" breakWord>{str(selected.payload.image_url)}</Text>
                      {selected.payload.caption ? <Text as="p" breakWord>{str(selected.payload.caption)}</Text> : null}
                    </BlockStack>
                  )}
                </BlockStack>

                {selected.result && (
                  <BlockStack gap="100">
                    <Text as="span" variant="headingSm">Result</Text>
                    {str(selected.result.external_id) ? <Text as="p">Provider reference: {str(selected.result.external_id)}</Text> : null}
                    {selected.result.publication_count != null ? <Text as="p">{str(selected.result.publication_count)} channel(s) updated</Text> : null}
                  </BlockStack>
                )}

                {selected.error && selected.status !== 'needs_review' && (
                  <Text as="p" tone="critical">{selected.error}</Text>
                )}

                <Text as="p" tone="subdued" variant="bodySm">
                  Created {formatDateTime(selected.created_at)}
                  {selected.approved_at ? ` · Approved ${formatDateTime(selected.approved_at)}` : ''}
                  {selected.executed_at ? ` · Executed ${formatDateTime(selected.executed_at)}` : ''}
                </Text>

                <InlineStack gap="300" blockAlign="center">
                  <Button
                    onClick={() => { if (canApprove) void approve(selected.id); }}
                    loading={busyAction === `approve-${selected.id}`}
                    disabled={!canApprove || busyAction !== null}
                  >Approve</Button>
                  <Button
                    variant="primary"
                    tone="success"
                    onClick={() => { if (canExecute) void execute(selected.id); }}
                    loading={busyAction === `execute-${selected.id}`}
                    disabled={!canExecute || busyAction !== null}
                  >Execute (publish now)</Button>
                  <Button variant="plain" onClick={() => setSelectedId(null)}>Close</Button>
                </InlineStack>
                {!canApprove && !canExecute && (
                  <Text as="span" tone="subdued" variant="bodySm">
                    {selected.status === 'published'
                      ? 'This request has been published.'
                      : selected.status === 'failed'
                        ? 'This request failed and cannot be retried; create a new request.'
                        : selected.status === 'needs_review'
                          ? 'Reconcile the provider outcome before creating any new request.'
                          : 'No actions available for this status.'}
                  </Text>
                )}
              </BlockStack>
            </Card>
          </Layout.Section>
        )}
      </Layout>
    </Page>
  );
}
