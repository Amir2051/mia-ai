import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  BlockStack,
  Box,
  Card,
  InlineGrid,
  InlineStack,
  Layout,
  Page,
  ProgressBar,
  Select,
  Text,
  TextField,
} from '@shopify/polaris';
import api from '../services/api';
import StatCard from '../components/StatCard';
import { CardSkeleton, ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, formatDateTime, formatMoney, formatNumber } from '../lib/format';
import { useSession } from '../lib/session';

type Summary = {
  revenue?: number | null;
  orders?: number | null;
  customers?: number | null;
  average_order_value?: number | null;
  top_products?: Array<{ title?: string | null; sku?: string | null; units_sold?: number | null; revenue?: number | null }> | null;
  recent_sales?: Array<{ id?: string | null; name?: string | null; created_at?: string | null; amount?: number | null; currency?: string | null }> | null;
};

const RANGES = [
  { label: 'Last 7 days', value: '7' },
  { label: 'Last 30 days', value: '30' },
  { label: 'Last 90 days', value: '90' },
  { label: 'Last 365 days', value: '365' },
  { label: 'All time', value: 'all' },
  { label: 'Custom range', value: 'custom' },
];

const isoDate = (date: Date) => date.toISOString().slice(0, 10);

export default function Analytics() {
  const session = useSession();
  const [range, setRange] = useState('30');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [data, setData] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const params = useMemo(() => {
    if (range === 'all') return {};
    if (range === 'custom') {
      const value: Record<string, string> = {};
      if (customStart) value.start = customStart;
      if (customEnd) value.end = customEnd;
      return value;
    }
    const start = new Date();
    start.setDate(start.getDate() - Number(range) + 1);
    return { start: isoDate(start), end: isoDate(new Date()) };
  }, [range, customStart, customEnd]);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .get('/analytics/', { params })
      .then((res) => setData(res.data as Summary))
      .catch((err) => setError(errorMessage(err, 'Could not load analytics')))
      .finally(() => setLoading(false));
  }, [params]);

  useEffect(() => {
    if (session.connected) load();
  }, [session.connected, load]);

  if (session.loading) return <PageSkeleton title="Analytics" withAside />;
  if (!session.connected) return <NotConnected title="Analytics" />;

  const currency = data?.recent_sales?.find((sale) => sale.currency)?.currency || 'USD';
  const topProducts = data?.top_products ?? [];
  const topRevenue = Math.max(1, ...topProducts.map((product) => product.revenue ?? 0));
  const sales = data?.recent_sales ?? [];

  return (
    <Page title="Analytics" subtitle="Sales from your Shopify orders">
      <BlockStack gap="400">
        <Card>
          <InlineStack gap="300" blockAlign="end" wrap>
            <Box minWidth="200px">
              <Select label="Date range" options={RANGES} value={range} onChange={setRange} />
            </Box>
            {range === 'custom' && (
              <>
                <TextField label="From" type="date" value={customStart} onChange={setCustomStart} autoComplete="off" />
                <TextField label="To" type="date" value={customEnd} onChange={setCustomEnd} autoComplete="off" />
              </>
            )}
          </InlineStack>
        </Card>

        {error && <ErrorBanner message={error} onRetry={load} />}

        <InlineGrid columns={{ xs: 2, md: 4 }} gap="400">
          <StatCard label="Revenue" value={formatMoney(data?.revenue ?? 0, currency)} loading={loading} />
          <StatCard label="Orders" value={formatNumber(data?.orders ?? 0)} loading={loading} />
          <StatCard label="Average order value" value={formatMoney(data?.average_order_value ?? 0, currency)} loading={loading} />
          <StatCard label="Customers" value={formatNumber(data?.customers ?? 0)} helpText="All time" loading={loading} />
        </InlineGrid>

        <Layout>
          <Layout.Section>
            {loading ? (
              <CardSkeleton lines={8} />
            ) : (
              <Card>
                <BlockStack gap="400">
                  <Text as="h2" variant="headingMd">Top products by sales</Text>
                  {topProducts.length === 0 ? (
                    <Text as="p" tone="subdued">No sales in this period.</Text>
                  ) : (
                    topProducts.map((product, index) => (
                      <BlockStack key={`${product.title}-${product.sku}-${index}`} gap="150">
                        <InlineStack align="space-between" gap="300" wrap={false}>
                          <Box minWidth="0" width="100%">
                            <Text as="span" fontWeight="medium" truncate>{product.title || 'Untitled product'}</Text>
                          </Box>
                          <Text as="span" numeric>{formatMoney(product.revenue ?? 0, currency)}</Text>
                        </InlineStack>
                        <ProgressBar progress={((product.revenue ?? 0) / topRevenue) * 100} size="small" tone="primary" />
                        <Text as="span" variant="bodySm" tone="subdued">
                          {`${formatNumber(product.units_sold ?? 0)} units${product.sku && product.sku !== 'N/A' ? ` · ${product.sku}` : ''}`}
                        </Text>
                      </BlockStack>
                    ))
                  )}
                </BlockStack>
              </Card>
            )}
          </Layout.Section>
          <Layout.Section variant="oneThird">
            {loading ? (
              <CardSkeleton lines={6} />
            ) : (
              <Card>
                <BlockStack gap="300">
                  <Text as="h2" variant="headingMd">Latest sales</Text>
                  {sales.length === 0 ? (
                    <Text as="p" tone="subdued">No sales in this period.</Text>
                  ) : (
                    sales.slice(0, 8).map((sale, index) => (
                      <InlineStack key={sale.id || index} align="space-between" blockAlign="center">
                        <BlockStack gap="050">
                          <Text as="span" fontWeight="medium">{sale.name || 'Order'}</Text>
                          <Text as="span" variant="bodySm" tone="subdued">{formatDateTime(sale.created_at)}</Text>
                        </BlockStack>
                        <Text as="span" numeric>{formatMoney(sale.amount, sale.currency || currency)}</Text>
                      </InlineStack>
                    ))
                  )}
                </BlockStack>
              </Card>
            )}
          </Layout.Section>
        </Layout>
      </BlockStack>
    </Page>
  );
}
