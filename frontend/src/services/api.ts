import axios from 'axios';

declare global {
  interface Window {
    shopify?: {
      idToken: () => Promise<string>;
    };
  }
}

const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
});

api.interceptors.request.use(async (config) => {
  try {
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

    const config = (error as { config?: { _miaRetried?: boolean } }).config;
    const retryableShopifySession =
      (error as { response?: { status?: number; headers?: Record<string, string> } }).response?.status === 401 &&
      !config?._miaRetried;

    if (retryableShopifySession && window.shopify?.idToken) {
      config!._miaRetried = true;
      return window.shopify.idToken().then((token) => {
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
