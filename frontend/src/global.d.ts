import type { DetailedHTMLProps, HTMLAttributes } from 'react';

declare global {
  interface Window {
    shopify?: {
      idToken: () => Promise<string>;
      toast?: {
        show: (message: string, options?: { duration?: number; isError?: boolean }) => void;
      };
    };
  }

  namespace JSX {
    interface IntrinsicElements {
      // App Bridge renders this menu in the Shopify admin sidebar.
      'ui-nav-menu': DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement>;
    }
  }
}

export {};
