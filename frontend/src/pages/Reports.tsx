import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  BlockStack,
  Box,
  Button,
  Card,
  Divider,
  InlineGrid,
  InlineStack,
  Layout,
  Modal,
  Page,
  Select,
  Text,
  TextField,
} from '@shopify/polaris';
import api from '../services/api';
import { ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, formatDateTime, formatMoney, formatNumber, humanize } from '../lib/format';
import { showToast } from '../lib/toast';
import { useSession } from '../lib/session';

type ReportResult = {
  report_name?: string;
  frequency?: string;
  generated_at?: string;
  currency?: string | null;
  warnings?: string[];
  comparison?: {
    period_days?: number;
    current?: { orders?: number; net_sales?: number; average_order_value?: number };
    previous?: { orders?: number; net_sales?: number; average_order_value?: number };
    change_percent?: { net_sales?: number | null; orders?: number | null; average_order_value?: number | null };
  };
};

type Schedule = {
  id: number;
  name: string;
  report_type: string;
  frequency: 'daily' | 'weekly' | 'monthly';
  schedule_time: string;
  timezone: string;
  day_of_week?: number | null;
  day_of_month?: number | null;
  enabled: boolean;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_status: string;
  last_error?: string | null;
  last_result?: ReportResult | null;
  delivery?: string;
};

type HistoryItem = {
  id: number;
  action: string;
  entity_id?: string | null;
  created_at?: string | null;
  details: Record<string, unknown>;
};

const frequencyOptions = [
  { label: 'Daily', value: 'daily' },
  { label: 'Weekly', value: 'weekly' },
  { label: 'Monthly', value: 'monthly' },
];
const weekdayOptions = [
  { label: 'Monday', value: '0' },
  { label: 'Tuesday', value: '1' },
  { label: 'Wednesday', value: '2' },
  { label: 'Thursday', value: '3' },
  { label: 'Friday', value: '4' },
  { label: 'Saturday', value: '5' },
  { label: 'Sunday', value: '6' },
];
const monthdayOptions = Array.from({ length: 31 }, (_, index) => ({
  label: 'Day ' + (index + 1),
  value: String(index + 1),
}));

function statusTone(status: string): 'success' | 'critical' | 'info' | 'warning' {
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'critical';
  if (status === 'running') return 'info';
  return 'warning';
}

function displayUtc(value?: string | null, timeZone?: string): string {
  if (!value) return '—';
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : value + 'Z';
  if (timeZone) {
    try {
      return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZone }).format(new Date(normalized));
    } catch {
      // Fall back to the browser's local formatting if a saved timezone is invalid.
    }
  }
  return formatDateTime(normalized);
}

function changeText(value: number | null | undefined): string {
  if (value == null) return 'No baseline';
  return (value > 0 ? '+' : '') + value.toFixed(1) + '%';
}

function reportMoney(value: number | null | undefined, currency?: string | null): string {
  if (value == null) return '—';
  return currency ? formatMoney(value, currency) : formatNumber(value) + ' (currency unavailable)';
}

