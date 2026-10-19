import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  BlockStack,
  Box,
  Button,
  Card,
  Icon,
  IndexTable,
  InlineGrid,
  InlineStack,
  Layout,
  Page,
  Text,
} from '@shopify/polaris';
import { ImportIcon, MagicIcon, MegaphoneIcon, ProductIcon } from '@shopify/polaris-icons';
import { useNavigate } from 'react-router-dom';
import api from '../services/api';
import StatCard from '../components/StatCard';
import { CardSkeleton, ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { formatDateTime, formatMoney, formatNumber, idPath } from '../lib/format';
import { useSession } from '../lib/session';

type Sale = { id?: string | null; name?: string | null; created_at?: string | null; amount?: number | null; currency?: string | null };
type TopProduct = { title?: string | null; sku?: string | null; units_sold?: number | null; revenue?: number | null };
type Summary = {
  revenue?: number | null;
  orders?: number | null;
  customers?: number | null;
  average_order_value?: number | null;
  recent_sales?: Sale[] | null;
  top_products?: TopProduct[] | null;
};
type MiaContext = { low_stock?: Array<{ title: string; inventory: number }>; counts?: { products?: number } };

export default function Dashboard() {
  const session = useSession();
  const navigate = useNavigate();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [context, setContext] = useState<MiaContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.allSettled([api.get('/analytics/'), api.get('/mia/context')])
      .then(([analytics, mia]) => {
        if (analytics.status === 'fulfilled') setSummary(analytics.value.data as Summary);
        else setError('We could not load your Shopify sales right now.');
        if (mia.status === 'fulfilled') setContext(mia.value.data as MiaContext);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (session.connected) load();
  }, [session.connected, load]);

  if (session.loading) return <PageSkeleton title="Dashboard" withAside />;
  if (!session.connected) return <NotConnected title="Dashboard" error={session.error} />;

  const currency = summary?.recent_sales?.find((sale) => sale.currency)?.currency || 'USD';
  const sales = summary?.recent_sales ?? [];
  const topProducts = summary?.top_products ?? [];
  const lowStock = context?.low_stock ?? [];

  return (
    <Page
      title="Dashboard"
      subtitle={session.shopDomain ?? undefined}
      primaryAction={{ content: 'Ask Mia', icon: MagicIcon, url: '/mia' }}
    >
      <BlockStack gap="500">
        {error && <ErrorBanner message={error} onRetry={load} />}

        <InlineGrid columns={{ xs: 2, md: 4 }} gap="400">
          <StatCard label="Revenue" value={formatMoney(summary?.revenue ?? 0, currency)} loading={loading} />
          <StatCard label="Orders" value={formatNumber(summary?.orders ?? 0)} loading={loading} />
          <StatCard label="Average order value" value={formatMoney(summary?.average_order_value ?? 0, currency)} loading={loading} />
          <StatCard label="Customers" value={formatNumber(summary?.customers ?? 0)} loading={loading} />
        </InlineGrid>

        <Card>
          <InlineStack align="space-between" blockAlign="center" gap="400" wrap>
            <InlineStack gap="300" blockAlign="center" wrap={false}>
              <Box background="bg-surface-magic" borderRadius="200" padding="200">
                <Icon source={MagicIcon} tone="magic" />
              </Box>
              <BlockStack gap="100">
                <Text as="h2" variant="headingMd">
                  {lowStock.length > 0
                    ? `${lowStock.length} product${lowStock.length === 1 ? ' is' : 's are'} running low`
                    : 'Mia is ready to grow your catalog'}
                </Text>
                <Text as="p" tone="subdued">
                  {lowStock.length > 0
                    ? `${lowStock.slice(0, 2).map((p) => p.title).join(', ')}${lowStock.length > 2 ? ' and more' : ''} — restock or promote them before they sell out.`
                    : 'Generate SEO packages, product descriptions and ready-to-post campaigns from your live products.'}
                </Text>
              </BlockStack>
            </InlineStack>
            <InlineStack gap="200">
              {lowStock.length > 0 && <Button url="/products">Review products</Button>}
              <Button variant="primary" url="/marketing">
                Open SEO &amp; marketing
              </Button>
            </InlineStack>
          </InlineStack>
        </Card>

        <Layout>
          <Layout.Section>
            {loading ? (
              <CardSkeleton lines={8} />
            ) : (
              <Card padding="0">
                <Box padding="400">
                  <InlineStack align="space-between" blockAlign="center">
                    <Text as="h2" variant="headingMd">
                      Recent orders
                    </Text>
                    <Button variant="plain" url="/orders">
                      View all
                    </Button>
                  </InlineStack>
                </Box>
                <IndexTable
                  resourceName={{ singular: 'order', plural: 'orders' }}
                  itemCount={sales.length}
                  selectable={false}
                  headings={[{ title: 'Order' }, { title: 'Date' }, { title: 'Total', alignment: 'end' }]}
                  emptyState={
                    <Box padding="600">
                      <Text as="p" alignment="center" tone="subdued">
                        No orders yet. New orders will show up here.
                      </Text>
                    </Box>
                  }
                >
                  {sales.map((sale, index) => (
                    <IndexTable.Row
                      id={sale.id || String(index)}
                      key={sale.id || index}
                      position={index}
                      onClick={() => sale.id && navigate(idPath('/orders', sale.id))}
                    >
                      <IndexTable.Cell>
                        <Text as="span" fontWeight="semibold">
                          {sale.name || 'Order'}
                        </Text>
                      </IndexTable.Cell>
                      <IndexTable.Cell>{formatDateTime(sale.created_at)}</IndexTable.Cell>
                      <IndexTable.Cell>
                        <Text as="span" alignment="end" numeric>
                          {formatMoney(sale.amount, sale.currency || currency)}
                        </Text>
                      </IndexTable.Cell>
                    </IndexTable.Row>
                  ))}
                </IndexTable>
              </Card>
            )}
          </Layout.Section>

          <Layout.Section variant="oneThird">
            <BlockStack gap="400">
              <Card>
                <BlockStack gap="300">
                  <Text as="h2" variant="headingMd">
                    Top products
                  </Text>
                  {loading ? (
                    <Text as="p" tone="subdued">
                      Loading…
                    </Text>
                  ) : topProducts.length === 0 ? (
                    <Text as="p" tone="subdued">
                      Sales by product appear after your first orders.
                    </Text>
                  ) : (
                    topProducts.map((product, index) => (
                      <InlineStack key={`${product.title}-${product.sku}-${index}`} align="space-between" blockAlign="center" wrap={false} gap="200">
                        <Box minWidth="0" width="100%">
                          <BlockStack gap="050">
                            <Text as="span" fontWeight="medium" truncate>
                              {product.title || 'Untitled product'}
                            </Text>
                            <Text as="span" variant="bodySm" tone="subdued">
                              {formatNumber(product.units_sold ?? 0)} sold
                            </Text>
                          </BlockStack>
                        </Box>
                        <Text as="span" numeric>
                          {formatMoney(product.revenue ?? 0, currency)}
                        </Text>
                      </InlineStack>
                    ))
                  )}
                </BlockStack>
              </Card>

              <Card>
                <BlockStack gap="300">
                  <Text as="h2" variant="headingMd">
                    Shortcuts
                  </Text>
                  <Shortcut icon={ProductIcon} label="Manage products" url="/products" />
                  <Shortcut icon={ImportIcon} label="Import products from CSV" url="/imports" />
                  <Shortcut icon={MegaphoneIcon} label="Create a campaign" url="/marketing" badge="AI" />
                </BlockStack>
              </Card>
            </BlockStack>
          </Layout.Section>
        </Layout>
      </BlockStack>
    </Page>
  );
}

function Shortcut({ icon, label, url, badge }: { icon: typeof ProductIcon; label: string; url: string; badge?: string }) {
  return (
    <InlineStack gap="200" blockAlign="center" wrap={false}>
      <Box>
        <Icon source={icon} tone="subdued" />
      </Box>
      <Button variant="plain" url={url}>
        {label}
      </Button>
      {badge && <Badge tone="magic">{badge}</Badge>}
    </InlineStack>
  );
}
