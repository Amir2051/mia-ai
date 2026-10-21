import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  Banner,
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

type Sale = {
  id?: string | null;
  name?: string | null;
  created_at?: string | null;
  amount?: number | null;
  currency?: string | null;
  financial_status?: string | null;
  is_cancelled?: boolean;
};
type TopProduct = { title?: string | null; sku?: string | null; units_sold?: number | null; revenue?: number | null };
type PeriodStats = {
  orders: number;
  paid_orders: number;
  net_sales: number;
  gross_order_value: number;
  average_order_value: number;
};
type PeriodComparison = {
  period_days: number;
  timezone: string;
  current_period: { start: string; end: string };
  previous_period: { start: string; end: string };
  current: PeriodStats;
  previous: PeriodStats;
  change_percent: { net_sales: number | null; orders: number | null; average_order_value: number | null };
};
type Summary = {
  revenue?: number | null;
  net_sales?: number | null;
  gross_order_value?: number | null;
  orders?: number | null;
  paid_orders?: number | null;
  unpaid_orders?: number | null;
  refunded_orders?: number | null;
  cancelled_orders?: number | null;
  customers?: number | null;
  customer_count_precision?: string | null;
  customer_count_warning?: string | null;
  coverage_warning?: string | null;
  product_revenue_note?: string | null;
  period_comparison?: PeriodComparison | null;
  average_order_value?: number | null;
  recent_sales?: Sale[] | null;
  top_products?: TopProduct[] | null;
};
type MiaContext = {
  low_stock?: Array<{ title: string; inventory: number }>;
  counts?: { products?: number | null };
  available?: { products?: boolean };
};
type Insight = { title: string; description: string; tone: 'critical' | 'warning' | 'success' | 'info' };

function changeLabel(value: number | null | undefined): string {
  if (value == null) return 'No baseline';
  return (value > 0 ? '+' : '') + value.toFixed(1) + '%';
}

function changeTone(value: number | null | undefined): 'critical' | 'warning' | 'success' | 'info' {
  if (value == null) return 'warning';
  if (value > 0) return 'success';
  if (value < 0) return 'critical';
  return 'info';
}

