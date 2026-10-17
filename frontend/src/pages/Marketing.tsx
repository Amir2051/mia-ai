import { useEffect, useMemo, useState } from 'react';
import {
  Badge,
  BlockStack,
  Box,
  Button,
  Card,
  Checkbox,
  Divider,
  InlineGrid,
  InlineStack,
  Layout,
  List,
  Modal,
  Page,
  ProgressBar,
  Select,
  Tabs,
  Tag,
  Text,
} from '@shopify/polaris';
import { ImageIcon, MagicIcon, MegaphoneIcon, SearchIcon } from '@shopify/polaris-icons';
import api from '../services/api';
import SafeHtml from '../components/SafeHtml';
import { Field, MarketingCopy, scoreTone, type MarketingResult, type SeoResult } from '../components/MiaResults';
import { ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage } from '../lib/format';
import { showToast } from '../lib/toast';
import { useSession } from '../lib/session';

type Product = { id: string; title: string; status?: string };
type Current = { title?: string; seo_title?: string; seo_description?: string; handle?: string; tags?: string[] };
type Analysis = { seo_score?: number; issues?: string[]; recommendations?: string[] };
type Busy = null | 'analyze' | 'seo' | 'marketing' | 'image' | 'campaign' | 'apply';

export default function Marketing() {
  const session = useSession();
  const [products, setProducts] = useState<Product[]>([]);
  const [productId, setProductId] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);

  const [current, setCurrent] = useState<Current | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [seo, setSeo] = useState<SeoResult | null>(null);
  const [marketing, setMarketing] = useState<MarketingResult | null>(null);
  const [image, setImage] = useState<string | null>(null);
  const [tab, setTab] = useState(0);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [includeHandle, setIncludeHandle] = useState(false);

  useEffect(() => {
    if (!session.connected) return;
    api
      .get('/products/?query=')
      .then((res) => {
        const rows = (res.data?.data?.products?.edges ?? []).map((edge: { node: Product }) => edge.node).filter((p: Product) => p?.id);
        setProducts(rows);
        if (rows[0]) setProductId(rows[0].id);
      })
      .catch((err) => setError(errorMessage(err, 'Could not load your products')))
      .finally(() => setLoading(false));
  }, [session.connected]);

  const selected = useMemo(() => products.find((p) => p.id === productId), [products, productId]);

  if (session.loading || (session.connected && loading)) return <PageSkeleton title="SEO & marketing" />;
  if (!session.connected) return <NotConnected title="SEO & marketing" />;

  const clearResults = () => {
    setAnalysis(null);
    setSeo(null);
    setMarketing(null);
    setImage(null);
    setCurrent(null);
  };

  async function call(kind: Exclude<Busy, null | 'apply'>) {
    if (!productId) return;
    setBusy(kind);
    setError(null);
    try {
      if (kind === 'analyze') {
        const res = await api.post('/seo/analyze', { product_id: productId });
        setAnalysis(res.data);
        setCurrent(res.data.product);
      } else if (kind === 'seo') {
        const res = await api.post('/seo/generate', { product_id: productId });
        setSeo(res.data.result);
        setCurrent(res.data.product);
        setTab(0);
      } else if (kind === 'marketing') {
        const res = await api.post('/seo/marketing', { product_id: productId });
        setMarketing(res.data.result);
        setTab(1);
      } else if (kind === 'image') {
        const res = await api.post('/seo/creative', { product_id: productId });
        setImage(res.data.image || null);
        setTab(2);
      } else {
        const res = await api.post('/seo/full-campaign', { product_id: productId });
        setCurrent(res.data.product);
        setSeo(res.data.seo);
        setMarketing(res.data.marketing);
        setImage(res.data.creative?.image || null);
        setTab(0);
      }
    } catch (err) {
      const labels = { analyze: 'SEO check', seo: 'SEO generation', marketing: 'Campaign generation', image: 'Image enhancement', campaign: 'Full campaign' };
      setError(errorMessage(err, `${labels[kind]} failed. Please try again.`));
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    if (!seo) return;
    setBusy('apply');
    setError(null);
    try {
      await api.post('/seo/apply', {
        product_id: productId,
        changes: {
          title: seo.optimized_title,
          descriptionHtml: seo.optimized_description_html,
          seo: { title: seo.seo_title, description: seo.meta_description },
          tags: seo.tags,
          image_alt_text: seo.image_alt_text,
          ...(includeHandle && seo.handle_suggestion ? { handle: seo.handle_suggestion } : {}),
        },
        confirmed: true,
        apply_handle: includeHandle,
      });
      setConfirmOpen(false);
      setSeo(null);
      showToast('SEO changes are live in Shopify');
      const verify = await api.post('/seo/analyze', { product_id: productId });
      setAnalysis(verify.data);
      setCurrent(verify.data.product);
    } catch (err) {
      setConfirmOpen(false);
      setError(errorMessage(err, 'Shopify did not accept the SEO changes'));
    } finally {
      setBusy(null);
    }
  }

  const tabs = [
    { id: 'seo', content: 'SEO' },
    { id: 'campaign', content: 'Campaign' },
    { id: 'image', content: 'Product image' },
  ];
  const hasResults = !!(seo || marketing || image);

  return (
    <Page
      title="SEO & marketing"
      subtitle="Mia writes from your live product data. Nothing changes in Shopify until you approve it."
      titleMetadata={<Badge tone="magic">AI</Badge>}
    >
      <Layout>
        <Layout.Section>
          <BlockStack gap="400">
            {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

            <Card>
              <BlockStack gap="400">
                <Select
                  label="Product"
                  options={products.length ? products.map((p) => ({ label: p.title, value: p.id })) : [{ label: 'No products in your store', value: '' }]}
                  value={productId}
                  onChange={(value) => {
                    setProductId(value);
                    clearResults();
                  }}
                  disabled={!products.length || busy !== null}
                />
                <InlineStack gap="200" wrap>
                  <Button variant="primary" icon={MagicIcon} onClick={() => call('campaign')} loading={busy === 'campaign'} disabled={!productId || busy !== null}>
                    Generate everything
                  </Button>
                  <Button icon={SearchIcon} onClick={() => call('seo')} loading={busy === 'seo'} disabled={!productId || busy !== null}>
                    SEO package
                  </Button>
                  <Button icon={MegaphoneIcon} onClick={() => call('marketing')} loading={busy === 'marketing'} disabled={!productId || busy !== null}>
                    Campaign copy
                  </Button>
                  <Button icon={ImageIcon} onClick={() => call('image')} loading={busy === 'image'} disabled={!productId || busy !== null}>
                    Enhance image
                  </Button>
                </InlineStack>
                {busy === 'campaign' && (
                  <Text as="p" tone="subdued">Writing SEO, campaign copy and an enhanced image — this can take up to a minute.</Text>
                )}
              </BlockStack>
            </Card>

            {hasResults && (
              <Card padding="0">
                <Tabs tabs={tabs} selected={tab} onSelect={setTab} />
                <Box padding="400">
                  {tab === 0 &&
                    (seo ? (
                      <BlockStack gap="400">
                        <InlineStack align="space-between" blockAlign="center">
                          <Text as="h2" variant="headingMd">SEO package</Text>
                          {typeof seo.seo_score === 'number' && <Badge tone={scoreTone(seo.seo_score)}>{`Projected score ${seo.seo_score}/100`}</Badge>}
                        </InlineStack>
                        <InlineGrid columns={{ xs: 1, md: 2 }} gap="400">
                          <BlockStack gap="300">
                            <Text as="h3" variant="headingSm" tone="subdued">Now in Shopify</Text>
                            <Field label="Product title" value={current?.title} />
                            <Field label="SEO title" value={current?.seo_title} />
                            <Field label="Meta description" value={current?.seo_description} />
                            <Field label="URL handle" value={current?.handle} />
                          </BlockStack>
                          <BlockStack gap="300">
                            <Text as="h3" variant="headingSm">Mia suggests</Text>
                            <Field label="Product title" value={seo.optimized_title} />
                            <Field label="SEO title" value={seo.seo_title} />
                            <Field label="Meta description" value={seo.meta_description} />
                            <Field label="URL handle" value={seo.handle_suggestion} />
                          </BlockStack>
                        </InlineGrid>
                        <Divider />
                        <InlineGrid columns={{ xs: 1, md: 2 }} gap="400">
                          <BlockStack gap="200">
                            <Text as="h3" variant="headingSm">Keywords</Text>
                            <InlineStack gap="100" wrap>{(seo.keywords ?? []).map((k) => <Tag key={k}>{k}</Tag>)}</InlineStack>
                          </BlockStack>
                          <BlockStack gap="200">
                            <Text as="h3" variant="headingSm">Tags</Text>
                            <InlineStack gap="100" wrap>{(seo.tags ?? []).map((k) => <Tag key={k}>{k}</Tag>)}</InlineStack>
                          </BlockStack>
                        </InlineGrid>
                        {!!seo.image_alt_text?.length && (
                          <BlockStack gap="100">
                            <Text as="h3" variant="headingSm">Image alt text</Text>
                            <List>{seo.image_alt_text.map((alt, i) => <List.Item key={i}>{alt}</List.Item>)}</List>
                          </BlockStack>
                        )}
                        <Divider />
                        <BlockStack gap="200">
                          <Text as="h3" variant="headingSm">Product description</Text>
                          <SafeHtml html={seo.optimized_description_html} />
                        </BlockStack>
                        <InlineStack gap="200">
                          <Button variant="primary" onClick={() => setConfirmOpen(true)} disabled={busy !== null}>Review and apply</Button>
                          <Button onClick={() => call('seo')} loading={busy === 'seo'} disabled={busy !== null}>Regenerate</Button>
                        </InlineStack>
                      </BlockStack>
                    ) : (
                      <Text as="p" tone="subdued">Generate an SEO package to see suggestions here.</Text>
                    ))}
                  {tab === 1 &&
                    (marketing ? <MarketingCopy result={marketing} /> : <Text as="p" tone="subdued">Generate campaign copy to see it here.</Text>)}
                  {tab === 2 &&
                    (image ? (
                      <BlockStack gap="300">
                        <img className="mia-thumb" src={image} alt={`Enhanced image of ${selected?.title ?? 'product'}`} />
                        <InlineStack>
                          <Button url={image} external>Open full size</Button>
                        </InlineStack>
                      </BlockStack>
                    ) : (
                      <Text as="p" tone="subdued">Enhance the product image to see it here.</Text>
                    ))}
                </Box>
              </Card>
            )}
          </BlockStack>
        </Layout.Section>

        <Layout.Section variant="oneThird">
          <Card>
            <BlockStack gap="300">
              <InlineStack align="space-between" blockAlign="center">
                <Text as="h2" variant="headingMd">SEO health</Text>
                {analysis && <Badge tone={scoreTone(analysis.seo_score)}>{`${analysis.seo_score ?? 0}/100`}</Badge>}
              </InlineStack>
              {analysis ? (
                <>
                  <ProgressBar progress={analysis.seo_score ?? 0} size="small" tone={(analysis.seo_score ?? 0) >= 80 ? 'success' : (analysis.seo_score ?? 0) >= 60 ? 'highlight' : 'critical'} />
                  {analysis.issues?.length ? (
                    <List>{analysis.issues.map((issue) => <List.Item key={issue}>{issue}</List.Item>)}</List>
                  ) : (
                    <Text as="p" tone="success">No major issues found.</Text>
                  )}
                  {!!analysis.recommendations?.length && (
                    <BlockStack gap="100">
                      <Text as="h3" variant="headingSm">Recommendations</Text>
                      <List>{analysis.recommendations.map((item) => <List.Item key={item}>{item}</List.Item>)}</List>
                    </BlockStack>
                  )}
                </>
              ) : (
                <Text as="p" tone="subdued">Check how this product scores before generating changes.</Text>
              )}
              <Button onClick={() => call('analyze')} loading={busy === 'analyze'} disabled={!productId || busy !== null}>
                {analysis ? 'Check again' : 'Check SEO'}
              </Button>
            </BlockStack>
          </Card>
        </Layout.Section>
      </Layout>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Apply SEO changes to Shopify?"
        primaryAction={{ content: 'Apply changes', onAction: apply, loading: busy === 'apply' }}
        secondaryActions={[{ content: 'Cancel', onAction: () => setConfirmOpen(false) }]}
      >
        <Modal.Section>
          <BlockStack gap="300">
            <Text as="p">{`Mia will update the title, description, SEO title, meta description, tags and image alt text for ${selected?.title ?? 'this product'}.`}</Text>
            {seo?.handle_suggestion && (
              <Checkbox
                label={`Also change the URL handle to "${seo.handle_suggestion}"`}
                helpText="Changing a handle changes the product URL. Shopify can redirect the old URL, but existing links and rankings may be affected."
                checked={includeHandle}
                onChange={setIncludeHandle}
              />
            )}
          </BlockStack>
        </Modal.Section>
      </Modal>
    </Page>
  );
}