export default function Reports() {
  const session = useSession();
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Schedule | null>(null);
  const [name, setName] = useState('Weekly sales summary');
  const [frequency, setFrequency] = useState<'daily' | 'weekly' | 'monthly'>('weekly');
  const [scheduleTime, setScheduleTime] = useState('09:00');
  const [timezone, setTimezone] = useState('UTC');
  const [dayOfWeek, setDayOfWeek] = useState('0');
  const [dayOfMonth, setDayOfMonth] = useState('1');

  const refresh = useCallback(async () => {
    const [scheduleResult, historyResult] = await Promise.allSettled([
      api.get('/reports/schedules'),
      api.get('/reports/history?limit=20'),
    ]);
    if (scheduleResult.status === 'fulfilled') {
      setSchedules(scheduleResult.value.data?.items ?? []);
    } else {
      setError(errorMessage(scheduleResult.reason, 'Could not load scheduled reports'));
    }
    if (historyResult.status === 'fulfilled') {
      setHistory(historyResult.value.data?.items ?? []);
    } else {
      setError((current) => current ?? errorMessage(historyResult.reason, 'Could not load report history'));
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!session.connected) return;
    void refresh();
    api.get('/settings/health').then((response) => {
      const storeTimezone = response.data?.shopify?.timezone;
      if (typeof storeTimezone === 'string' && storeTimezone.trim()) setTimezone(storeTimezone);
    }).catch(() => {
      // UTC remains a safe, explicit fallback when the store timezone is unavailable.
    });
  }, [session.connected, refresh]);

  async function createSchedule() {
    setSaving(true);
    setError(null);
    try {
      await api.post('/reports/schedules', {
        name,
        report_type: 'sales_summary',
        frequency,
        schedule_time: scheduleTime,
        timezone,
        day_of_week: frequency === 'weekly' ? Number(dayOfWeek) : null,
        day_of_month: frequency === 'monthly' ? Number(dayOfMonth) : null,
      });
      showToast('Scheduled report created');
      await refresh();
    } catch (err) {
      setError(errorMessage(err, 'Could not create the scheduled report'));
    } finally {
      setSaving(false);
    }
  }

  async function runNow(schedule: Schedule) {
    setBusyId(schedule.id);
    setError(null);
    try {
      await api.post('/reports/schedules/' + schedule.id + '/run-now');
      showToast('Report generated and saved');
      await refresh();
    } catch (err) {
      setError(errorMessage(err, 'Report generation failed'));
      await refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function toggleSchedule(schedule: Schedule) {
    setBusyId(schedule.id);
    setError(null);
    try {
      await api.patch('/reports/schedules/' + schedule.id, { enabled: !schedule.enabled });
      showToast(schedule.enabled ? 'Schedule paused' : 'Schedule resumed');
      await refresh();
    } catch (err) {
      setError(errorMessage(err, 'Could not update the schedule'));
    } finally {
      setBusyId(null);
    }
  }

  async function deleteSchedule() {
    if (!deleteTarget) return;
    setBusyId(deleteTarget.id);
    setError(null);
    try {
      await api.delete('/reports/schedules/' + deleteTarget.id);
      showToast('Scheduled report deleted');
      setDeleteTarget(null);
      await refresh();
    } catch (err) {
      setError(errorMessage(err, 'Could not delete the schedule'));
    } finally {
      setBusyId(null);
    }
  }

  if (session.loading || (session.connected && loading)) return <PageSkeleton title="Scheduled reports" />;
  if (!session.connected) return <NotConnected title="Scheduled reports" error={session.error} />;

  return (
    <Page title="Scheduled reports" subtitle="Automate sales summaries and review every run in MIA. Reports are saved in-app; email delivery is not configured.">
      <Layout>
        <Layout.Section>
          <BlockStack gap="400">
            {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

            <Card>
              <BlockStack gap="300">
                <Text as="h2" variant="headingMd">Create a sales report schedule</Text>
                <Text as="p" tone="subdued">
                  Daily compares the last 24 hours with the prior 24 hours, weekly compares 7-day periods, and monthly compares 30-day periods. Reports use order data available to this Shopify app.
                </Text>
                <TextField label="Report name" value={name} onChange={setName} autoComplete="off" maxLength={120} />
                <InlineGrid columns={{ xs: 1, md: 2 }} gap="300">
                  <Select label="Frequency" options={frequencyOptions} value={frequency} onChange={(value) => setFrequency(value as typeof frequency)} />
                  <TextField label="Run time (24-hour)" value={scheduleTime} onChange={setScheduleTime} autoComplete="off" helpText="Example: 09:00" />
                  <TextField label="Timezone" value={timezone} onChange={setTimezone} autoComplete="off" helpText="Prefilled from Shopify when available; must be an IANA timezone." />
                  {frequency === 'weekly' && <Select label="Run on" options={weekdayOptions} value={dayOfWeek} onChange={setDayOfWeek} />}
                  {frequency === 'monthly' && <Select label="Day of month" options={monthdayOptions} value={dayOfMonth} onChange={setDayOfMonth} />}
                </InlineGrid>
                <InlineStack>
                  <Button variant="primary" onClick={() => void createSchedule()} loading={saving} disabled={!name.trim() || !scheduleTime.trim() || !timezone.trim()}>
                    Create schedule
                  </Button>
                </InlineStack>
              </BlockStack>
            </Card>

            <Card>
              <BlockStack gap="300">
                <InlineStack align="space-between" blockAlign="center">
                  <Text as="h2" variant="headingMd">Your schedules</Text>
                  <Button variant="tertiary" onClick={() => void refresh()}>Refresh</Button>
                </InlineStack>
                {schedules.length === 0 ? (
                  <Text as="p" tone="subdued">No scheduled reports yet. Create one above; the background worker checks due schedules every 30 seconds.</Text>
                ) : schedules.map((schedule) => {
                  const comparison = schedule.last_result?.comparison;
                  const currency = schedule.last_result?.currency;
                  return (
                    <Box key={schedule.id} paddingBlock="200">
                      <BlockStack gap="200">
                        <InlineStack align="space-between" blockAlign="start" gap="300" wrap>
                          <BlockStack gap="100">
                            <Text as="h3" variant="headingSm">{schedule.name}</Text>
                            <Text as="p" variant="bodySm" tone="subdued">
                              {humanize(schedule.frequency)} · {schedule.schedule_time} {schedule.timezone}
                              {schedule.frequency === 'weekly' ? ' · ' + (weekdayOptions.find((day) => day.value === String(schedule.day_of_week ?? 0))?.label ?? 'Monday') : ''}
                              {schedule.frequency === 'monthly' ? ' · Day ' + (schedule.day_of_month ?? 1) : ''}
                            </Text>
                            <Text as="p" variant="bodySm">Next run: {displayUtc(schedule.next_run_at, schedule.timezone)}</Text>
                            <Text as="p" variant="bodySm" tone="subdued">
                              Last run: {displayUtc(schedule.last_run_at, schedule.timezone)} · Delivery: in-app
                            </Text>
                          </BlockStack>
                          <InlineStack gap="200" blockAlign="center" wrap>
                            <Badge tone={schedule.enabled ? statusTone(schedule.last_status) : 'warning'}>{schedule.enabled ? humanize(schedule.last_status) : 'Paused'}</Badge>
                            <Button onClick={() => void runNow(schedule)} loading={busyId === schedule.id} disabled={busyId !== null}>Run now</Button>
                            <Button variant="tertiary" onClick={() => void toggleSchedule(schedule)} loading={busyId === schedule.id} disabled={busyId !== null}>
                              {schedule.enabled ? 'Pause' : 'Resume'}
                            </Button>
                            <Button variant="plain" tone="critical" onClick={() => setDeleteTarget(schedule)} disabled={busyId !== null}>Delete</Button>
                          </InlineStack>
                        </InlineStack>
                        {schedule.last_error && <Text as="p" tone="critical">{schedule.last_error}</Text>}
                        {comparison && (
                          <InlineGrid columns={{ xs: 1, md: 3 }} gap="200">
                            <Box borderColor="border" borderWidth="025" borderRadius="200" padding="200">
                              <Text as="p" variant="bodySm" tone="subdued">Net sales</Text>
                              <Text as="p" variant="headingMd">{reportMoney(comparison.current?.net_sales, currency)}</Text>
                              <Text as="p" variant="bodySm">Change: {changeText(comparison.change_percent?.net_sales)}</Text>
                            </Box>
                            <Box borderColor="border" borderWidth="025" borderRadius="200" padding="200">
                              <Text as="p" variant="bodySm" tone="subdued">Orders</Text>
                              <Text as="p" variant="headingMd">{formatNumber(comparison.current?.orders)}</Text>
                              <Text as="p" variant="bodySm">Change: {changeText(comparison.change_percent?.orders)}</Text>
                            </Box>
                            <Box borderColor="border" borderWidth="025" borderRadius="200" padding="200">
                              <Text as="p" variant="bodySm" tone="subdued">Average order value</Text>
                              <Text as="p" variant="headingMd">{reportMoney(comparison.current?.average_order_value, currency)}</Text>
                              <Text as="p" variant="bodySm">Change: {changeText(comparison.change_percent?.average_order_value)}</Text>
                            </Box>
                          </InlineGrid>
                        )}
                        {!!schedule.last_result?.warnings?.length && (
                          <Text as="p" tone="subdued" variant="bodySm">Coverage notes: {schedule.last_result.warnings.join(' ')}</Text>
                        )}
                      </BlockStack>
                      <Divider />
                    </Box>
                  );
                })}
              </BlockStack>
            </Card>

            <Card>
              <BlockStack gap="300">
                <Text as="h2" variant="headingMd">Report run history</Text>
                {history.length === 0 ? (
                  <Text as="p" tone="subdued">Completed and failed scheduled report runs will appear here.</Text>
                ) : history.map((item) => {
                  const details = item.details ?? {};
                  const failed = item.action.endsWith('.failed');
                  return (
                    <Box key={item.id} paddingBlock="200">
                      <InlineStack align="space-between" blockAlign="start" gap="300">
                        <BlockStack gap="100">
                          <Text as="p" fontWeight="medium">{String(details.report_name ?? ('Scheduled report ' + (item.entity_id ?? '')))}</Text>
                          <Text as="p" variant="bodySm" tone="subdued">
                            {displayUtc(item.created_at)} · {humanize(String(details.frequency ?? 'report'))}
                          </Text>
                          {typeof details.error === 'string' && details.error.length > 0 && <Text as="p" tone="critical">{details.error}</Text>}
                          {Array.isArray(details.warnings) && details.warnings.length > 0 && (
                            <Text as="p" variant="bodySm" tone="subdued">{details.warnings.map(String).join(' ')}</Text>
                          )}
                        </BlockStack>
                        <BlockStack gap="100" align="end">
                          <Badge tone={failed ? 'critical' : 'success'}>{failed ? 'Failed' : 'Completed'}</Badge>
                          {details.net_sales != null && <Text as="p" variant="bodySm">{reportMoney(Number(details.net_sales), typeof details.currency === 'string' ? details.currency : null)}</Text>}
                          {details.orders != null && <Text as="p" variant="bodySm">{formatNumber(Number(details.orders))} orders</Text>}
                          {details.change_percent != null && <Text as="p" variant="bodySm">Sales change: {changeText(Number(details.change_percent))}</Text>}
                        </BlockStack>
                      </InlineStack>
                    </Box>
                  );
                })}
              </BlockStack>
            </Card>
          </BlockStack>
        </Layout.Section>
      </Layout>

      <Modal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Delete scheduled report?"
        primaryAction={{ content: 'Delete schedule', destructive: true, onAction: () => void deleteSchedule(), loading: busyId !== null }}
        secondaryActions={[{ content: 'Cancel', onAction: () => setDeleteTarget(null) }]}
      >
        <Modal.Section>
          <Text as="p">Delete {deleteTarget?.name ?? 'this schedule'}? Existing run history will remain available.</Text>
        </Modal.Section>
      </Modal>
    </Page>
  );
}
