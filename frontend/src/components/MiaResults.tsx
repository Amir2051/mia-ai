import { useState } from 'react';
import { Badge, BlockStack, Box, Button, Card, Divider, InlineGrid, InlineStack, Tag, Text } from '@shopify/polaris';
import { ClipboardIcon } from '@shopify/polaris-icons';
import SafeHtml from './SafeHtml';
import { copyText } from '../lib/toast';

export type SeoResult = {
  seo_title: string;
  meta_description: string;
  optimized_title: string;
  optimized_description_html: string;
  keywords?: string[];
  tags?: string[];
  handle_suggestion?: string;
  image_alt_text?: string[];
  seo_score?: number;
  issues?: string[];
  recommendations?: string[];
};

export type MarketingResult = {
  product_title?: string;
  image_url?: string | null;
  enhanced_image_url?: string | null;
  facebook?: { copy: string; cta?: string };
  instagram?: { copy: string; cta?: string };
  tiktok?: { copy: string; cta?: string };
  email?: { subject: string; body: string };
  ad?: { primary_text: string; headline: string; description: string; cta: string };
  creative_prompt?: string;
  hashtags?: string[];
};

export function scoreTone(score?: number) {
  if (score === undefined || score === null) return undefined;
  if (score >= 80) return 'success' as const;
  if (score >= 60) return 'attention' as const;
  return 'critical' as const;
}

export function Field({ label, value }: { label: string; value?: string | null }) {
  return (
    <BlockStack gap="100">
      <Text as="span" variant="bodySm" tone="subdued">
        {label}
      </Text>
      <Text as="p" breakWord>
        {value || '—'}
      </Text>
    </BlockStack>
  );
}

/** Compact SEO package used inside Mia chat and product pages. */
export function SeoSummary({ result, title }: { result: SeoResult; title?: string }) {
  return (
    <Card>
      <BlockStack gap="300">
        <InlineStack align="space-between" blockAlign="center" gap="200">
          <Text as="h3" variant="headingSm">
            SEO package{title ? ` · ${title}` : ''}
          </Text>
          {typeof result.seo_score === 'number' && (
            <Badge tone={scoreTone(result.seo_score)}>{`Score ${result.seo_score}/100`}</Badge>
          )}
        </InlineStack>
        <Field label="SEO title" value={result.seo_title} />
        <Field label="Meta description" value={result.meta_description} />
        <Field label="Optimized product title" value={result.optimized_title} />
        {!!result.keywords?.length && (
          <BlockStack gap="100">
            <Text as="span" variant="bodySm" tone="subdued">
              Keywords
            </Text>
            <InlineStack gap="100" wrap>
              {result.keywords.map((keyword) => (
                <Tag key={keyword}>{keyword}</Tag>
              ))}
            </InlineStack>
          </BlockStack>
        )}
        <Divider />
        <SafeHtml html={result.optimized_description_html} />
      </BlockStack>
    </Card>
  );
}

function CopyBlock({ title, text, cta }: { title: string; text: string; cta?: string }) {
  return (
    <Card>
      <BlockStack gap="300">
        <InlineStack align="space-between" blockAlign="center">
          <Text as="h3" variant="headingSm">
            {title}
          </Text>
          <Button icon={ClipboardIcon} variant="tertiary" onClick={() => copyText(cta ? `${text}\n\n${cta}` : text)} accessibilityLabel={`Copy ${title}`}>
            Copy
          </Button>
        </InlineStack>
        <Text as="p">
          <span className="mia-pre-wrap">{text}</span>
        </Text>
        {cta && <Badge>{`CTA: ${cta}`}</Badge>}
      </BlockStack>
    </Card>
  );
}

/** Channel-by-channel campaign copy with one-click copy. */
export function MarketingCopy({ result, compact = false }: { result: MarketingResult; compact?: boolean }) {
  const [showAll, setShowAll] = useState(!compact);
  const image = result.enhanced_image_url || result.image_url;
  const blocks = [
    result.facebook && { title: 'Facebook', text: result.facebook.copy, cta: result.facebook.cta },
    result.instagram && { title: 'Instagram', text: result.instagram.copy, cta: result.instagram.cta },
    result.tiktok && { title: 'TikTok', text: result.tiktok.copy, cta: result.tiktok.cta },
    result.ad && {
      title: 'Paid ad',
      text: `${result.ad.primary_text}\n\nHeadline: ${result.ad.headline}\nDescription: ${result.ad.description}`,
      cta: result.ad.cta,
    },
    result.email && { title: 'Email', text: `Subject: ${result.email.subject}\n\n${result.email.body}` },
    result.creative_prompt && { title: 'Creative direction', text: result.creative_prompt },
  ].filter(Boolean) as Array<{ title: string; text: string; cta?: string }>;
  const visible = showAll ? blocks : blocks.slice(0, 2);

  return (
    <BlockStack gap="400">
      {(image || !!result.hashtags?.length) && (
        <Card>
          <BlockStack gap="300">
            {image && <img className="mia-thumb" src={image} alt={result.product_title || 'Product'} />}
            {!!result.hashtags?.length && (
              <InlineStack gap="100" wrap>
                {result.hashtags.map((tag) => (
                  <Tag key={tag}>{tag}</Tag>
                ))}
              </InlineStack>
            )}
          </BlockStack>
        </Card>
      )}
      <InlineGrid columns={compact ? 1 : { xs: 1, md: 2 }} gap="400">
        {visible.map((block) => (
          <CopyBlock key={block.title} {...block} />
        ))}
      </InlineGrid>
      {compact && blocks.length > 2 && (
        <Box>
          <Button variant="plain" onClick={() => setShowAll((value) => !value)}>
            {showAll ? 'Show less' : `Show all ${blocks.length} channels`}
          </Button>
        </Box>
      )}
    </BlockStack>
  );
}
