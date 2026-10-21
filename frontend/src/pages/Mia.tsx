import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  Badge,
  BlockStack,
  Box,
  Button,
  Card,
  InlineStack,
  Layout,
  Page,
  Select,
  Spinner,
  Text,
  TextField,
} from '@shopify/polaris';
import { MagicIcon, SendIcon } from '@shopify/polaris-icons';
import api from '../services/api';
import { MarketingCopy, SeoSummary, type MarketingResult, type SeoResult } from '../components/MiaResults';
import { ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, formatNumber } from '../lib/format';
import { useSession } from '../lib/session';

type Product = { id: string; title: string; status?: string; inventory?: number | null };
type Context = {
  counts?: { products?: number | null; orders?: number | null; customers?: number | null; inventory_units?: number | null };
  available?: { products?: boolean; orders?: boolean; customers?: boolean; publications?: boolean };
  products?: Product[];
  low_stock?: Array<{ title: string; inventory: number }>;
  warnings?: string[];
};

function snapshotCount(value: number | null | undefined, available: boolean | undefined): string {
  if (value == null && available === false) return '—';
  return formatNumber(value ?? 0);
}
type Artifact =
  | { type: 'seo'; product?: { title?: string }; result: SeoResult }
  | { type: 'marketing'; product?: { title?: string }; result: MarketingResult };
type Message = { role: 'mia' | 'user'; text: string; artifact?: Artifact };

const GREETING: Message = {
  role: 'mia',
  text: "Hi, I'm Mia. Pick a product and I'll write SEO, descriptions and ready-to-post campaigns from your live Shopify data. Nothing changes in your store until you approve it.",
};

const SUGGESTIONS = [
  { label: 'Write an SEO package', prompt: (title: string) => `Analyze this product and generate a real SEO package: ${title}` },
  { label: 'Create a campaign', prompt: (title: string) => `Generate ready-to-post Facebook, Instagram, TikTok, email and paid-ad marketing for this product: ${title}` },
  { label: 'Find conversion issues', prompt: (title: string) => `Analyze this product for weak description, SEO issues and conversion opportunities: ${title}` },
];

