import {
  Banner,
  BlockStack,
  Card,
  EmptyState,
  Layout,
  Page,
  SkeletonBodyText,
  SkeletonDisplayText,
  SkeletonPage,
} from '@shopify/polaris';

export const EMPTY_STATE_IMAGE =
  'https://cdn.shopify.com/s/files/1/0262/4071/2726/files/emptystate-files.png';

export function PageSkeleton({ title, withAside = false }: { title?: string; withAside?: boolean }) {
  return (
    <SkeletonPage title={title} primaryAction>
      <Layout>
        <Layout.Section>
          <Card>
            <BlockStack gap="400">
              <SkeletonDisplayText size="small" />
              <SkeletonBodyText lines={4} />
            </BlockStack>
          </Card>
          <Card>
            <SkeletonBodyText lines={6} />
          </Card>
        </Layout.Section>
        {withAside && (
          <Layout.Section variant="oneThird">
            <Card>
              <SkeletonBodyText lines={5} />
            </Card>
          </Layout.Section>
        )}
      </Layout>
    </SkeletonPage>
  );
}

export function CardSkeleton({ lines = 5 }: { lines?: number }) {
  return (
    <Card>
      <SkeletonBodyText lines={lines} />
    </Card>
  );
}

export function NotConnected({ title }: { title: string }) {
  return (
    <Page title={title}>
      <Card>
        <EmptyState
          heading="Open Mia from your Shopify admin"
          image={EMPTY_STATE_IMAGE}
          action={{ content: 'Continue with Shopify', url: '/login' }}
        >
          <p>Mia needs an active Shopify admin session to read your store. If you opened Mia directly, continue with Shopify and select the store where Mia is installed. If you are already in Shopify Admin, reopen Mia from Apps so Shopify can provide a secure session.</p>
        </EmptyState>
      </Card>
    </Page>
  );
}

export function ErrorBanner({
  message,
  onRetry,
  onDismiss,
  title = 'Something went wrong',
}: {
  message: string;
  onRetry?: () => void;
  onDismiss?: () => void;
  title?: string;
}) {
  return (
    <Banner
      tone="critical"
      title={title}
      action={onRetry ? { content: 'Try again', onAction: onRetry } : undefined}
      onDismiss={onDismiss}
    >
      <p>{message}</p>
    </Banner>
  );
}
