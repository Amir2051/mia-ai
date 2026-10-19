import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  Badge,
  Banner,
  BlockStack,
  Box,
  Button,
  Card,
  Divider,
  FormLayout,
  InlineGrid,
  InlineStack,
  Layout,
  List,
  Modal,
  Page,
  ProgressBar,
  Select,
  Tag,
  Text,
  TextField,
  Thumbnail,
} from '@shopify/polaris';
import { EditIcon, MagicIcon, MegaphoneIcon, SearchIcon } from '@shopify/polaris-icons';
import api from '../services/api';
import SafeHtml from '../components/SafeHtml';
import { Field, MarketingCopy, scoreTone, type MarketingResult, type SeoResult } from '../components/MiaResults';
import { ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, formatNumber, formatPrice, humanize, productTone } from '../lib/format';
import { showToast } from '../lib/toast';
import { useSession } from '../lib/session';

type Variant = {
  id?: string | null;
  sku?: string | null;
  price?: string | null;
  inventoryQuantity?: number | null;
  selectedOptions?: Array<{ name?: string | null; value?: string | null }>;
};
type Product = {
  id?: string | null;
  title?: string | null;
  descriptionHtml?: string | null;
  status?: string | null;
  tags?: string | string[] | null;
  vendor?: string | null;
  productType?: string | null;
  variants?: { edges: Array<{ node: Variant }> };
  images?: { edges: Array<{ node: { id?: string | null; url?: string | null; altText?: string | null } }> };
};
type Analysis = { seo_score?: number; issues?: string[]; recommendations?: string[] };
type CurrentSeo = { title?: string; seo_title?: string; seo_description?: string; tags?: string[] };

const STATUS_OPTIONS = [
  { label: 'Active', value: 'ACTIVE' },
  { label: 'Draft', value: 'DRAFT' },
  { label: 'Archived', value: 'ARCHIVED' },
];

function tagList(tags: Product['tags']): string[] {
  if (!tags) return [];
  if (Array.isArray(tags)) return tags.filter(Boolean);
  return tags.split(',').map((tag) => tag.trim()).filter(Boolean);
}

