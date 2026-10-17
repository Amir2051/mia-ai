import { useCallback, useEffect, useState } from 'react';
import { Badge, Box, Card, EmptyState, Icon, IndexTable, Page, Text, TextField } from '@shopify/polaris';
import { SearchIcon } from '@shopify/polaris-icons';
import { useNavigate } from 'react-router-dom';
import api from '../services/api';
import { EMPTY_STATE_IMAGE, ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, financialTone, formatDateTime, formatShopMoney, fulfillmentTone, humanize, idPath } from '../lib/format';
import { useDebounced } from '../lib/useDebounced';
import { useSession } from '../lib/session';

export type OrderNode = {
  id: string;
  name: string;
  createdAt: string;
  displayFinancialStatus?: string | null;
  displayFulfillmentStatus?: string | null;
  totalPriceSet?: { shopMoney?: { amount?: string | null; currencyCode?: string | null } | null } | null;
  lineItems?: { edges: Array<{ node: { quantity?: number | null } }> };
};

export default function Orders() {
  const session = useSession();
  const navigate = useNavigate();
  const [items, setItems] = useState<OrderNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const search = useDebounced(query);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .get(`/orders/?query=${encodeURIComponent(search)}`)
      .then((res) => setItems((res.data?.data?.orders?.edges ?? []).map((edge: { node: OrderNode }) => edge.node)))
      .catch((err) => {
        setItems([]);
        setError(errorMessage(err, 'Could not load orders'));
      })
      .finally(() => setLoading(false));
  }, [search]);

  useEffect(() => {
    if (session.connected) load();
  }, [session.connected, load]);

  if (session.loading) return <PageSkeleton title="Orders" />;
  if (!session.connected) return <NotConnected title="Orders" />;

  return (
    <Page title="Orders">
      {error && (
        <Box paddingBlockEnd="400">
          <ErrorBanner message={error} onRetry={load} />
        </Box>
      )}
      <Card padding="0">
        <Box padding="300">
          <TextField
            label="Search orders"
            labelHidden
            prefix={<Icon source={SearchIcon} />}
            placeholder="Search by order number"
            value={query}
            onChange={setQuery}
            clearButton
            onClearButtonClick={() => setQuery('')}
            autoComplete="off"
          />
        </Box>
        <IndexTable
          resourceName={{ singular: 'order', plural: 'orders' }}
          itemCount={items.length}
          selectable={false}
          loading={loading}
          headings={[
            { title: 'Order' },
            { title: 'Date' },
            { title: 'Total', alignment: 'end' },
            { title: 'Payment' },
            { title: 'Fulfillment' },
            { title: 'Items', alignment: 'end' },
          ]}
          emptyState={
            <EmptyState heading={search ? 'No orders match your search' : 'No orders yet'} image={EMPTY_STATE_IMAGE}>
              <p>{search ? 'Try a different order number.' : 'Orders from your store will show up here.'}</p>
            </EmptyState>
          }
        >
          {items.map((order, index) => {
            const itemCount = (order.lineItems?.edges ?? []).reduce((sum, { node }) => sum + (node.quantity ?? 0), 0);
            return (
              <IndexTable.Row id={order.id} key={order.id} position={index} onClick={() => navigate(idPath('/orders', order.id))}>
                <IndexTable.Cell>
                  <Text as="span" fontWeight="semibold">{order.name || 'Order'}</Text>
                </IndexTable.Cell>
                <IndexTable.Cell>{formatDateTime(order.createdAt)}</IndexTable.Cell>
                <IndexTable.Cell>
                  <Text as="span" alignment="end" numeric>{formatShopMoney(order.totalPriceSet)}</Text>
                </IndexTable.Cell>
                <IndexTable.Cell>
                  <Badge tone={financialTone(order.displayFinancialStatus)} progress={order.displayFinancialStatus === 'PAID' ? 'complete' : 'partiallyComplete'}>
                    {humanize(order.displayFinancialStatus)}
                  </Badge>
                </IndexTable.Cell>
                <IndexTable.Cell>
                  <Badge tone={fulfillmentTone(order.displayFulfillmentStatus)} progress={order.displayFulfillmentStatus === 'FULFILLED' ? 'complete' : 'incomplete'}>
                    {humanize(order.displayFulfillmentStatus)}
                  </Badge>
                </IndexTable.Cell>
                <IndexTable.Cell>
                  <Text as="span" alignment="end" numeric>{itemCount}</Text>
                </IndexTable.Cell>
              </IndexTable.Row>
            );
          })}
        </IndexTable>
      </Card>
    </Page>
  );
}
