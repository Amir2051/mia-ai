import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  Banner,
  BlockStack,
  Box,
  Button,
  Card,
  Collapsible,
  EmptyState,
  FormLayout,
  InlineStack,
  Layout,
  Link,
  Page,
  Select,
  Text,
  TextField,
} from '@shopify/polaris';
import { SendIcon } from '@shopify/polaris-icons';
import api from '../services/api';
import { CardSkeleton, EMPTY_STATE_IMAGE, ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, formatDateTime } from '../lib/format';
import { showToast } from '../lib/toast';
import { useSession } from '../lib/session';

type Account = { id: number; provider: string; account_type: string; account_name: string };
type Post = { id: string; message?: string; created_time?: string; permalink_url?: string };

const TYPE_LABELS: Record<string, string> = { facebook_page: 'Facebook Page', instagram_business: 'Instagram' };

export default function Social() {
  const session = useSession();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [accountId, setAccountId] = useState('');
  const [message, setMessage] = useState('');
  const [link, setLink] = useState('');
  const [publishing, setPublishing] = useState(false);
  const [posts, setPosts] = useState<Record<number, Post[]>>({});
  const [loadingPosts, setLoadingPosts] = useState<number | null>(null);
  const [devOpen, setDevOpen] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api
      .get('/social/accounts')
      .then((res) => {
        const rows: Account[] = res.data?.accounts ?? [];
        setAccounts(rows);
        const firstPage = rows.find((account) => account.account_type === 'facebook_page');
        if (firstPage) setAccountId((value) => value || String(firstPage.id));
        setError(null);
      })
      .catch((err) => setError(errorMessage(err, 'Could not load your social accounts')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!session.connected) return;
    load();
    const params = new URLSearchParams(window.location.search);
    if (params.get('connected') === '1') {
      setNotice('Your Meta account is connected.');
      window.history.replaceState({}, '', '/social');
    }
  }, [session.connected, load]);

  if (session.loading) return <PageSkeleton title="Social" withAside />;
  if (!session.connected) return <NotConnected title="Social" />;

  const pages = accounts.filter((account) => account.account_type === 'facebook_page');

  const connectMeta = async () => {
    setError(null);
    try {
      const res = await api.get('/social/meta/connect');
      const url = res.data?.authorization_url;
      if (!url) throw new Error('Meta did not return a sign-in link');
      // Meta's login page can't render inside the Shopify admin iframe.
      window.open(url, '_top');
    } catch (err) {
      setError(errorMessage(err, 'Could not start the Meta connection'));
    }
  };

  const connectTestToken = async () => {
    setError(null);
    try {
      const res = await api.post('/social/meta/test-connect');
      showToast(`Test account connected (${res.data?.pages_saved ?? 0} pages)`);
      load();
    } catch (err) {
      setError(errorMessage(err, 'Could not connect the configured test token'));
    }
  };

  const publish = async () => {
    if (!message.trim() || !accountId) return;
    setPublishing(true);
    setError(null);
    try {
      await api.post('/social/meta/facebook/publish', { account_id: Number(accountId), message: message.trim(), link: link.trim() || null });
      setMessage('');
      setLink('');
      showToast('Posted to Facebook');
      setPosts((value) => {
        const next = { ...value };
        delete next[Number(accountId)];
        return next;
      });
    } catch (err) {
      setError(errorMessage(err, 'Facebook did not accept the post'));
    } finally {
      setPublishing(false);
    }
  };

  const loadPosts = async (id: number) => {
    setLoadingPosts(id);
    setError(null);
    try {
      const res = await api.get(`/social/meta/facebook/${id}/monitor`);
      setPosts((value) => ({ ...value, [id]: (res.data?.data ?? []) as Post[] }));
    } catch (err) {
      setError(errorMessage(err, 'Could not load recent posts'));
    } finally {
      setLoadingPosts(null);
    }
  };

  return (
    <Page
      title="Social"
      subtitle="Publish to your Facebook Page and keep an eye on recent posts"
      primaryAction={accounts.length ? { content: 'Connect another account', onAction: connectMeta } : undefined}
    >
      <Layout>
        <Layout.Section>
          <BlockStack gap="400">
            {notice && <Banner tone="success" onDismiss={() => setNotice(null)}><p>{notice}</p></Banner>}
            {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

            {loading ? (
              <CardSkeleton />
            ) : accounts.length === 0 ? (
              <Card>
                <EmptyState
                  heading="Connect Facebook and Instagram"
                  action={{ content: 'Connect with Meta', onAction: connectMeta }}
                  image={EMPTY_STATE_IMAGE}
                >
                  <p>Post Mia’s campaign copy straight to your Facebook Page and see how recent posts are doing.</p>
                </EmptyState>
              </Card>
            ) : (
              <>
                {pages.length > 0 && (
                  <Card>
                    <BlockStack gap="300">
                      <Text as="h2" variant="headingMd">New Facebook post</Text>
                      <FormLayout>
                        {pages.length > 1 && (
                          <Select
                            label="Page"
                            options={pages.map((page) => ({ label: page.account_name, value: String(page.id) }))}
                            value={accountId}
                            onChange={setAccountId}
                          />
                        )}
                        <TextField
                          label="Post"
                          value={message}
                          onChange={setMessage}
                          multiline={5}
                          autoComplete="off"
                          maxLength={5000}
                          showCharacterCount
                          helpText="Tip: generate copy in SEO & marketing, then paste it here."
                        />
                        <TextField label="Link (optional)" type="url" value={link} onChange={setLink} autoComplete="off" placeholder="https://" />
                      </FormLayout>
                      <InlineStack align="end">
                        <Button variant="primary" icon={SendIcon} onClick={publish} loading={publishing} disabled={!message.trim()}>
                          Publish to Facebook
                        </Button>
                      </InlineStack>
                    </BlockStack>
                  </Card>
                )}

                {pages.map((page) => (
                  <Card key={page.id}>
                    <BlockStack gap="300">
                      <InlineStack align="space-between" blockAlign="center">
                        <Text as="h2" variant="headingMd">{`Recent posts · ${page.account_name}`}</Text>
                        <Button onClick={() => loadPosts(page.id)} loading={loadingPosts === page.id}>
                          {posts[page.id] ? 'Refresh' : 'Load posts'}
                        </Button>
                      </InlineStack>
                      {posts[page.id] &&
                        (posts[page.id].length === 0 ? (
                          <Text as="p" tone="subdued">No posts yet.</Text>
                        ) : (
                          posts[page.id].slice(0, 10).map((post) => (
                            <Box key={post.id} paddingBlockStart="300" borderBlockStartWidth="025" borderColor="border">
                              <BlockStack gap="100">
                                <Text as="p"><span className="mia-pre-wrap">{post.message || '(No text — media post)'}</span></Text>
                                <InlineStack gap="200">
                                  <Text as="span" variant="bodySm" tone="subdued">{formatDateTime(post.created_time)}</Text>
                                  {post.permalink_url && <Link url={post.permalink_url} external>View on Facebook</Link>}
                                </InlineStack>
                              </BlockStack>
                            </Box>
                          ))
                        ))}
                    </BlockStack>
                  </Card>
                ))}
              </>
            )}
          </BlockStack>
        </Layout.Section>

        <Layout.Section variant="oneThird">
          <BlockStack gap="400">
            <Card>
              <BlockStack gap="300">
                <Text as="h2" variant="headingMd">Connected accounts</Text>
                {loading ? (
                  <Text as="p" tone="subdued">Loading…</Text>
                ) : accounts.length === 0 ? (
                  <Text as="p" tone="subdued">None yet.</Text>
                ) : (
                  accounts.map((account) => (
                    <InlineStack key={account.id} align="space-between" blockAlign="center" gap="200">
                      <Text as="span">{account.account_name}</Text>
                      <Badge>{TYPE_LABELS[account.account_type] || account.account_type}</Badge>
                    </InlineStack>
                  ))
                )}
              </BlockStack>
            </Card>
            <Card>
              <BlockStack gap="200">
                <InlineStack>
                  <Button variant="plain" disclosure={devOpen ? 'up' : 'down'} onClick={() => setDevOpen((value) => !value)}>
                    Developer options
                  </Button>
                </InlineStack>
                <Collapsible open={devOpen} id="social-dev-options">
                  <BlockStack gap="200">
                    <Text as="p" variant="bodySm" tone="subdued">Connect the Meta test token configured on the server. For testing only.</Text>
                    <InlineStack>
                      <Button onClick={connectTestToken}>Connect test token</Button>
                    </InlineStack>
                  </BlockStack>
                </Collapsible>
              </BlockStack>
            </Card>
          </BlockStack>
        </Layout.Section>
      </Layout>
    </Page>
  );
}