export default function ProductDetail() {
  const { id = '' } = useParams();
  const productId = decodeURIComponent(id);
  const session = useSession();
  const [product, setProduct] = useState<Product | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [action, setAction] = useState<null | 'analyze' | 'enhance' | 'apply' | 'marketing' | 'save'>(null);

  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [proposal, setProposal] = useState<{ result: SeoResult; current: CurrentSeo } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [marketing, setMarketing] = useState<MarketingResult | null>(null);

  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ title: '', vendor: '', productType: '', status: '', descriptionHtml: '', tags: '' });
  const [variantEdits, setVariantEdits] = useState<Record<string, { price: string; inventoryQuantity: string }>>({});

  const resetForm = useCallback((data: Product) => {
    setForm({
      title: data.title || '',
      vendor: data.vendor || '',
      productType: data.productType || '',
      status: data.status || 'DRAFT',
      descriptionHtml: data.descriptionHtml || '',
      tags: tagList(data.tags).join(', '),
    });
    const edits: Record<string, { price: string; inventoryQuantity: string }> = {};
    (data.variants?.edges ?? []).forEach(({ node }) => {
      if (node.id) {
        edits[node.id] = {
          price: node.price ?? '',
          inventoryQuantity: node.inventoryQuantity != null ? String(node.inventoryQuantity) : '',
        };
      }
    });
    setVariantEdits(edits);
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .get(`/products/${encodeURIComponent(productId)}`)
      .then((res) => {
        const data = (res.data?.data as Product) || null;
        setProduct(data);
        if (data) resetForm(data);
      })
      .catch((err) => setError(errorMessage(err, 'Could not load this product')))
      .finally(() => setLoading(false));
  }, [productId, resetForm]);

  useEffect(() => {
    if (session.connected) load();
  }, [session.connected, load]);

  if (session.loading || (session.connected && loading && !product)) return <PageSkeleton withAside />;
  if (!session.connected) return <NotConnected title="Product" error={session.error} />;
  if (error || !product) {
    return (
      <Page backAction={{ content: 'Products', url: '/products' }} title="Product">
        <ErrorBanner message={error || 'This product could not be found.'} onRetry={load} />
      </Page>
    );
  }

  const variants = product.variants?.edges ?? [];
  const images = product.images?.edges ?? [];
  const tags = tagList(product.tags);

  async function analyze() {
    setAction('analyze');
    setActionError(null);
    try {
      const res = await api.post('/seo/analyze', { product_id: product!.id });
      setAnalysis(res.data);
    } catch (err) {
      setActionError(errorMessage(err, 'Mia could not analyze this product'));
    } finally {
      setAction(null);
    }
  }

  async function enhance() {
    setAction('enhance');
    setActionError(null);
    setProposal(null);
    try {
      const res = await api.post('/seo/generate', { product_id: product!.id });
      if (!res.data?.result) throw new Error('Mia returned no suggestions');
      setProposal({ result: res.data.result, current: res.data.product || {} });
    } catch (err) {
      setActionError(errorMessage(err, 'Mia could not generate suggestions'));
    } finally {
      setAction(null);
    }
  }

  async function applyProposal() {
    if (!proposal) return;
    setAction('apply');
    setActionError(null);
    const { result } = proposal;
    try {
      const res = await api.post('/seo/apply', {
        product_id: product!.id,
        changes: {
          title: result.optimized_title,
          descriptionHtml: result.optimized_description_html,
          seo: { title: result.seo_title, description: result.meta_description },
          tags: result.tags,
          image_alt_text: result.image_alt_text,
        },
        confirmed: true,
        apply_handle: false,
      });
      if (res.data?.success === false) throw new Error('Shopify did not accept the changes');
      setConfirmOpen(false);
      setProposal(null);
      showToast('Mia’s changes are live in Shopify');
      load();
    } catch (err) {
      setConfirmOpen(false);
      setActionError(errorMessage(err, 'Mia could not apply the changes'));
    } finally {
      setAction(null);
    }
  }

  async function createMarketing() {
    setAction('marketing');
    setActionError(null);
    try {
      const res = await api.post('/seo/marketing', { product_id: product!.id });
      setMarketing(res.data?.result || null);
    } catch (err) {
      setActionError(errorMessage(err, 'Mia could not create a campaign for this product'));
    } finally {
      setAction(null);
    }
  }

  async function save() {
    setAction('save');
    setActionError(null);
    const payload: Record<string, unknown> = {
      title: form.title || product!.title,
      vendor: form.vendor || undefined,
      productType: form.productType || undefined,
      status: form.status || product!.status,
      tags: form.tags.split(',').map((tag) => tag.trim()).filter(Boolean),
    };
    if (form.descriptionHtml !== (product!.descriptionHtml || '')) payload.descriptionHtml = form.descriptionHtml || undefined;

    const variantsInput = variants
      .map(({ node }) => {
        const edit = node.id ? variantEdits[node.id] : undefined;
        const patch: Record<string, unknown> = { id: node.id };
        if (edit && edit.price !== (node.price ?? '')) patch.price = edit.price || undefined;
        if (edit && edit.inventoryQuantity !== (node.inventoryQuantity != null ? String(node.inventoryQuantity) : '')) {
          patch.inventoryQuantity = edit.inventoryQuantity ? Number(edit.inventoryQuantity) : undefined;
        }
        return patch;
      })
      .filter((patch) => Object.keys(patch).length > 1);
    if (variantsInput.length) payload.variants = variantsInput;

    try {
      const res = await api.put(`/products/${encodeURIComponent(product!.id || productId)}`, { product: payload });
      const data = (res.data?.data || res.data) as Product;
      if (data?.id) {
        setProduct(data);
        resetForm(data);
      } else {
        load();
      }
      setEditing(false);
      showToast('Product saved');
    } catch (err) {
      setActionError(errorMessage(err, 'Could not save the product'));
    } finally {
      setAction(null);
    }
  }

  const busy = action !== null;

  return (
    <Page
      backAction={{ content: 'Products', url: '/products' }}
      title={product.title || 'Untitled product'}
      titleMetadata={<Badge tone={productTone(product.status)}>{humanize(product.status)}</Badge>}
      primaryAction={
        editing
          ? { content: 'Save', onAction: save, loading: action === 'save', disabled: busy && action !== 'save' }
          : { content: 'Improve with Mia', icon: MagicIcon, onAction: enhance, loading: action === 'enhance', disabled: busy }
      }
      secondaryActions={
        editing
          ? [{ content: 'Cancel', onAction: () => { setEditing(false); resetForm(product); }, disabled: action === 'save' }]
          : [
              { content: 'Check SEO', icon: SearchIcon, onAction: analyze, loading: action === 'analyze', disabled: busy },
              { content: 'Create campaign', icon: MegaphoneIcon, onAction: createMarketing, loading: action === 'marketing', disabled: busy },
              { content: 'Edit', icon: EditIcon, onAction: () => setEditing(true), disabled: busy },
            ]
      }
    >
      <Layout>
        <Layout.Section>
          <BlockStack gap="400">
            {actionError && <ErrorBanner message={actionError} onDismiss={() => setActionError(null)} />}

            {analysis && (
              <Card>
                <BlockStack gap="300">
                  <InlineStack align="space-between" blockAlign="center">
                    <Text as="h2" variant="headingMd">
                      SEO check
                    </Text>
                    <Badge tone={scoreTone(analysis.seo_score)}>{`${analysis.seo_score ?? 0}/100`}</Badge>
                  </InlineStack>
                  <ProgressBar progress={analysis.seo_score ?? 0} tone={(analysis.seo_score ?? 0) >= 80 ? 'success' : (analysis.seo_score ?? 0) >= 60 ? 'highlight' : 'critical'} size="small" />
                  {!!analysis.issues?.length && (
                    <BlockStack gap="100">
                      <Text as="h3" variant="headingSm">Issues</Text>
                      <List>{analysis.issues.map((issue) => <List.Item key={issue}>{issue}</List.Item>)}</List>
                    </BlockStack>
                  )}
                  {!!analysis.recommendations?.length && (
                    <BlockStack gap="100">
                      <Text as="h3" variant="headingSm">Recommendations</Text>
                      <List>{analysis.recommendations.map((item) => <List.Item key={item}>{item}</List.Item>)}</List>
                    </BlockStack>
                  )}
                  <InlineStack>
                    <Button icon={MagicIcon} onClick={enhance} loading={action === 'enhance'} disabled={busy}>
                      Fix with Mia
                    </Button>
                  </InlineStack>
                </BlockStack>
              </Card>
            )}

            {proposal && (
              <Card>
                <BlockStack gap="400">
                  <InlineStack align="space-between" blockAlign="center" gap="200">
                    <InlineStack gap="200" blockAlign="center">
                      <Text as="h2" variant="headingMd">
                        Mia’s suggestions
                      </Text>
                      <Badge tone="magic">AI</Badge>
                    </InlineStack>
                    {typeof proposal.result.seo_score === 'number' && (
                      <Badge tone={scoreTone(proposal.result.seo_score)}>{`Projected score ${proposal.result.seo_score}/100`}</Badge>
                    )}
                  </InlineStack>
                  <InlineGrid columns={{ xs: 1, md: 2 }} gap="400">
                    <BlockStack gap="300">
                      <Text as="h3" variant="headingSm" tone="subdued">Now</Text>
                      <Field label="Product title" value={proposal.current.title || product.title} />
                      <Field label="SEO title" value={proposal.current.seo_title} />
                      <Field label="Meta description" value={proposal.current.seo_description} />
                    </BlockStack>
                    <BlockStack gap="300">
                      <Text as="h3" variant="headingSm">Suggested</Text>
                      <Field label="Product title" value={proposal.result.optimized_title} />
                      <Field label="SEO title" value={proposal.result.seo_title} />
                      <Field label="Meta description" value={proposal.result.meta_description} />
                    </BlockStack>
                  </InlineGrid>
                  {!!proposal.result.tags?.length && (
                    <InlineStack gap="100" wrap>
                      {proposal.result.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)}
                    </InlineStack>
                  )}
                  <Divider />
                  <BlockStack gap="200">
                    <Text as="h3" variant="headingSm">Suggested description</Text>
                    <SafeHtml html={proposal.result.optimized_description_html} />
                  </BlockStack>
                  <InlineStack gap="200" blockAlign="center">
                    <Button variant="primary" onClick={() => setConfirmOpen(true)} disabled={busy}>
                      Review and apply
                    </Button>
                    <Button onClick={enhance} loading={action === 'enhance'} disabled={busy}>
                      Regenerate
                    </Button>
                    <Button variant="tertiary" onClick={() => setProposal(null)} disabled={busy}>
                      Discard
                    </Button>
                  </InlineStack>
                </BlockStack>
              </Card>
            )}

            {marketing && (
              <BlockStack gap="300">
                <InlineStack align="space-between" blockAlign="center">
                  <Text as="h2" variant="headingMd">Campaign</Text>
                  <Button variant="tertiary" onClick={() => setMarketing(null)}>Close</Button>
                </InlineStack>
                <MarketingCopy result={marketing} />
              </BlockStack>
            )}

            <Card>
              {editing ? (
                <FormLayout>
                  <TextField label="Title" value={form.title} onChange={(value) => setForm({ ...form, title: value })} autoComplete="off" />
                  <TextField
                    label="Description (HTML)"
                    value={form.descriptionHtml}
                    onChange={(value) => setForm({ ...form, descriptionHtml: value })}
                    multiline={8}
                    autoComplete="off"
                    monospaced
                  />
                </FormLayout>
              ) : (
                <BlockStack gap="300">
                  <Text as="h2" variant="headingMd">Description</Text>
                  {product.descriptionHtml ? (
                    <SafeHtml html={product.descriptionHtml} />
                  ) : (
                    <Banner tone="warning">
                      <p>This product has no description. Mia can write one for you.</p>
                    </Banner>
                  )}
                </BlockStack>
              )}
            </Card>

            {images.length > 0 && (
              <Card>
                <BlockStack gap="300">
                  <Text as="h2" variant="headingMd">Media</Text>
                  <InlineStack gap="300" wrap>
                    {images.map(({ node }) => (
                      <Thumbnail key={node.id || node.url} source={node.url || ''} alt={node.altText || product.title || ''} size="large" />
                    ))}
                  </InlineStack>
                </BlockStack>
              </Card>
            )}

            <Card padding="0">
              <Box padding="400">
                <Text as="h2" variant="headingMd">Variants</Text>
              </Box>
              {variants.length === 0 ? (
                <Box padding="400" paddingBlockStart="0">
                  <Text as="p" tone="subdued">This product has no variants.</Text>
                </Box>
              ) : (
                variants.map(({ node }, index) => {
                  const edit = node.id ? variantEdits[node.id] : undefined;
                  const options = (node.selectedOptions ?? []).map((o) => o.value).filter(Boolean).join(' / ');
                  return (
                    <Box key={node.id || index} padding="400" borderBlockStartWidth="025" borderColor="border">
                      <InlineGrid columns={editing ? { xs: 1, sm: 3 } : { xs: 1, sm: 3 }} gap="300" alignItems="center">
                        <BlockStack gap="050">
                          <Text as="span" fontWeight="semibold">{options || 'Default'}</Text>
                          <Text as="span" variant="bodySm" tone="subdued">{node.sku ? `SKU ${node.sku}` : 'No SKU'}</Text>
                        </BlockStack>
                        {editing && node.id && edit ? (
                          <>
                            <TextField
                              label="Price"
                              type="number"
                              value={edit.price}
                              onChange={(value) => setVariantEdits({ ...variantEdits, [node.id!]: { ...edit, price: value } })}
                              autoComplete="off"
                            />
                            <TextField
                              label="Available"
                              type="number"
                              value={edit.inventoryQuantity}
                              onChange={(value) => setVariantEdits({ ...variantEdits, [node.id!]: { ...edit, inventoryQuantity: value } })}
                              autoComplete="off"
                            />
                          </>
                        ) : (
                          <>
                            <Text as="span" numeric>{formatPrice(node.price)}</Text>
                            <Text as="span" tone={(node.inventoryQuantity ?? 0) <= 0 ? 'critical' : undefined}>
                              {`${formatNumber(node.inventoryQuantity ?? 0)} available`}
                            </Text>
                          </>
                        )}
                      </InlineGrid>
                    </Box>
                  );
                })
              )}
            </Card>
          </BlockStack>
        </Layout.Section>

        <Layout.Section variant="oneThird">
          <BlockStack gap="400">
            <Card>
              <BlockStack gap="300">
                <Text as="h2" variant="headingMd">Status</Text>
                {editing ? (
                  <Select label="Status" labelHidden options={STATUS_OPTIONS} value={form.status} onChange={(value) => setForm({ ...form, status: value })} />
                ) : (
                  <InlineStack>
                    <Badge tone={productTone(product.status)}>{humanize(product.status)}</Badge>
                  </InlineStack>
                )}
              </BlockStack>
            </Card>
            <Card>
              <BlockStack gap="300">
                <Text as="h2" variant="headingMd">Organization</Text>
                {editing ? (
                  <FormLayout>
                    <TextField label="Product type" value={form.productType} onChange={(value) => setForm({ ...form, productType: value })} autoComplete="off" />
                    <TextField label="Vendor" value={form.vendor} onChange={(value) => setForm({ ...form, vendor: value })} autoComplete="off" />
                    <TextField label="Tags" helpText="Separate tags with commas" value={form.tags} onChange={(value) => setForm({ ...form, tags: value })} autoComplete="off" />
                  </FormLayout>
                ) : (
                  <BlockStack gap="300">
                    <Field label="Product type" value={product.productType} />
                    <Field label="Vendor" value={product.vendor} />
                    <BlockStack gap="100">
                      <Text as="span" variant="bodySm" tone="subdued">Tags</Text>
                      {tags.length ? (
                        <InlineStack gap="100" wrap>{tags.map((tag) => <Tag key={tag}>{tag}</Tag>)}</InlineStack>
                      ) : (
                        <Text as="p" tone="subdued">No tags</Text>
                      )}
                    </BlockStack>
                  </BlockStack>
                )}
              </BlockStack>
            </Card>
          </BlockStack>
        </Layout.Section>
      </Layout>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Apply Mia’s changes to Shopify?"
        primaryAction={{ content: 'Apply changes', onAction: applyProposal, loading: action === 'apply' }}
        secondaryActions={[{ content: 'Cancel', onAction: () => setConfirmOpen(false) }]}
      >
        <Modal.Section>
          <BlockStack gap="200">
            <Text as="p">This updates the product title, description, SEO title, meta description, tags and image alt text in your store.</Text>
            <Text as="p" tone="subdued">The product URL (handle) stays the same.</Text>
          </BlockStack>
        </Modal.Section>
      </Modal>
    </Page>
  );
}
