import { useCallback, useEffect, useState } from 'react';
import { Badge, Box, Card, EmptyState, Icon, IndexTable, Page, Text, TextField } from '@shopify/polaris';
import { ImportIcon, MagicIcon, SearchIcon } from '@shopify/polaris-icons';
import { useNavigate } from 'react-router-dom';
import api from '../services/api';
import { EMPTY_STATE_IMAGE, ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, formatNumber, formatPrice, humanize, idPath, productTone } from '../lib/format';
import { useDebounced } from '../lib/useDebounced';
import { useSession } from '../lib/session';

type ProductNode = {
  id: string;
  title: string;
  status: string;
  totalInventory: number | null;
  variants?: { edges: Array<{ node: { sku?: string | null; price?: string | null } }> };
};

function InventoryBadge({ count }: { count: number | null }) {
  if (count === null || count === undefined) return <Text as="span" tone="subdued">—</Text>;
  if (count <= 0) return <Badge tone="critical">Out of stock</Badge>;
  if (count < 10) return <Badge tone="warning">{`${count} in stock`}</Badge>;
  return <Text as="span">{`${formatNumber(count)} in stock`}</Text>;
}

export default function Products() {
  const session = useSession();
  const navigate = useNavigate();
  const [items, setItems] = useState<ProductNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const search = useDebounced(query);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .get(`/products/?query=${encodeURIComponent(search)}`)
      .then((res) => setItems((res.data?.data?.products?.edges ?? []).map((edge: { node: ProductNode }) => edge.node)))
      .catch((err) => {
        setItems([]);
        setError(errorMessage(err, 'Could not load products'));
      })
      .finally(() => setLoading(false));
  }, [search]);

  useEffect(() => {
    if (session.connected) load();
  }, [session.connected, load]);

  if (session.loading) return <PageSkeleton title="Products" />;
  if (!session.connected) return <NotConnected title="Products" error={session.error} />;

  return (
    <Page
      title="Products"
      primaryAction={{ content: 'Optimize with Mia', icon: MagicIcon, url: '/marketing' }}
      secondaryActions={[{ content: 'Import CSV', icon: ImportIcon, url: '/imports' }]}
    >
      {error && (
        <Box paddingBlockEnd="400">
          <ErrorBanner message={error} onRetry={load} />
        </Box>
      )}
      <Card padding="0">
        <Box padding="300">
          <TextField
            label="Search products"
            labelHidden
            prefix={<Icon source={SearchIcon} />}
            placeholder="Search by title, SKU or tag"
            value={query}
            onChange={setQuery}
            clearButton
            onClearButtonClick={() => setQuery('')}
            autoComplete="off"
          />
        </Box>
        <IndexTable
          resourceName={{ singular: 'product', plural: 'products' }}
          itemCount={items.length}
          selectable={false}
          loading={loading}
          headings={[
            { title: 'Product' },
            { title: 'Status' },
            { title: 'Inventory' },
            { title: 'SKU' },
            { title: 'Price', alignment: 'end' },
          ]}
          emptyState={
            <EmptyState
              heading={search ? 'No products match your search' : 'Add your first product'}
              image={EMPTY_STATE_IMAGE}
              action={search ? undefined : { content: 'Import from CSV', url: '/imports' }}
            >
              <p>{search ? 'Try a different title, SKU or tag.' : 'Import a supplier sheet or add products in Shopify.'}</p>
            </EmptyState>
          }
        >
          {items.map((product, index) => {
            const variant = product.variants?.edges?.[0]?.node;
            return (
              <IndexTable.Row
                id={product.id}
                key={product.id}
                position={index}
                onClick={() => navigate(idPath('/products', product.id))}
              >
                <IndexTable.Cell>
                  <Text as="span" fontWeight="semibold">
                    {product.title || 'Untitled'}
                  </Text>
                </IndexTable.Cell>
                <IndexTable.Cell>
                  <Badge tone={productTone(product.status)}>{humanize(product.status)}</Badge>
                </IndexTable.Cell>
                <IndexTable.Cell>
                  <InventoryBadge count={product.totalInventory} />
                </IndexTable.Cell>
                <IndexTable.Cell>
                  <Text as="span" tone="subdued">
                    {variant?.sku || '—'}
                  </Text>
                </IndexTable.Cell>
                <IndexTable.Cell>
                  <Text as="span" alignment="end" numeric>
                    {formatPrice(variant?.price)}
                  </Text>
                </IndexTable.Cell>
              </IndexTable.Row>
            );
          })}
        </IndexTable>
      </Card>
    </Page>
  );
}
