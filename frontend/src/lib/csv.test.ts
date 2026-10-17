import { describe, expect, it } from 'vitest';
import { inferMapping, parseCsv } from './csv';

describe('parseCsv', () => {
  it('handles quoted commas, escaped quotes and multiline fields', () => {
    const csv = 'title,description,price\r\n"Pillow, sage","Soft ""linen""\nInsert included",39.00\r\n';

    const { columns, rows } = parseCsv(csv);

    expect(columns).toEqual(['title', 'description', 'price']);
    expect(rows).toEqual([{ title: 'Pillow, sage', description: 'Soft "linen"\nInsert included', price: '39.00' }]);
  });

  it('strips a UTF-8 byte order mark and skips blank lines', () => {
    const { columns, rows } = parseCsv('﻿title,sku\n\nCandle,CND-1\n');

    expect(columns).toEqual(['title', 'sku']);
    expect(rows).toHaveLength(1);
  });

  it('rejects a file without product rows', () => {
    expect(() => parseCsv('title,sku\n')).toThrow(/at least one product row/);
  });
});

describe('inferMapping', () => {
  it('maps Shopify export headers to product fields', () => {
    const mapping = inferMapping(['Title', 'Body (HTML)', 'Variant SKU', 'Variant Price', 'Image Src']);

    expect(mapping).toMatchObject({
      title: 'Title',
      descriptionHtml: 'Body (HTML)',
      sku: 'Variant SKU',
      price: 'Variant Price',
      image: 'Image Src',
    });
  });

  it('leaves unknown columns unmapped', () => {
    expect(inferMapping(['Colour'])).toEqual({});
  });
});
