// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import SafeHtml from './SafeHtml';

describe('SafeHtml', () => {
  it('keeps formatting but strips scripts and event handlers from AI HTML', () => {
    const { container } = render(
      <SafeHtml html={'<p>Soft <strong>linen</strong></p><script>alert(1)</script><a href="javascript:alert(2)" onclick="x()">x</a>'} />,
    );

    expect(container.querySelector('strong')?.textContent).toBe('linen');
    expect(container.querySelector('script')).toBeNull();
    const link = container.querySelector('a');
    expect(link?.getAttribute('onclick')).toBeNull();
    expect(link?.getAttribute('href') ?? '').not.toMatch(/javascript:/i);
  });

  it('drops images that are not web URLs and keeps https images', () => {
    const { container } = render(
      <SafeHtml html={'<img src=x onerror="alert(1)"><img src="https://cdn.shopify.com/p.png" alt="Pillow">'} />,
    );

    const images = container.querySelectorAll('img');
    expect(images).toHaveLength(1);
    expect(images[0].getAttribute('src')).toBe('https://cdn.shopify.com/p.png');
    expect(images[0].getAttribute('onerror')).toBeNull();
  });
});