export default function Dashboard() {
  const session = useSession();
  const navigate = useNavigate();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [context, setContext] = useState<MiaContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.allSettled([api.get('/analytics/'), api.get('/mia/context')])
      .then(([analytics, mia]) => {
        if (analytics.status === 'fulfilled') setSummary(analytics.value.data as Summary);
        else setError('We could not load your Shopify sales right now.');
        if (mia.status === 'fulfilled') {
          const miaContext = mia.value.data as MiaContext;
          setContext(miaContext);
          setContextError(miaContext?.available?.products === false
            ? 'Product inventory could not be read from Shopify; the low-stock list is unavailable, not empty.'
            : null);
        } else {
          setContextError('Product inventory could not be loaded from Shopify.');
        }
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
  const comparison = summary?.period_comparison;
  const insights: Insight[] = [];
  const netSalesChange = comparison?.change_percent.net_sales;
  const orderChange = comparison?.change_percent.orders;
  const aovChange = comparison?.change_percent.average_order_value;

  if (netSalesChange != null && netSalesChange <= -10) {
    insights.push({
      title: 'Net sales are down ' + Math.abs(netSalesChange).toFixed(1) + '%',
      description: 'Compare campaign traffic, conversion rates, and the top products from the current period before changing prices or ad spend.',
      tone: 'critical',
    });
  } else if (netSalesChange != null && netSalesChange >= 10) {
    insights.push({
      title: 'Net sales are up ' + netSalesChange.toFixed(1) + '%',
      description: 'Review which products and campaigns contributed most so you can prioritize the strongest performers.',
      tone: 'success',
    });
  }
  if (aovChange != null && aovChange <= -10) {
    insights.push({
      title: 'Average order value is down ' + Math.abs(aovChange).toFixed(1) + '%',
      description: 'Consider testing bundles or relevant cross-sells on proven products; compare the results before making a permanent change.',
      tone: 'warning',
    });
  }
  if ((summary?.unpaid_orders ?? 0) > 0) {
    insights.push({
      title: String(summary?.unpaid_orders) + ' unpaid order' + (summary?.unpaid_orders === 1 ? '' : 's') + ' in the returned history',
      description: 'Review their payment status before treating those orders as collected sales.',
      tone: 'warning',
    });
  }
  if ((summary?.cancelled_orders ?? 0) > 0) {
    insights.push({
      title: String(summary?.cancelled_orders) + ' cancelled order' + (summary?.cancelled_orders === 1 ? '' : 's') + ' in the returned history',
      description: 'Review cancellation reasons to see whether product availability, shipping, or checkout issues are recurring.',
      tone: 'warning',
    });
  }
  if (lowStock.length > 0) {
    insights.push({
      title: String(lowStock.length) + ' product' + (lowStock.length === 1 ? ' is' : 's are') + ' low on stock',
      description: lowStock.slice(0, 3).map((item) => item.title).join(', ') + (lowStock.length > 3 ? ' and more' : '') + '. Review inventory before promoting these products.',
      tone: 'critical',
    });
  }

  return (
    <Page
      title="Dashboard"
      subtitle={session.shopDomain ?? undefined}
      primaryAction={{ content: 'Ask Mia', icon: MagicIcon, url: '/mia' }}
    >
      <BlockStack gap="500">
        {error && <ErrorBanner message={error} onRetry={load} />}
        {summary?.coverage_warning && (
          <Banner tone="warning" title="Order history may be incomplete">
            <Text as="p">{summary.coverage_warning}</Text>
          </Banner>
        )}
        {summary?.customer_count_warning && (
          <Banner tone="info" title="Customer count unavailable">
            <Text as="p">{summary.customer_count_warning}</Text>
          </Banner>
        )}
        {contextError && (
          <Banner tone="warning" title="Inventory insights unavailable">
            <Text as="p">{contextError}</Text>
          </Banner>
        )}

        <InlineGrid columns={{ xs: 2, md: 4 }} gap="400">
          <StatCard
            label="Net sales"
            value={formatMoney(summary?.net_sales ?? summary?.revenue ?? 0, currency)}
            helpText="Shopify current subtotal after returns/refunds; tax treatment follows store settings."
            loading={loading}
          />
          <StatCard
            label="Gross order value"
            value={formatMoney(summary?.gross_order_value ?? 0, currency)}
            helpText="Non-cancelled orders before returns/refunds; includes unpaid orders."
            loading={loading}
          />
          <StatCard
            label="Orders"
            value={formatNumber(summary?.orders ?? 0)}
            helpText={summary ? `Paid ${summary.paid_orders ?? 0} · unpaid ${summary.unpaid_orders ?? 0} · cancelled ${summary.cancelled_orders ?? 0}` : undefined}
            loading={loading}
          />
          <StatCard label="Average net paid order value" value={formatMoney(summary?.average_order_value ?? 0, currency)} loading={loading} />
          <StatCard
            label="Customers"
            value={summary?.customers == null ? '—' : `${formatNumber(summary.customers)}${summary.customer_count_precision?.toUpperCase() === 'AT_LEAST' ? '+' : ''}`}
            helpText={summary?.customer_count_precision?.toUpperCase() === 'AT_LEAST' ? 'Shopify reports this as a minimum count.' : 'Shopify customer count.'}
            loading={loading}
          />
        </InlineGrid>

        {comparison && (
          <Card>
            <BlockStack gap="300">
              <InlineStack align="space-between" blockAlign="center" gap="300" wrap>
                <BlockStack gap="050">
                  <Text as="h2" variant="headingMd">Sales trend</Text>
                  <Text as="p" variant="bodySm" tone="subdued">
                    Last {comparison.period_days} rolling days vs the previous {comparison.period_days} days · {comparison.timezone}
                  </Text>
                </BlockStack>
                <Badge tone={changeTone(netSalesChange)}>{changeLabel(netSalesChange) + ' net sales'}</Badge>
              </InlineStack>
              <InlineGrid columns={{ xs: 1, md: 3 }} gap="400">
                {([
                  { label: 'Net sales', current: comparison.current.net_sales, previous: comparison.previous.net_sales, change: netSalesChange, format: (value: number) => formatMoney(value, currency) },
                  { label: 'Orders', current: comparison.current.orders, previous: comparison.previous.orders, change: orderChange, format: (value: number) => formatNumber(value) },
                  { label: 'Average order value', current: comparison.current.average_order_value, previous: comparison.previous.average_order_value, change: aovChange, format: (value: number) => formatMoney(value, currency) },
                ]).map((metric) => (
                  <Box key={metric.label} borderColor="border" borderWidth="025" borderRadius="200" padding="300">
                    <BlockStack gap="200">
                      <Text as="span" tone="subdued" variant="bodySm">{metric.label}</Text>
                      <Text as="p" variant="headingLg">{metric.format(metric.current)}</Text>
                      <InlineStack align="space-between" blockAlign="center" gap="200">
                        <Text as="span" tone="subdued" variant="bodySm">Previous: {metric.format(metric.previous)}</Text>
                        <Badge tone={changeTone(metric.change)}>{changeLabel(metric.change)}</Badge>
                      </InlineStack>
                    </BlockStack>
                  </Box>
                ))}
              </InlineGrid>
              <Text as="p" variant="bodySm" tone="subdued">
                Comparison uses the order history returned by Shopify. Net sales exclude unpaid and cancelled orders and use Shopify's current subtotal after refunds/returns; percentage change is unavailable when the prior period has no baseline. Any order-history warning above also applies here.
              </Text>
            </BlockStack>
          </Card>
        )}

        <Card>
          <BlockStack gap="300">
            <Text as="h2" variant="headingMd">Actionable insights</Text>
            {insights.length ? insights.map((insight) => (
              <Banner key={insight.title} tone={insight.tone} title={insight.title}>
                <Text as="p">{insight.description}</Text>
              </Banner>
            )) : (
              <Text as="p" tone="subdued">
                No major trend or inventory flags were identified from the data returned by Shopify. Check the comparison baseline and order-history coverage before drawing conclusions.
              </Text>
            )}
          </BlockStack>
        </Card>

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
                  headings={[{ title: 'Order' }, { title: 'Date' }, { title: 'Status' }, { title: 'Total', alignment: 'end' }]}
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
                        {sale.is_cancelled ? (
                          <Badge tone="critical">Cancelled</Badge>
                        ) : sale.financial_status ? (
                          <Badge tone={['PAID', 'PARTIALLY_REFUNDED'].includes(sale.financial_status) ? 'success' : 'warning'}>
                            {sale.financial_status.replace(/_/g, ' ')}
                          </Badge>
                        ) : (
                          <Text as="span" tone="subdued">Unknown</Text>
                        )}
                      </IndexTable.Cell>
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
