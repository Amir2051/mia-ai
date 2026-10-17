import DOMPurify from 'dompurify';
import { useMemo } from 'react';

// Drop images that don't point at a web address (e.g. src="x"), which would
// otherwise render as broken-image icons after their handlers are stripped.
DOMPurify.addHook('uponSanitizeElement', (node, data) => {
  if (data.tagName === 'img') {
    const src = (node as Element).getAttribute?.('src') || '';
    if (!/^https:\/\//i.test(src)) node.parentNode?.removeChild(node);
  }
});

/**
 * Renders product or AI-generated HTML with scripts, event handlers and
 * other active content stripped. Never pass untrusted HTML to
 * dangerouslySetInnerHTML directly.
 */
export default function SafeHtml({ html, className = 'mia-prose' }: { html?: string | null; className?: string }) {
  const clean = useMemo(
    () =>
      DOMPurify.sanitize(html || '', {
        USE_PROFILES: { html: true },
        FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe'],
        FORBID_ATTR: ['style'],
      }),
    [html],
  );
  return <div className={className} dangerouslySetInnerHTML={{ __html: clean }} />;
}
