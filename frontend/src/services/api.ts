import axios from 'axios';

type MiaRequestConfig = { _miaRetried?: boolean; _miaSkipSessionRetry?: boolean };

const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
});

api.interceptors.request.use(async (config) => {
  try {
    // App Bridge ID tokens are short-lived. Always replace any carried-over
    // Authorization header with a freshly requested token before sending API
    // traffic; never let a previous request's token age in Axios config.
    if (window.shopify?.idToken) {
      const token = await window.shopify.idToken();

      if (token) {
        config.headers = config.headers ?? {};
        config.headers.Authorization = `Bearer ${token}`;
      }
    }
  } catch (error) {
    console.error('Shopify ID token error:', error);
  }

  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    const axiosError = error as {
      response?: {
        data?: {
          detail?: string;
        };
      };
      message?: string;
    };

    const rawDetail =
      axiosError.response?.data?.detail ||
      axiosError.message ||
      'Request failed';

    const detail =
      typeof rawDetail === 'string' ? rawDetail : JSON.stringify(rawDetail);

    const config = (error as { config?: MiaRequestConfig & { url?: string } }).config;
    const isSessionEndpoint = /\/auth\/session(?:\?|$)/.test(config?.url || '');
    // Token decryption failures are returned as 500s, not 401s. A forced
    // Shopify token exchange can replace stale ciphertext with a fresh token
    // encrypted by the currently configured key, then safely retry once.
    const isStoredTokenDecryptionFailure =
      /Stored Shopify access token could not be decrypted|Unable to decrypt Shopify access token/i.test(detail);
    const responseStatus = (error as { response?: { status?: number } }).response?.status;
    const canRetryExpiredSession =
      responseStatus === 401 &&
      isSessionEndpoint &&
      !config?._miaRetried &&
      !config?._miaSkipSessionRetry;

    // If the first session request races token expiry, retry that same endpoint
    // once. The request interceptor obtains a fresh App Bridge ID token.
    if (canRetryExpiredSession) {
      config!._miaRetried = true;
      return api.request(config as any);
    }

    const retryableShopifySession =
      (responseStatus === 401 || (responseStatus === 500 && isStoredTokenDecryptionFailure)) &&
      !config?._miaRetried &&
      !config?._miaSkipSessionRetry &&
      !isSessionEndpoint;

    if (retryableShopifySession && window.shopify?.idToken) {
      config!._miaRetried = true;
      return new Promise<string>((resolve, reject) => {
        let attempt = 0;
        const refresh = async () => {
          try {
            const token = await window.shopify!.idToken();
            if (token) {
              try {
                await api.get('/auth/session?force=true', {
                  headers: { Authorization: `Bearer ${token}` },
                  // A failed token exchange must not recursively start another retry cycle.
                  _miaSkipSessionRetry: true,
                } as any);
              } catch (sessionError) {
                return reject(sessionError);
              }
              return resolve(token);
            }
          } catch (refreshError) {
            return reject(refreshError);
          }
          attempt += 1;
          setTimeout(refresh, Math.min(250 * attempt, 1000));
        };
        refresh();
      }).then((token) => {
        if (token) {
          const original = error as { config?: { headers?: Record<string, string> } };
          original.config = original.config ?? {};
          original.config.headers = original.config.headers ?? {};
          original.config.headers.Authorization = `Bearer ${token}`;
        }
        return api.request((error as { config: any }).config);
      });
    }

    if (axiosError instanceof Error) {
      Object.defineProperty(axiosError, 'message', {
        value: detail,
        writable: true,
        configurable: true,
      });
    } else {
      const rejected = new Error(detail);
      Object.assign(rejected, axiosError);
      return Promise.reject(rejected);
    }

    return Promise.reject(axiosError);
  }
);

export default api;
export function postFormData(url: string, formData: FormData) {
  return api.post(url, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
}
