// @vitest-environment jsdom
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AppProvider } from '@shopify/polaris';

vi.mock('../services/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('../lib/session', () => ({
  useSession: () => ({ loading: false, connected: true, shopDomain: 'demo.myshopify.com', scopes: null, error: null, refresh: vi.fn() }),
}));
vi.mock('../lib/toast', () => ({ showToast: vi.fn() }));

import api from '../services/api';
import Publishing from './Publishing';

const mockedApi = api as unknown as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> };

type Action = Record<string, any>;

function fb(overrides: Action = {}): Action {
  return {
    id: 1,
    action_type: 'meta_facebook',
    status: 'pending_approval',
    idempotency_key: 'req-abcd1234',
    payload: { account_id: 7, message: 'Autumn linen sale is live' },
    result: null,
    error: null,
    attempt_count: 0,
    created_at: '2026-10-24T12:00:00Z',
    approved_at: null,
    executed_at: null,
    ...overrides,
  };
}

function serveList(actions: Action[]) {
  mockedApi.get.mockImplementation((url: string) => {
    if (url === '/social/accounts') {
      return Promise.resolve({ data: { connected: true, accounts: [{ id: 7, account_type: 'facebook_page', account_name: 'Demo Page' }] } });
    }
    const m = /\/publishing\/requests\/(\d+)$/.exec(url);
    if (m) {
      const one = actions.find((a) => String(a.id) === m[1]);
      return Promise.resolve({ data: { action: one } });
    }
    return Promise.resolve({ data: { actions } });
  });
}

function renderPage() {
  return render(
    <AppProvider i18n={{}}>
      <Publishing />
    </AppProvider>,
  );
}

beforeAll(() => {
  if (!window.matchMedia) {
    window.matchMedia = (query: string) => ({ matches: false, media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } });
  }
  globalThis.ResizeObserver = globalThis.ResizeObserver || class { observe() {} unobserve() {} disconnect() {} };
  if (!window.scrollTo) window.scrollTo = () => {};
});

beforeEach(() => {
  mockedApi.get.mockReset();
  mockedApi.post.mockReset();
});

afterEach(() => cleanup());

