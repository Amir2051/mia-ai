import { useCallback, useEffect, useState } from 'react';
import { Banner, Box, Card, EmptyState, Icon, IndexTable, Page, Text, TextField } from '@shopify/polaris';
import { SearchIcon } from '@shopify/polaris-icons';
import { useNavigate } from 'react-router-dom';
import api from '../services/api';
import { EMPTY_STATE_IMAGE, ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, formatMoney, formatNumber, gidTail, idPath } from '../lib/format';
import { useDebounced } from '../lib/useDebounced';
import { useSession } from '../lib/session';

export type CustomerNode = {
  id: string;
  firstName?: string | null;
  lastName?: string | null;
  displayName?: string | null;
  email?: string | null;
  phone?: string | null;
  defaultEmailAddress?: { emailAddress?: string | null } | null;
  defaultPhoneNumber?: { phoneNumber?: string | null } | null;
  numberOfOrders?: number | string | null;
  amountSpent?: { amount?: string | null; currencyCode?: string | null } | null;
};

/** Customer name/email are protected data; fall back gracefully when Shopify withholds them. */
export function customerName(node: CustomerNode): string {
  const name = node.displayName || [node.firstName, node.lastName].filter(Boolean).join(' ');
  return name || node.email || node.defaultEmailAddress?.emailAddress || `Customer ${gidTail(node.id)}`;
}

export function customerEmail(node: CustomerNode): string | null {
  return node.email ?? node.defaultEmailAddress?.emailAddress ?? null;
}

export default function Customers() {
  const session = useSession();
  const navigate = useNavigate();
  const [items, setItems] = useState<CustomerNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const search = useDebounced(query);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .get(`/customers/?query=${encodeURIComponent(search)}`)
      .then((res) => {
        const edges = res.data?.data?.customers?.edges;
        setItems(Array.isArray(edges) ? edges.map((edge: { node: CustomerNode }) => edge?.node).filter((node: CustomerNode) => !!node?.id) : []);
      })
      .catch((err) => {
        setItems([]);
        setError(errorMessage(err, 'Could not load customers'));
      })
      .finally(() => setLoading(false));
  }, [search]);

  useEffect(() => {
    if (session.connected) load();
  }, [session.connected, load]);

  if (session.loading) return <PageSkeleton title="Customers" />;
  if (!session.connected) return <NotConnected title="Customers" />;

  const namesHidden = items.length > 0 && items.every((node) => !node.displayName && !node.firstName && !customerEmail(node));

  return (
    <Page title="Customers">
      {error && (
        <Box paddingBlockEnd="400">
          <ErrorBanner message={error} onRetry={load} />
        </Box>
      )}
      {namesHidden && (
        <Box paddingBlockEnd="400">
          <Banner tone="info" title="Customer names are hidden by Shopify">
            <p>Shopify shares customer names and contact details with apps only after it approves protected customer data access. Order counts and spend are still shown.</p>
          </Banner>
        </Box>
      )}
      <Card padding="0">
        <Box padding="300">
          <TextField
            label="Search customers"
            labelHidden
            prefix={<Icon source={SearchIcon} />}
            placeholder="Search customers"
            value={query}
            onChange={setQuery}
            clearButton
            onClearButtonClick={() => setQuery('')}
            autoComplete="off"
          />
        </Box>
        <IndexTable
          resourceName={{ singular: 'customer', plural: 'customers' }}
          itemCount={items.length}
          selectable={false}
          loading={loading}
          headings={[{ title: 'Customer' }, { title: 'Email' }, { title: 'Orders', alignment: 'end' }, { title: 'Amount spent', alignment: 'end' }]}
          emptyState={
            <EmptyState heading={search ? 'No customers match your search' : 'No customers yet'} image={EMPTY_STATE_IMAGE}>
              <p>{search ? 'Try a different name or email.' : 'Customers appear here after their first order.'}</p>
            </EmptyState>
          }
        >
          {items.map((node, index) => (
            <IndexTable.Row id={node.id} key={node.id} position={index} onClick={() => navigate(idPath('/customers', node.id))}>
              <IndexTable.Cell>
                <Text as="span" fontWeight="semibold">{customerName(node)}</Text>
              </IndexTable.Cell>
              <IndexTable.Cell>
                <Text as="span" tone="subdued">{customerEmail(node) || '—'}</Text>
              </IndexTable.Cell>
              <IndexTable.Cell>
                <Text as="span" alignment="end" numeric>{formatNumber(node.numberOfOrders ?? 0)}</Text>
              </IndexTable.Cell>
              <IndexTable.Cell>
                <Text as="span" alignment="end" numeric>{formatMoney(node.amountSpent?.amount ?? null, node.amountSpent?.currencyCode || 'USD')}</Text>
              </IndexTable.Cell>
            </IndexTable.Row>
          ))}
        </IndexTable>
      </Card>
    </Page>
  );
}
