import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  Banner,
  BlockStack,
  Box,
  Button,
  Card,
  DataTable,
  Divider,
  DropZone,
  FormLayout,
  Icon,
  InlineGrid,
  InlineStack,
  Layout,
  List,
  Modal,
  Page,
  Select,
  Text,
  TextField,
} from '@shopify/polaris';
import { NoteIcon } from '@shopify/polaris-icons';
import api from '../services/api';
import { CardSkeleton, ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { IMPORT_FIELDS, inferMapping, parseCsv } from '../lib/csv';
import { errorMessage, formatDateTime, humanize, importTone } from '../lib/format';
import { showToast } from '../lib/toast';
import { useSession } from '../lib/session';

type Summary = {
  processed?: number | null;
  created?: number | null;
  updated?: number | null;
  failed?: number | null;
  skipped?: number | null;
  total?: number | null;
  valid?: number | null;
  warnings?: number | null;
  errors?: number | null;
  duplicates?: number | null;
};
type ImportRow = {
  id?: number | null;
  title?: string | null;
  source?: string | null;
  status?: string | null;
  sync_status?: string | null;
  error?: string | null;
  shopify_product_id?: string | null;
  created_at?: string | null;
  summary?: Summary;
};
type Detail = { row?: number; status?: string; error?: unknown; reason?: string; duplicate_match?: string };
type ImportRecord = { id?: number | null; status?: string | null; summary?: Summary; details?: Detail[] };
type Step = 'upload' | 'preview' | 'results';

const MAX_FILE_SIZE = 5 * 1024 * 1024;
const DUPLICATE_OPTIONS = [
  { label: 'Skip it', value: 'skip' },
  { label: 'Update the existing product', value: 'update' },
  { label: 'Create it anyway', value: 'create' },
];
const STATUS_OPTIONS = [
  { label: 'Draft', value: 'DRAFT' },
  { label: 'Active', value: 'ACTIVE' },
];

function normalizeDuplicateAction(value?: string | null): string {
  if (value === 'overwrite') return 'update';
  return DUPLICATE_OPTIONS.some((option) => option.value === value) ? (value as string) : 'skip';
}

function detailText(detail: Detail): string {
  const error = detail.error;
  if (Array.isArray(error)) return error.join('; ');
  if (typeof error === 'string') return error;
  if (detail.reason === 'duplicate') return `Matches an existing product${detail.duplicate_match ? ` by ${detail.duplicate_match}` : ''}`;
  return humanize(detail.status);
}

function Stat({ label, value, tone }: { label: string; value?: number | null; tone?: 'critical' | 'success' | 'caution' }) {
  return (
    <Box padding="300" background="bg-surface-secondary" borderRadius="200">
      <BlockStack gap="100">
        <Text as="span" variant="bodySm" tone="subdued">{label}</Text>
        <Text as="span" variant="headingLg" tone={tone}>{value ?? 0}</Text>
      </BlockStack>
    </Box>
  );
}

export default function Imports() {
  const session = useSession();
  const [history, setHistory] = useState<ImportRow[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ImportRow | null>(null);

  const [step, setStep] = useState<Step>('upload');
  const [fileName, setFileName] = useState('');
  const [fileContent, setFileContent] = useState('');
  const [fileError, setFileError] = useState<string | null>(null);
  const [columns, setColumns] = useState<string[]>([]);
  const [rows, setRows] = useState<Array<Record<string, string>>>([]);
  const [rowCount, setRowCount] = useState(0);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [config, setConfig] = useState({ title: '', supplier: '', category: '', duplicate_action: 'skip', status: 'DRAFT' });
  const [current, setCurrent] = useState<ImportRecord | null>(null);
  const [working, setWorking] = useState<null | 'preview' | 'run'>(null);
  const [error, setError] = useState<string | null>(null);

  const loadHistory = useCallback(() => {
    setHistoryLoading(true);
    setHistoryError(null);
    api
      .get('/imports/')
      .then((res) => setHistory((res.data?.data?.imports ?? []) as ImportRow[]))
      .catch((err) => setHistoryError(errorMessage(err, 'Could not load import history')))
      .finally(() => setHistoryLoading(false));
  }, []);

  useEffect(() => {
    if (!session.connected) return;
    loadHistory();
    // Use the merchant's saved import defaults from Settings.
    api
      .get('/settings/')
      .then((res) => {
        const settings = res.data?.settings || {};
        setConfig((value) => ({
          ...value,
          duplicate_action: normalizeDuplicateAction(settings.duplicate_product_action),
          status: settings.import_default_status === 'ACTIVE' ? 'ACTIVE' : 'DRAFT',
        }));
      })
      .catch(() => undefined);
  }, [session.connected, loadHistory]);

  if (session.loading) return <PageSkeleton title="Import products" withAside />;
  if (!session.connected) return <NotConnected title="Import products" error={session.error} />;

  const readFile = (file: File) => {
    setFileError(null);
    if (!/\.csv$/i.test(file.name) && file.type !== 'text/csv') {
      setFileError('Choose a .csv file.');
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      setFileError('The file is larger than 5 MB. Split it into smaller files.');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const content = typeof reader.result === 'string' ? reader.result : '';
      try {
        const parsed = parseCsv(content);
        setFileName(file.name);
        setFileContent(content);
        setColumns(parsed.columns);
        setRows(parsed.rows.slice(0, 5));
        setRowCount(parsed.rows.length);
        setMapping(inferMapping(parsed.columns));
        setConfig((value) => ({ ...value, title: value.title || file.name.replace(/\.csv$/i, '') }));
      } catch (err) {
        setFileError(err instanceof Error ? err.message : 'This CSV could not be read.');
      }
    };
    reader.onerror = () => setFileError('This file could not be read.');
    reader.readAsText(file);
  };

  const reset = () => {
    setStep('upload');
    setFileName('');
    setFileContent('');
    setColumns([]);
    setRows([]);
    setRowCount(0);
    setMapping({});
    setCurrent(null);
    setError(null);
    setConfig((value) => ({ ...value, title: '', supplier: '', category: '' }));
  };

  // Explicit "" tells the server not to fall back to its default column.
  const mappingPayload = () => Object.fromEntries(IMPORT_FIELDS.map((field) => [field.key, mapping[field.key] || '']));

  const preview = async () => {
    setWorking('preview');
    setError(null);
    try {
      const res = await api.post('/imports/preview', {
        content: fileContent,
        title: config.title,
        description: '',
        supplier: config.supplier,
        category: config.category,
        tags: [],
        status: config.status,
        duplicate_action: config.duplicate_action,
        mapping: mappingPayload(),
        validate_only: true,
      });
      setCurrent((res.data?.data?.import as ImportRecord) || null);
      setStep('preview');
    } catch (err) {
      setError(errorMessage(err, 'The preview failed. Check the file and try again.'));
    } finally {
      setWorking(null);
    }
  };

  const run = async () => {
    if (!current?.id) {
      setError('Preview the file before importing.');
      return;
    }
    setWorking('run');
    setError(null);
    try {
      const res = await api.post(`/imports/${current.id}/run`, {
        content: fileContent,
        mapping: mappingPayload(),
        status: config.status,
        duplicate_action: config.duplicate_action,
      });
      setCurrent((res.data?.data?.import as ImportRecord) || null);
      setStep('results');
      showToast('Import finished');
      loadHistory();
    } catch (err) {
      setError(errorMessage(err, 'The import failed.'));
    } finally {
      setWorking(null);
    }
  };

  const summary = current?.summary || {};
  const problemRows = (current?.details ?? []).filter((detail) => detail.status === 'failed' || detail.error);
  const sampleColumns = columns.slice(0, 6);
  const titleMapped = !!mapping.title;

  return (
    <Page title="Import products" subtitle="Bring supplier sheets and Shopify exports into your store">
      <Layout>
        <Layout.Section>
          <BlockStack gap="400">
            {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

            <InlineStack gap="200">
              <Badge tone={step === 'upload' ? 'info' : undefined} progress={step === 'upload' ? 'partiallyComplete' : 'complete'}>1. Upload &amp; map</Badge>
              <Badge tone={step === 'preview' ? 'info' : undefined} progress={step === 'results' ? 'complete' : step === 'preview' ? 'partiallyComplete' : 'incomplete'}>2. Review</Badge>
              <Badge tone={step === 'results' ? 'success' : undefined} progress={step === 'results' ? 'complete' : 'incomplete'}>3. Import</Badge>
            </InlineStack>

            {step === 'upload' && (
              <>
                <Card>
                  <BlockStack gap="300">
                    <Text as="h2" variant="headingMd">CSV file</Text>
                    {fileName ? (
                      <InlineStack align="space-between" blockAlign="center">
                        <InlineStack gap="200" blockAlign="center">
                          <Icon source={NoteIcon} tone="subdued" />
                          <BlockStack gap="050">
                            <Text as="span" fontWeight="semibold">{fileName}</Text>
                            <Text as="span" variant="bodySm" tone="subdued">{`${rowCount} product row${rowCount === 1 ? '' : 's'} · ${columns.length} columns`}</Text>
                          </BlockStack>
                        </InlineStack>
                        <Button onClick={reset}>Choose another file</Button>
                      </InlineStack>
                    ) : (
                      <DropZone
                        accept=".csv,text/csv"
                        type="file"
                        allowMultiple={false}
                        onDrop={(_files, accepted) => accepted[0] && readFile(accepted[0])}
                        error={!!fileError}
                      >
                        <DropZone.FileUpload actionTitle="Add CSV file" actionHint="Accepts .csv up to 5 MB" />
                      </DropZone>
                    )}
                    {fileError && <Banner tone="critical"><p>{fileError}</p></Banner>}
                  </BlockStack>
                </Card>

                {fileName && (
                  <>
                    <Card>
                      <BlockStack gap="300">
                        <BlockStack gap="100">
                          <Text as="h2" variant="headingMd">Match your columns</Text>
                          <Text as="p" tone="subdued">Mia matched what it could. Check each Shopify field uses the right column.</Text>
                        </BlockStack>
                        {!titleMapped && (
                          <Banner tone="warning"><p>Choose the column that holds product titles — every product needs one.</p></Banner>
                        )}
                        <InlineGrid columns={{ xs: 1, sm: 2 }} gap="300">
                          {IMPORT_FIELDS.map((field) => (
                            <Select
                              key={field.key}
                              label={field.label}
                              options={[{ label: "Don't import", value: '' }, ...columns.map((column) => ({ label: column, value: column }))]}
                              value={mapping[field.key] || ''}
                              onChange={(value) => setMapping({ ...mapping, [field.key]: value })}
                            />
                          ))}
                        </InlineGrid>
                      </BlockStack>
                    </Card>

                    <Card>
                      <BlockStack gap="300">
                        <Text as="h2" variant="headingMd">Import options</Text>
                        <FormLayout>
                          <TextField label="Import name" value={config.title} onChange={(value) => setConfig({ ...config, title: value })} autoComplete="off" />
                          <FormLayout.Group>
                            <Select label="If a product already exists" options={DUPLICATE_OPTIONS} value={config.duplicate_action} onChange={(value) => setConfig({ ...config, duplicate_action: value })} />
                            <Select label="Product status" options={STATUS_OPTIONS} value={config.status} onChange={(value) => setConfig({ ...config, status: value })} helpText="Draft products stay hidden until you publish them." />
                          </FormLayout.Group>
                          <FormLayout.Group>
                            <TextField label="Supplier" value={config.supplier} onChange={(value) => setConfig({ ...config, supplier: value })} autoComplete="off" />
                            <TextField label="Category" value={config.category} onChange={(value) => setConfig({ ...config, category: value })} autoComplete="off" />
                          </FormLayout.Group>
                        </FormLayout>
                        <InlineStack align="end">
                          <Button variant="primary" onClick={preview} loading={working === 'preview'} disabled={!titleMapped}>
                            Review import
                          </Button>
                        </InlineStack>
                      </BlockStack>
                    </Card>
                  </>
                )}
              </>
            )}

            {step === 'preview' && (
              <Card>
                <BlockStack gap="400">
                  <Text as="h2" variant="headingMd">Review before importing</Text>
                  <InlineGrid columns={{ xs: 2, md: 4 }} gap="300">
                    <Stat label="Rows" value={summary.total ?? rowCount} />
                    <Stat label="Ready" value={summary.valid} tone="success" />
                    <Stat label="Errors" value={summary.errors} tone={summary.errors ? 'critical' : undefined} />
                    <Stat label="Already in store" value={summary.duplicates} tone={summary.duplicates ? 'caution' : undefined} />
                  </InlineGrid>
                  {!!summary.errors && (
                    <Banner tone="warning" title={`${summary.errors} row${summary.errors === 1 ? '' : 's'} will be skipped`}>
                      <p>Fix them in your spreadsheet and import again, or continue with the rows that are ready.</p>
                    </Banner>
                  )}
                  {problemRows.length > 0 && (
                    <BlockStack gap="100">
                      <Text as="h3" variant="headingSm">Rows that need attention</Text>
                      <List>
                        {problemRows.slice(0, 8).map((detail, index) => (
                          <List.Item key={index}>{`Row ${detail.row ?? '?'}: ${detailText(detail)}`}</List.Item>
                        ))}
                      </List>
                    </BlockStack>
                  )}
                  <Divider />
                  <Text as="h3" variant="headingSm">First rows of your file</Text>
                  <DataTable
                    columnContentTypes={sampleColumns.map(() => 'text')}
                    headings={sampleColumns}
                    rows={rows.map((row) => sampleColumns.map((column) => (row[column] || '—').slice(0, 60)))}
                    truncate
                  />
                  <InlineStack align="end" gap="200">
                    <Button onClick={() => setStep('upload')} disabled={working === 'run'}>Back</Button>
                    <Button variant="primary" onClick={run} loading={working === 'run'} disabled={summary.valid === 0}>
                      {`Import ${summary.valid ?? rowCount} product${(summary.valid ?? rowCount) === 1 ? '' : 's'}`}
                    </Button>
                  </InlineStack>
                </BlockStack>
              </Card>
            )}

            {step === 'results' && (
              <Card>
                <BlockStack gap="400">
                  <Banner tone={summary.failed ? 'warning' : 'success'} title={summary.failed ? 'Import finished with some errors' : 'Import complete'}>
                    <p>{`${(summary.created ?? 0) + (summary.updated ?? 0)} products were added or updated in Shopify.`}</p>
                  </Banner>
                  <InlineGrid columns={{ xs: 2, md: 4 }} gap="300">
                    <Stat label="Created" value={summary.created} tone="success" />
                    <Stat label="Updated" value={summary.updated} />
                    <Stat label="Skipped" value={summary.skipped} />
                    <Stat label="Failed" value={summary.failed} tone={summary.failed ? 'critical' : undefined} />
                  </InlineGrid>
                  {problemRows.length > 0 && (
                    <List>
                      {problemRows.slice(0, 10).map((detail, index) => (
                        <List.Item key={index}>{`Row ${detail.row ?? '?'}: ${detailText(detail)}`}</List.Item>
                      ))}
                    </List>
                  )}
                  <InlineStack align="end" gap="200">
                    <Button url="/products">View products</Button>
                    <Button variant="primary" onClick={reset}>Import another file</Button>
                  </InlineStack>
                </BlockStack>
              </Card>
            )}
          </BlockStack>
        </Layout.Section>

        <Layout.Section variant="oneThird">
          {historyLoading ? (
            <CardSkeleton />
          ) : (
            <Card padding="0">
              <Box padding="400">
                <Text as="h2" variant="headingMd">Recent imports</Text>
              </Box>
              {historyError && (
                <Box padding="400" paddingBlockStart="0">
                  <ErrorBanner message={historyError} onRetry={loadHistory} />
                </Box>
              )}
              {!historyError && history.length === 0 && (
                <Box padding="400" paddingBlockStart="0">
                  <Text as="p" tone="subdued">Your imports will appear here.</Text>
                </Box>
              )}
              {history.map((item) => (
                <Box key={item.id ?? item.created_at} borderBlockStartWidth="025" borderColor="border" padding="400">
                  <BlockStack gap="100">
                    <InlineStack align="space-between" blockAlign="start" gap="200" wrap={false}>
                      <Button variant="plain" textAlign="start" onClick={() => setSelected(item)}>
                        {item.title || 'Untitled import'}
                      </Button>
                      <span className="mia-nowrap">
                        <Badge tone={importTone(item.status)}>{humanize(item.status)}</Badge>
                      </span>
                    </InlineStack>
                    <Text as="span" variant="bodySm" tone="subdued">
                      {`${formatDateTime(item.created_at)}${item.summary ? ` · ${item.summary.created ?? 0} created` : ''}`}
                    </Text>
                  </BlockStack>
                </Box>
              ))}
            </Card>
          )}
        </Layout.Section>
      </Layout>

      <Modal open={!!selected} onClose={() => setSelected(null)} title={selected?.title || 'Import'}>
        <Modal.Section>
          {selected && (
            <BlockStack gap="300">
              <InlineStack gap="200">
                <Badge tone={importTone(selected.status)}>{humanize(selected.status)}</Badge>
                <Text as="span" tone="subdued">{formatDateTime(selected.created_at)}</Text>
              </InlineStack>
              {selected.summary && (
                <InlineGrid columns={4} gap="200">
                  <Stat label="Processed" value={selected.summary.processed} />
                  <Stat label="Created" value={selected.summary.created} />
                  <Stat label="Skipped" value={selected.summary.skipped} />
                  <Stat label="Failed" value={selected.summary.failed} />
                </InlineGrid>
              )}
              {selected.error && <Banner tone="critical"><p>{selected.error}</p></Banner>}
            </BlockStack>
          )}
        </Modal.Section>
      </Modal>
    </Page>
  );
}
