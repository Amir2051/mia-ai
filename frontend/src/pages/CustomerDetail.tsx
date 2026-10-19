import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { BlockStack, Box, Button, Card, InlineGrid, InlineStack, Layout, Page, Text } from '@shopify/polaris';
import api from '../services/api';
import StatCard from '../components/StatCard';
import { ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { Field } from '../components/MiaResults';
import { errorMessage, formatDateTime, formatMoney, formatNumber, formatShopMoney, idPath } from '../lib/format';
import { useSession } from '../lib/session';
import { customerEmail, customerName, type CustomerNode } from './Customers';

type Customer = CustomerNode & {
  orders?: {
    edges: Array<{
      node: {
        id?: string | null;
        name?: string | null;
        createdAt?: string | null;
        totalPriceSet?: { shopMoney?: { amount?: string | null; currencyCode?: string | null } | null } | null;
      };
    }>;
  };
};

export default function CustomerDetail() {
  const { id = '' } = useParams();
  const customerId = decodeURIComponent(id);
  const session = useSession();
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .get(`/customers/${encodeURIComponent(customerId)}`)
      .then((res) => {
        const payload = res.data?.data;
        setCustomer((payload?.customer ?? payload) || null);
      })
      .catch((err) => setError(errorMessage(err, 'Could not load this customer')))
      .finally(() => setLoading(false));
  }, [customerId]);

  useEffect(() => {
    if (session.connected) load();
  }, [session.connected, load]);

  if (session.loading || (session.connected && loading)) return <PageSkeleton withAside />;
  if (!session.connected) return <NotConnected title="Customer" error={session.error} />;
  if (error || !customer) {
    return (
      <Page backAction={{ content: 'Customers', url: '/customers' }} title="Customer">
        <ErrorBanner message={error || 'This customer could not be found.'} onRetry={load} />
      </Page>
    );
  }

  const orders = customer.orders?.edges ?? [];
  const currency = customer.amountSpent?.currencyCode || 'USD';
  const phone = customer.phone ?? customer.defaultPhoneNumber?.phoneNumber ?? null;

  return (
    <Page backAction={{ content: 'Customers', url: '/customers' }} title={customerName(customer)}>
      <Layout>
        <Layout.Section>
          <BlockStack gap="400">
            <InlineGrid columns={{ xs: 1, sm: 2 }} gap="400">
              <StatCard label="Amount spent" value={formatMoney(customer.amountSpent?.amount ?? null, currency)} />
              <StatCard label="Orders" value={formatNumber(customer.numberOfOrders ?? 0)} />
            </InlineGrid>
            <Card padding="0">
              <Box padding="400">
                <Text as="h2" variant="headingMd">Recent orders</Text>
              </Box>
              {orders.length === 0 ? (
                <Box padding="400" paddingBlockStart="0">
                  <Text as="p" tone="subdued">No orders yet.</Text>
                </Box>
              ) : (
                orders.map(({ node }, index) => (
                  <Box key={node.id || index} padding="400" borderBlockStartWidth="025" borderColor="border">
                    <InlineStack align="space-between" blockAlign="center">
                      <BlockStack gap="050">
                        {node.id ? (
                          <Button variant="plain" url={idPath('/orders', node.id)}>{node.name || 'Order'}</Button>
                        ) : (
                          <Text as="span" fontWeight="semibold">{node.name || 'Order'}</Text>
                        )}
                        <Text as="span" variant="bodySm" tone="subdued">{formatDateTime(node.createdAt)}</Text>
                      </BlockStack>
                      <Text as="span" numeric>{formatShopMoney(node.totalPriceSet, currency)}</Text>
                    </InlineStack>
                  </Box>
                ))
              )}
            </Card>
          </BlockStack>
        </Layout.Section>
        <Layout.Section variant="oneThird">
          <Card>
            <BlockStack gap="300">
              <Text as="h2" variant="headingMd">Contact</Text>
              <Field label="Email" value={customerEmail(customer) || 'Not shared by Shopify'} />
              <Field label="Phone" value={phone || 'Not shared by Shopify'} />
            </BlockStack>
          </Card>
        </Layout.Section>
      </Layout>
    </Page>
  );
}
