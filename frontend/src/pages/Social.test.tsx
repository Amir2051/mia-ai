// @vitest-environment jsdom
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AppProvider } from '@shopify/polaris';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../services/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('../lib/session', () => ({
  useSession: () => ({
    loading: false,
    connected: true,
    shopDomain: 'demo.myshopify.com',
    scopes: null,
    error: null,
    refresh: vi.fn(),
  }),
}));
vi.mock('../lib/toast', () => ({ showToast: vi.fn() }));

import api from '../services/api';
import Social from './Social';

const mockedApi = api as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
};

function renderPage() {
  return render(
    <MemoryRouter>
      <AppProvider i18n={{}}>
        <Social />
      </AppProvider>
    </MemoryRouter>,
  );
}

beforeAll(() => {
  if (!window.matchMedia) {
    window.matchMedia = (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() { return false; },
    });
  }
  globalThis.ResizeObserver = globalThis.ResizeObserver || class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  if (!window.scrollTo) window.scrollTo = () => {};
});

beforeEach(() => {
  mockedApi.get.mockReset();
  mockedApi.post.mockReset();
  mockedApi.get.mockImplementation((url: string) => {
    if (url === '/social/accounts') {
      return Promise.resolve({ data: { connected: true, accounts: [] } });
    }
    if (url === '/social/configuration') {
      return Promise.resolve({
        data: {
          platforms: [{
            provider: 'meta',
            label: 'Meta',
            configured: false,
            redirect_uri: 'https://mia.drivenest.info/api/social/meta/callback',
            required_env: ['META_APP_ID', 'META_APP_SECRET'],
          }],
        },
      });
    }
    return Promise.resolve({ data: {} });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Social Meta connection fallback', () => {
  it('uses the configured server test token when Meta OAuth credentials are not configured', async () => {
    const popup = {
      close: vi.fn(),
      location: { href: '' },
    };
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    mockedApi.post.mockResolvedValueOnce({ data: { pages_saved: 1 } });

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Connect Meta' }));

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/social/meta/test-connect');
    });

    expect(popup.close).toHaveBeenCalledTimes(1);
    expect(
      mockedApi.get.mock.calls.some(([url]) => url === '/social/meta/connect'),
    ).toBe(false);
  });
});