export default function Mia() {
  const session = useSession();
  const [context, setContext] = useState<Context | null>(null);
  const [knowledge, setKnowledge] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState('');
  const [messages, setMessages] = useState<Message[]>([GREETING]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!session.connected) return;
    let active = true;
    Promise.allSettled([api.get('/mia/context?include_orders=true'), api.get('/mia/conversation')])
      .then(([contextResult, conversationResult]) => {
        if (!active) return;
        if (contextResult.status === 'fulfilled') {
          const data = contextResult.value.data;
          setContext(data);
          if (data?.products?.[0]) setSelected(data.products[0].id);
          if (data?.available?.products === false) {
            setLoadError('Mia could not retrieve the product catalog from Shopify. Product and inventory counts are unavailable, not zero.');
          }
        } else {
          setLoadError(errorMessage(contextResult.reason, 'Mia could not read your store right now.'));
        }
        if (conversationResult.status === 'fulfilled') {
          const saved = conversationResult.value.data?.messages;
          setKnowledge(Array.isArray(conversationResult.value.data?.knowledge) ? conversationResult.value.data.knowledge : []);
          if (Array.isArray(saved) && saved.length) {
            setMessages(saved.map((item: { role: string; content: string }) => ({
              role: item.role === 'user' ? 'user' : 'mia',
              text: item.content,
            })));
          }
        }
      })
      .catch((err) => {
        if (active) setLoadError(errorMessage(err, 'Mia could not load your store and conversation.'));
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [session.connected]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, busy]);

  if (session.loading) return <PageSkeleton title="Ask Mia" withAside />;
  if (!session.connected) return <NotConnected title="Ask Mia" error={session.error} />;

  const products = context?.products ?? [];
  const product = products.find((p) => p.id === selected);

  async function run(question: string) {
    setMessages((current) => [...current, { role: 'user', text: question }]);
    setBusy(true);
    try {
      const history = messages
        .slice(-10)
        .map((m) => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.text }));
      const res = await api.post('/mia/chat', {
        message: question,
        product_id: selected || undefined,
        history,
      });
      if (Array.isArray(res.data.knowledge)) setKnowledge(res.data.knowledge);
      const reply: Message = { role: 'mia', text: res.data.answer, artifact: res.data.artifact };
      const nextMessages = [...messages, { role: 'user' as const, text: question }, reply];
      setMessages(nextMessages);
      // Persist only text, not generated artifacts or live Shopify payloads.
      try {
        await api.put('/mia/conversation', {
          messages: nextMessages.map((message) => ({
            role: message.role === 'user' ? 'user' : 'assistant',
            content: message.text,
          })),
        });
        const savedState = await api.get('/mia/conversation');
        setKnowledge(Array.isArray(savedState.data?.knowledge) ? savedState.data.knowledge : []);
      } catch {
        // Chat still works if persistence is temporarily unavailable; next load can retry.
      }
    } catch (err) {
      setMessages((current) => [
        ...current,
        { role: 'mia', text: errorMessage(err, "I couldn't finish that. Please try again in a moment.") },
      ]);
    } finally {
      setBusy(false);
    }
  }

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const question = input.trim();
    if (!question || busy) return;
    setInput('');
    run(question);
  }

  return (
    <Page title="Ask Mia" subtitle="Your AI growth assistant for this store" titleMetadata={<Badge tone="magic">AI</Badge>}>
      <Layout>
        <Layout.Section variant="oneThird">
          <BlockStack gap="400">
            {loadError && <ErrorBanner message={loadError} />}
            <Card>
              <BlockStack gap="300">
                <Text as="h2" variant="headingMd">
                  Product
                </Text>
                <Select
                  label="Product Mia should work on"
                  labelHidden
                  disabled={loading || products.length === 0}
                  options={
                    products.length
                      ? products.map((p) => ({ label: p.title, value: p.id }))
                      : [{ label: loading ? 'Loading products…' : context?.available?.products === false ? 'Products unavailable' : 'No products found', value: '' }]
                  }
                  value={selected}
                  onChange={setSelected}
                />
                {product && (
                  <Text as="p" variant="bodySm" tone="subdued">
                    {formatNumber(product.inventory ?? 0)} in stock
                  </Text>
                )}
                <BlockStack gap="200">
                  {SUGGESTIONS.map((suggestion) => (
                    <Button
                      key={suggestion.label}
                      icon={MagicIcon}
                      textAlign="start"
                      fullWidth
                      disabled={busy || !product}
                      onClick={() => product && run(suggestion.prompt(product.title))}
                    >
                      {suggestion.label}
                    </Button>
                  ))}
                </BlockStack>
              </BlockStack>
            </Card>

            <Card>
              <BlockStack gap="300">
                <Text as="h2" variant="headingMd">
                  Store snapshot
                </Text>
                <InlineStack align="space-between">
                  <Text as="span" tone="subdued">Products</Text>
                  <Text as="span" numeric>{loading ? '…' : snapshotCount(context?.counts?.products, context?.available?.products)}</Text>
                </InlineStack>
                <InlineStack align="space-between">
                  <Text as="span" tone="subdued">Orders</Text>
                  <Text as="span" numeric>{loading ? '…' : snapshotCount(context?.counts?.orders, context?.available?.orders)}</Text>
                </InlineStack>
                <InlineStack align="space-between">
                  <Text as="span" tone="subdued">Units in stock</Text>
                  <Text as="span" numeric>{loading ? '…' : snapshotCount(context?.counts?.inventory_units, context?.available?.products)}</Text>
                </InlineStack>
              </BlockStack>
            </Card>

            {!!knowledge.length && (
              <Card>
                <BlockStack gap="200">
                  <Text as="h2" variant="headingMd">Mia remembers</Text>
                  <Text as="p" variant="bodySm" tone="subdued">These merchant notes are saved for future chats. Say “forget [note]” or “forget all my notes” to remove them.</Text>
                  {knowledge.slice(-6).map((note, index) => (
                    <Text key={`${index}-${note}`} as="p" variant="bodySm">• {note}</Text>
                  ))}
                  {knowledge.length > 6 && <Text as="p" variant="bodySm" tone="subdued">Showing the latest 6 of {knowledge.length} notes.</Text>}
                </BlockStack>
              </Card>
            )}

            {!!context?.low_stock?.length && (
              <Card>
                <BlockStack gap="300">
                  <Text as="h2" variant="headingMd">
                    Low stock
                  </Text>
                  {context.low_stock.slice(0, 6).map((item) => (
                    <InlineStack key={item.title} align="space-between" wrap={false} gap="200">
                      <Box minWidth="0" width="100%">
                        <Text as="span" truncate>
                          {item.title}
                        </Text>
                      </Box>
                      <span className="mia-nowrap">
                        <Badge tone={item.inventory <= 0 ? 'critical' : 'warning'}>
                          {item.inventory <= 0 ? 'Out of stock' : `${item.inventory} left`}
                        </Badge>
                      </span>
                    </InlineStack>
                  ))}
                </BlockStack>
              </Card>
            )}
          </BlockStack>
        </Layout.Section>

        <Layout.Section>
          <Card>
            <BlockStack gap="400">
              <div className="mia-chat-log" ref={logRef} aria-live="polite">
                {messages.map((message, index) => (
                  <BlockStack key={index} gap="200">
                    <div className={`mia-bubble mia-bubble--${message.role}`}>{message.text}</div>
                    {message.artifact?.type === 'seo' && (
                      <SeoSummary result={message.artifact.result} title={message.artifact.product?.title} />
                    )}
                    {message.artifact?.type === 'marketing' && <MarketingCopy result={message.artifact.result} compact />}
                  </BlockStack>
                ))}
                {busy && (
                  <InlineStack gap="200" blockAlign="center">
                    <Spinner size="small" accessibilityLabel="Mia is working" />
                    <Text as="span" tone="subdued">
                      Mia is working on it…
                    </Text>
                  </InlineStack>
                )}
              </div>
              <form onSubmit={submit}>
                <TextField
                  label="Message Mia"
                  labelHidden
                  value={input}
                  onChange={setInput}
                  autoComplete="off"
                  placeholder="Ask for SEO, a product description, a TikTok script…"
                  disabled={busy}
                  connectedRight={
                    <Button submit variant="primary" icon={SendIcon} disabled={busy || !input.trim()} accessibilityLabel="Send">
                      Send
                    </Button>
                  }
                />
              </form>
            </BlockStack>
          </Card>
        </Layout.Section>
      </Layout>
    </Page>
  );
}
