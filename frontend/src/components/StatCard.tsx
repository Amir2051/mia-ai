import { BlockStack, Card, SkeletonDisplayText, Text } from '@shopify/polaris';

export default function StatCard({
  label,
  value,
  helpText,
  loading = false,
}: {
  label: string;
  value: string;
  helpText?: string;
  loading?: boolean;
}) {
  return (
    <Card>
      <BlockStack gap="200">
        <Text as="h3" variant="bodyMd" tone="subdued">
          {label}
        </Text>
        {loading ? (
          <SkeletonDisplayText size="medium" />
        ) : (
          <Text as="p" variant="heading2xl">
            {value}
          </Text>
        )}
        {helpText && (
          <Text as="p" variant="bodySm" tone="subdued">
            {helpText}
          </Text>
        )}
      </BlockStack>
    </Card>
  );
}