describe('Publishing workflow', () => {
  it('loads the queue and shows an empty state', async () => {
    serveList([]);
    renderPage();
    expect(await screen.findByText(/No publishing requests yet/i)).toBeTruthy();
  });

  it('shows an error with retry when the queue fails to load', async () => {
    mockedApi.get.mockRejectedValueOnce({ response: { data: { detail: 'Session expired' } } });
    renderPage();
    expect(await screen.findByText('Session expired')).toBeTruthy();
    serveList([]);
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    expect(await screen.findByText(/No publishing requests yet/i)).toBeTruthy();
  });

  it('creates a request without publishing', async () => {
    serveList([]);
    mockedApi.post.mockResolvedValueOnce({ data: { created: true, action: fb() } });
    renderPage();
    await screen.findByText(/No publishing requests yet/i);
    fireEvent.change(screen.getByLabelText('Destination'), { target: { value: 'meta_facebook' } });
    fireEvent.change(screen.getByLabelText('Facebook Page'), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Autumn linen sale is live' } });
    fireEvent.click(screen.getByRole('button', { name: /create request/i }));
    await waitFor(() => expect(mockedApi.post).toHaveBeenCalledTimes(1));
    const [url, body] = mockedApi.post.mock.calls[0];
    expect(url).toBe('/publishing/requests');
    expect(body.action_type).toBe('meta_facebook');
    expect(body.payload).toEqual({ account_id: 7, message: 'Autumn linen sale is live' });
    // Creating must never call approve/execute.
    expect(mockedApi.post.mock.calls.every((call: any[]) => !/approve|execute/.test(call[0]))).toBe(true);
  });

  it('approves a pending request through the server', async () => {
    serveList([fb()]);
    mockedApi.post.mockResolvedValueOnce({ data: { action: fb({ status: 'approved', approved_at: '2026-10-24T12:05:00Z' }) } });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /review request 1/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^approve$/i }));
    await waitFor(() => expect(mockedApi.post).toHaveBeenCalledWith('/publishing/requests/1/approve'));
  });

  it('does not allow execution until a request is approved', async () => {
    serveList([fb({ status: 'pending_approval' })]);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /review request 1/i }));
    const execBtn = await screen.findByRole('button', { name: /execute \(publish now\)/i });
    const blocked = (execBtn as HTMLButtonElement).disabled || execBtn.getAttribute('aria-disabled') === 'true';
    expect(blocked).toBe(true);
    fireEvent.click(execBtn);
    expect(mockedApi.post).not.toHaveBeenCalled();
  });

  it('executes an approved request and reflects the published status', async () => {
    serveList([fb({ status: 'approved' })]);
    mockedApi.post.mockResolvedValueOnce({ data: { action: fb({ status: 'published', executed_at: '2026-10-24T12:10:00Z', result: { provider: 'meta_facebook', external_id: 'page_99' } }) } });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /review request 1/i }));
    fireEvent.click(await screen.findByRole('button', { name: /execute \(publish now\)/i }));
    await waitFor(() => expect(mockedApi.post).toHaveBeenCalledWith('/publishing/requests/1/execute'));
    expect((await screen.findAllByText('Published')).length).toBeGreaterThan(0);
  });

  it('handles needs_review without automatically retrying', async () => {
    const actions = [fb({ status: 'approved' })];
    serveList(actions);
    mockedApi.post.mockImplementation((url: string) => {
      if (/execute/.test(url)) {
        actions[0] = fb({ status: 'needs_review', error: 'Provider outcome could not be confirmed; do not retry automatically' });
        return Promise.reject({ response: { status: 502, data: { detail: 'Provider outcome could not be confirmed; do not retry automatically' } } });
      }
      return Promise.resolve({ data: {} });
    });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /review request 1/i }));
    fireEvent.click(await screen.findByRole('button', { name: /execute \(publish now\)/i }));
    await waitFor(() => expect(screen.getByText(/Provider outcome not confirmed/i)).toBeTruthy());
    // exactly one execute POST; no automatic retry
    const execCalls = mockedApi.post.mock.calls.filter((call: any[]) => /execute/.test(call[0]));
    expect(execCalls).toHaveLength(1);
  });

  it('prevents duplicate execute submissions from rapid clicks', async () => {
    serveList([fb({ status: 'approved' })]);
    mockedApi.post.mockReturnValue(new Promise(() => {})); // never resolves: keeps action in-flight
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /review request 1/i }));
    const execBtn = await screen.findByRole('button', { name: /execute \(publish now\)/i });
    fireEvent.click(execBtn);
    fireEvent.click(execBtn);
    fireEvent.click(execBtn);
    await waitFor(() => expect(mockedApi.post).toHaveBeenCalledTimes(1));
  });

  it('surfaces API errors when creating a request', async () => {
    serveList([]);
    mockedApi.post.mockRejectedValueOnce({ response: { data: { detail: 'Missing Shopify session' } } });
    renderPage();
    await screen.findByText(/No publishing requests yet/i);
    fireEvent.change(screen.getByLabelText('Destination'), { target: { value: 'meta_facebook' } });
    fireEvent.change(screen.getByLabelText('Facebook Page'), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Hello' } });
    fireEvent.click(screen.getByRole('button', { name: /create request/i }));
    expect(await screen.findByText('Missing Shopify session')).toBeTruthy();
  });

  it('shows the exact submitted content and destination in the review', async () => {
    serveList([fb({ payload: { account_id: 42, message: 'Exact review content ABC' } })]);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /review request 1/i }));
    const heading = await screen.findByText(/Review request #1/i);
    expect(heading).toBeTruthy();
    expect(await screen.findByText('Exact review content ABC')).toBeTruthy();
    expect(screen.getAllByText(/account #42/i).length).toBeGreaterThan(0);
  });
});
