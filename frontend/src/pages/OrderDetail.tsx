import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Badge, BlockStack, Box, Card, Divider, InlineStack, Layout, Page, Text } from '@shopify/polaris';
import api from '../services/api';
import { ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, financialTone, formatDateTime, formatShopMoney, fulfillmentTone, humanize } from '../lib/format';
import { useSession } from '../lib/session';

type MoneySet = { shopMoney?: { amount?: string | null; currencyCode?: string | null } | null } | null;
type Order = {
  id?: string | null;
  name?: string | null;
  createdAt?: string | null;
  displayFinancialStatus?: string | null;
  displayFulfillmentStatus?: string | null;
  subtotalPriceSet?: MoneySet;
  totalShippingPriceSet?: MoneySet;
  totalTaxSet?: MoneySet;
  totalPriceSet?: MoneySet;
  lineItems?: { edges: Array<{ node: { title?: string | null; quantity?: number | null; sku?: string | null; originalUnitPriceSet?: MoneySet } }> };
  transactions?: Array<{ id?: string; kind?: string | null; status?: string | null; amountSet?: MoneySet }>;
};

function lineTotal(set: MoneySet, quantity: number): MoneySet {
  const amount = Number(set?.shopMoney?.amount);
  if (!Number.isFinite(amount)) return set;
  return { shopMoney: { amount: String(amount * quantity), currencyCode: set?.shopMoney?.currencyCode } };
}

function Row({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <InlineStack align="space-between">
      <Text as="span" fontWeight={strong ? 'semibold' : undefined} tone={strong ? undefined : 'subdued'}>{label}</Text>
      <Text as="span" fontWeight={strong ? 'semibold' : undefined} numeric>{value}</Text>
    </InlineStack>
  );
}

export default function OrderDetail() {
  const { id = '' } = useParams();
  const orderId = decodeURIComponent(id);
  const session = useSession();
  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .get(`/orders/${encodeURIComponent(orderId)}`)
      .then((res) => {
        const data = res.data?.data;
        setOrder((data?.order ?? data) || null);
      })
      .catch((err) => setError(errorMessage(err, 'Could not load this order')))
      .finally(() => setLoading(false));
  }, [orderId]);

  useEffect(() => {
    if (session.connected) load();
  }, [session.connected, load]);

  if (session.loading || (session.connected && loading)) return <PageSkeleton withAside />;
  if (!session.connected) return <NotConnected title="Order" error={session.error} />;
  if (error || !order) {
    return (
      <Page backAction={{ content: 'Orders', url: '/orders' }} title="Order">
        <ErrorBanner message={error || 'This order could not be found.'} onRetry={load} />
      </Page>
    );
  }

  const lineItems = order.lineItems?.edges ?? [];

  return (
    <Page
      backAction={{ content: 'Orders', url: '/orders' }}
      title={order.name || 'Order'}
      titleMetadata={
        <InlineStack gap="100">
          <Badge tone={financialTone(order.displayFinancialStatus)}>{humanize(order.displayFinancialStatus)}</Badge>
          <Badge tone={fulfillmentTone(order.displayFulfillmentStatus)}>{humanize(order.displayFulfillmentStatus)}</Badge>
        </InlineStack>
      }
      subtitle={formatDateTime(order.createdAt)}
    >
      <Layout>
        <Layout.Section>
          <BlockStack gap="400">
            <Card padding="0">
              <Box padding="400">
                <Text as="h2" variant="headingMd">{`Items (${lineItems.length})`}</Text>
              </Box>
              {lineItems.map(({ node }, index) => {
                const quantity = node.quantity ?? 0;
                return (
                  <Box key={`${node.title}-${index}`} padding="400" borderBlockStartWidth="025" borderColor="border">
                    <InlineStack align="space-between" blockAlign="center" gap="300" wrap={false}>
                      <Box minWidth="0" width="100%">
                        <BlockStack gap="050">
                          <Text as="span" fontWeight="semibold" truncate>{node.title || 'Item'}</Text>
                          {node.sku && <Text as="span" variant="bodySm" tone="subdued">{`SKU ${node.sku}`}</Text>}
                        </BlockStack>
                      </Box>
                      <span className="mia-nowrap">
                        <Text as="span" tone="subdued" numeric>{`${formatShopMoney(node.originalUnitPriceSet)} × ${quantity}`}</Text>
                      </span>
                      <span className="mia-nowrap">
                        <Text as="span" numeric>{formatShopMoney(lineTotal(node.originalUnitPriceSet ?? null, quantity))}</Text>
                      </span>
                    </InlineStack>
                  </Box>
                );
              })}
            </Card>

            <Card>
              <BlockStack gap="200">
                <Text as="h2" variant="headingMd">Payment</Text>
                {order.subtotalPriceSet && <Row label="Subtotal" value={formatShopMoney(order.subtotalPriceSet)} />}
                {order.totalShippingPriceSet && <Row label="Shipping" value={formatShopMoney(order.totalShippingPriceSet)} />}
                {order.totalTaxSet && <Row label="Taxes" value={formatShopMoney(order.totalTaxSet)} />}
                <Divider />
                <Row label="Total" value={formatShopMoney(order.totalPriceSet)} strong />
              </BlockStack>
            </Card>
          </BlockStack>
        </Layout.Section>

        <Layout.Section variant="oneThird">
          <Card>
            <BlockStack gap="300">
              <Text as="h2" variant="headingMd">Transactions</Text>
              {(order.transactions ?? []).length === 0 ? (
                <Text as="p" tone="subdued">No transactions recorded.</Text>
              ) : (
                (order.transactions ?? []).map((transaction, index) => (
                  <InlineStack key={transaction.id || index} align="space-between" blockAlign="center">
                    <BlockStack gap="050">
                      <Text as="span">{humanize(transaction.kind)}</Text>
                      <Text as="span" variant="bodySm" tone="subdued">{humanize(transaction.status)}</Text>
                    </BlockStack>
                    <Text as="span" numeric>{formatShopMoney(transaction.amountSet)}</Text>
                  </InlineStack>
                ))
              )}
            </BlockStack>
          </Card>
        </Layout.Section>
      </Layout>
    </Page>
  );
}
