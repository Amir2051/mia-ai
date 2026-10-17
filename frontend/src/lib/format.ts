import type { BadgeProps } from '@shopify/polaris';

type Money = { amount?: string | number | null; currencyCode?: string | null } | null | undefined;

export function formatMoney(value: string | number | null | undefined, currency = 'USD'): string {
  if (value === null || value === undefined || value === '') return '—';
  const amount = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(amount)) return '—';
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}

/** Price without a currency symbol, for fields Shopify returns without one. */
export function formatPrice(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const amount = Number(value);
  if (!Number.isFinite(amount)) return String(value);
  return new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount);
}

export function formatShopMoney(set: { shopMoney?: Money } | null | undefined, fallbackCurrency = 'USD'): string {
  const money = set?.shopMoney;
  return formatMoney(money?.amount ?? null, money?.currencyCode || fallbackCurrency);
}

export function formatDate(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

export function formatDateTime(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export function formatNumber(value?: number | string | null): string {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat().format(number) : String(value);
}

/** "PARTIALLY_FULFILLED" -> "Partially fulfilled" */
export function humanize(value?: string | null): string {
  if (!value) return '—';
  const text = value.replace(/[_-]+/g, ' ').toLowerCase().trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Last path segment of a Shopify gid, e.g. gid://shopify/Order/123 -> 123 */
export function gidTail(gid?: string | null): string {
  if (!gid) return '';
  const parts = gid.split('/');
  return parts[parts.length - 1] || gid;
}

/** Path segment safe for React Router routes that hold a Shopify gid. */
export function idPath(base: string, gid: string): string {
  return `${base}/${encodeURIComponent(gid)}`;
}

type Tone = BadgeProps['tone'];

const PRODUCT_TONES: Record<string, Tone> = { ACTIVE: 'success', DRAFT: 'info', ARCHIVED: undefined };
const FINANCIAL_TONES: Record<string, Tone> = {
  PAID: undefined,
  PENDING: 'warning',
  AUTHORIZED: 'warning',
  PARTIALLY_PAID: 'warning',
  REFUNDED: undefined,
  PARTIALLY_REFUNDED: undefined,
  VOIDED: undefined,
  EXPIRED: 'critical',
};
const FULFILLMENT_TONES: Record<string, Tone> = {
  FULFILLED: undefined,
  UNFULFILLED: 'attention',
  PARTIALLY_FULFILLED: 'warning',
  ON_HOLD: 'warning',
  IN_PROGRESS: 'info',
  SCHEDULED: 'info',
};
const IMPORT_TONES: Record<string, Tone> = {
  COMPLETED: 'success',
  VALIDATED: 'info',
  PENDING: 'attention',
  COMPLETED_WITH_ERRORS: 'warning',
  FAILED: 'critical',
};

export function productTone(status?: string | null): Tone {
  return PRODUCT_TONES[(status || '').toUpperCase()];
}

export function financialTone(status?: string | null): Tone {
  return FINANCIAL_TONES[(status || '').toUpperCase()];
}

export function fulfillmentTone(status?: string | null): Tone {
  return FULFILLMENT_TONES[(status || '').toUpperCase()];
}

export function importTone(status?: string | null): Tone {
  return IMPORT_TONES[(status || '').toUpperCase()];
}

export function errorMessage(error: unknown, fallback: string): string {
  const err = error as { response?: { data?: { detail?: unknown } }; message?: unknown };
  const detail = err?.response?.data?.detail ?? err?.message;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail) return JSON.stringify(detail);
  return fallback;
}
