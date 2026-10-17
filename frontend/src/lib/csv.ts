export type ParsedCsv = { columns: string[]; rows: Array<Record<string, string>> };

/**
 * RFC 4180-style parser: quoted commas, escaped quotes and newlines inside
 * quoted fields, so the browser preview matches the server's Python parser.
 */
export function parseCsv(content: string): ParsedCsv {
  const records: string[][] = [];
  let record: string[] = [];
  let value = '';
  let quoted = false;
  const text = content.replace(/^﻿/, '');

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];

    if (character === '"') {
      if (quoted && text[index + 1] === '"') {
        value += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
      continue;
    }

    if (character === ',' && !quoted) {
      record.push(value.trim());
      value = '';
      continue;
    }

    if ((character === '\n' || character === '\r') && !quoted) {
      if (character === '\r' && text[index + 1] === '\n') index += 1;
      record.push(value.trim());
      value = '';
      if (record.some((field) => field.length > 0)) records.push(record);
      record = [];
      continue;
    }

    value += character;
  }

  if (value.length > 0 || record.length > 0) {
    record.push(value.trim());
    if (record.some((field) => field.length > 0)) records.push(record);
  }

  if (records.length < 2) throw new Error('The CSV needs a header row and at least one product row.');

  const columns = records[0].map((column) => column.trim()).filter(Boolean);
  if (columns.length === 0) throw new Error('The CSV header row is empty.');

  const rows = records.slice(1).map((values) => {
    const row: Record<string, string> = {};
    columns.forEach((column, columnIndex) => {
      row[column] = values[columnIndex] ?? '';
    });
    return row;
  });

  return { columns, rows };
}

/** Shopify product fields Mia can import, in the order merchants expect. */
export const IMPORT_FIELDS: Array<{ key: string; label: string; aliases: string[] }> = [
  { key: 'title', label: 'Title', aliases: ['title', 'product_title', 'name'] },
  { key: 'descriptionHtml', label: 'Description', aliases: ['description', 'body_html', 'description_html', 'body'] },
  { key: 'vendor', label: 'Vendor', aliases: ['vendor', 'supplier', 'brand'] },
  { key: 'productType', label: 'Product type', aliases: ['product_type', 'category', 'type'] },
  { key: 'tags', label: 'Tags', aliases: ['tags', 'tag'] },
  { key: 'sku', label: 'SKU', aliases: ['sku', 'variant_sku', 'variant sku'] },
  { key: 'price', label: 'Price', aliases: ['price', 'variant_price', 'variant price'] },
  { key: 'compareAtPrice', label: 'Compare-at price', aliases: ['compare_at_price', 'variant_compare_at_price', 'compare at price'] },
  { key: 'inventoryQuantity', label: 'Inventory', aliases: ['inventory_quantity', 'variant_inventory_qty', 'inventory_qty', 'quantity'] },
  { key: 'weight', label: 'Weight', aliases: ['weight', 'variant_weight'] },
  { key: 'image', label: 'Image URL', aliases: ['image', 'image_src', 'image_url', 'image src'] },
];

const normalize = (value: string) => value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');

/** Guess field -> column mapping from common header names. */
export function inferMapping(columns: string[]): Record<string, string> {
  const mapping: Record<string, string> = {};
  for (const field of IMPORT_FIELDS) {
    const match = columns.find((column) => field.aliases.some((alias) => normalize(alias) === normalize(column)));
    if (match) mapping[field.key] = match;
  }
  return mapping;
}
