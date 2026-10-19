import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import api from '../services/api';

export type Session = {
  loading: boolean;
  connected: boolean;
  shopDomain: string | null;
  scopes: string | null;
  error: string | null;
  refresh: () => void;
};

const SessionContext = createContext<Session | null>(null);

/** Loads /auth/session once for the whole app instead of once per page. */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Omit<Session, 'refresh'>>({
    loading: true,
    connected: false,
    shopDomain: null,
    scopes: null,
    error: null,
  });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const loadSession = async () => {
      try {
        const embedded = window.self !== window.top;
        let authorization: string | undefined;
        if (embedded) {
          if (!window.shopify?.idToken) {
            throw new Error('Shopify App Bridge did not initialize. Close Mia and reopen it from Shopify Admin → Apps.');
          }
          let token: string;
          try {
            token = await window.shopify.idToken();
          } catch {
            throw new Error('Shopify could not provide a session token. Reopen Mia from Shopify Admin → Apps and try again.');
          }
          if (!token) {
            throw new Error('Shopify did not provide a session token. Reopen Mia from Shopify Admin → Apps and try again.');
          }
          authorization = `Bearer ${token}`;
        }

        const res = await api.get('/auth/session', {
          headers: authorization ? { Authorization: authorization } : undefined,
        });
        if (cancelled) return;
        setState({
          loading: false,
          connected: !!res.data?.connected,
          shopDomain: res.data?.shop_domain ?? null,
          scopes: res.data?.scopes ?? null,
          error: null,
        });
      } catch (err) {
        if (cancelled) return;
        setState({
          loading: false,
          connected: false,
          shopDomain: null,
          scopes: null,
          error: (err as Error)?.message || 'Could not load your Shopify session',
        });
      }
    };
    void loadSession();
    return () => {
      cancelled = true;
    };
  }, [version]);

  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  return <SessionContext.Provider value={{ ...state, refresh }}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error('useSession must be used inside SessionProvider');
  return session;
}
