import { useState, useEffect, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import api from '../services/api';

type Variant = {
  id?: string | null;
  sku?: string | null;
  price?: string | null;
  inventoryQuantity?: number | null;
  selectedOptions?: Array<{ name?: string | null; value?: string | null }>;
};

type Image = {
  id?: string | null;
  url?: string | null;
  altText?: string | null;
};

type Product = {
  id?: string | null;
  title?: string | null;
  descriptionHtml?: string | null;
  status?: string | null;
  tags?: string | null;
  vendor?: string | null;
  productType?: string | null;
  variants?: { edges: Array<{ node: Variant }> };
  images?: { edges: Array<{ node: Image }> };
};

export default function ProductDetail() {
  const { id } = useParams();
  const [product, setProduct] = useState<Product | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState('');
  const [vendor, setVendor] = useState('');
  const [productType, setProductType] = useState('');
  const [status, setStatus] = useState('');
  const [descriptionHtml, setDescriptionHtml] = useState('');
  const [tagsInput, setTagsInput] = useState('');
  const [variantEdits, setVariantEdits] = useState<Record<string, { price?: string; inventoryQuantity?: string }>>({});

  useEffect(() => {
    api.get('/auth/session')
      .then((res) => setConnected(!!res.data?.connected))
      .catch(() => setConnected(false));
  }, []);

  const loadProduct = useCallback(() => {
    if (!connected) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    setSaveError(null);
    setSaveSuccess(null);

    api.get(`/products/${id}`)
      .then((res) => {
        if (cancelled) return;
        const data = (res.data?.data as Product) || null;
        setProduct(data);
        if (data) {
          setTitle(data.title || '');
          setVendor(data.vendor || '');
          setProductType(data.productType || '');
          setStatus(data.status || '');
          setDescriptionHtml(data.descriptionHtml || '');
          const tags = data.tags
            ? typeof data.tags === 'string'
              ? data.tags.split(',').map((tag) => tag.trim()).filter(Boolean)
              : []
            : [];
          setTagsInput(tags.join(', '));
          const mapped: Record<string, { price?: string; inventoryQuantity?: string }> = {};
          ((data.variants?.edges ?? [])).forEach((edge) => {
            const node = edge.node;
            if (node.id) {
              mapped[node.id] = {
                price: node.price ?? '',
                inventoryQuantity: node.inventoryQuantity != null ? String(node.inventoryQuantity) : '',
              };
            }
          });
          setVariantEdits(mapped);
        }
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setProduct(null);
          setError((err as Error)?.message || 'Failed to load product');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [connected, id]);

  useEffect(() => {
    loadProduct();
  }, [loadProduct]);

  const startEditing = () => {
    setEditing(true);
    setSaveError(null);
    setSaveSuccess(null);
  };

  const cancelEditing = () => {
    setEditing(false);
    setSaveError(null);
    setSaveSuccess(null);
    if (product) {
      setTitle(product.title || '');
      setVendor(product.vendor || '');
      setProductType(product.productType || '');
      setStatus(product.status || '');
      setDescriptionHtml(product.descriptionHtml || '');
      const tags = product.tags
        ? typeof product.tags === 'string'
          ? product.tags.split(',').map((tag) => tag.trim()).filter(Boolean)
          : []
        : [];
      setTagsInput(tags.join(', '));
    }
  };

  const saveProduct = async () => {
    if (!product?.id) return;

    setSaving(true);
    setSaveError(null);
    setSaveSuccess(null);

    const payload: Record<string, unknown> = {
      title: title || product.title,
      vendor: vendor || undefined,
      productType: productType || undefined,
      status: status || product.status,
      tags: tagsInput
        .split(',')
        .map((tag) => tag.trim())
        .filter(Boolean),
    };

    if (descriptionHtml !== (product.descriptionHtml || '')) {
      payload.descriptionHtml = descriptionHtml || undefined;
    }

    const variantsInput: Array<Record<string, unknown>> = [];
    (product.variants?.edges ?? []).forEach((edge) => {
      const node = edge.node;
      const edit = node.id ? variantEdits[node.id] : null;
      const patch: Record<string, unknown> = { id: node.id };
      if (edit) {
        if (edit.price !== (node.price ?? '')) {
          patch.price = edit.price || undefined;
        }
        if (edit.inventoryQuantity !== (node.inventoryQuantity != null ? String(node.inventoryQuantity) : '')) {
          patch.inventoryQuantity = edit.inventoryQuantity ? Number(edit.inventoryQuantity) : undefined;
        }
      }
      if (Object.keys(patch).length > 1) {
        variantsInput.push(patch);
      }
    });

    if (variantsInput.length) {
      payload.variants = variantsInput;
    }

    try {
      const res = await api.put(`/products/${product.id}`, { product: payload });
      const data = res.data?.data || res.data;
      setProduct((data as Product) || null);
      setEditing(false);
      setSaveSuccess('Product updated.');
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || (err as Error)?.message || 'Failed to update product';
      setSaveError(detail);
    } finally {
      setSaving(false);
    }
  };

  if (!connected) {
    return <p style={{ color: '#616161' }}>Connect Shopify to load live product data.</p>;
  }

  if (loading) {
    return <p>Loading...</p>;
  }

  if (error) {
    return <p style={{ color: '#d72c0d' }}>Error: {error}</p>;
  }

  if (!product) {
    return <p style={{ color: '#616161' }}>Product not found.</p>;
  }

  const variants = product.variants?.edges ?? [];
  const images = product.images?.edges ?? [];
  const tags = product.tags
    ? typeof product.tags === 'string'
      ? product.tags.split(',').map((tag) => tag.trim()).filter(Boolean)
      : product.tags
    : [];

  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 12 }}>
        {product.title || 'Untitled'}
      </h1>

      <div style={{ marginBottom: 16 }}>
        <Link to="/products" style={{ color: '#005bd3' }}>Back to products</Link>
      </div>

      {saveError && (
        <div style={{ padding: 12, borderRadius: 6, background: '#fff4f4', color: '#d72c0d', marginBottom: 16 }}>
          {saveError}
        </div>
      )}
      {saveSuccess && (
        <div style={{ padding: 12, borderRadius: 6, background: '#f0fdf4', color: '#15803d', marginBottom: 16 }}>
          {saveSuccess}
        </div>
      )}

      {!editing ? (
        <div style={{ marginBottom: 16 }}>
          <button
            onClick={startEditing}
            style={{ padding: '8px 12px', borderRadius: 6, border: '1px solid #e1e3e5', background: 'white', cursor: 'pointer' }}
          >
            Edit product
          </button>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16, marginBottom: 16 }}>
          <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
            <h2 style={{ fontSize: 16, marginBottom: 8 }}>Edit details</h2>
            <label style={{ display: 'block', fontSize: 12, color: '#616161', marginBottom: 4 }}>Title</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: '100%', padding: 8, marginBottom: 12, border: '1px solid #e1e3e5', borderRadius: 6 }} />

            <label style={{ display: 'block', fontSize: 12, color: '#616161', marginBottom: 4 }}>Vendor</label>
            <input value={vendor} onChange={(e) => setVendor(e.target.value)} style={{ width: '100%', padding: 8, marginBottom: 12, border: '1px solid #e1e3e5', borderRadius: 6 }} />

            <label style={{ display: 'block', fontSize: 12, color: '#616161', marginBottom: 4 }}>Product type</label>
            <input value={productType} onChange={(e) => setProductType(e.target.value)} style={{ width: '100%', padding: 8, marginBottom: 12, border: '1px solid #e1e3e5', borderRadius: 6 }} />

            <label style={{ display: 'block', fontSize: 12, color: '#616161', marginBottom: 4 }}>Status</label>
            <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: '100%', padding: 8, marginBottom: 12, border: '1px solid #e1e3e5', borderRadius: 6 }}>
              <option value="">Same</option>
              <option value="DRAFT">Draft</option>
              <option value="ACTIVE">Active</option>
              <option value="ARCHIVED">Archived</option>
            </select>

            <label style={{ display: 'block', fontSize: 12, color: '#616161', marginBottom: 4 }}>Tags</label>
            <input value={tagsInput} onChange={(e) => setTagsInput(e.target.value)} style={{ width: '100%', padding: 8, marginBottom: 12, border: '1px solid #e1e3e5', borderRadius: 6 }} />

            <label style={{ display: 'block', fontSize: 12, color: '#616161', marginBottom: 4 }}>Description HTML</label>
            <textarea value={descriptionHtml} onChange={(e) => setDescriptionHtml(e.target.value)} rows={6} style={{ width: '100%', padding: 8, marginBottom: 12, border: '1px solid #e1e3e5', borderRadius: 6 }} />
          </div>

          <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
            <h2 style={{ fontSize: 16, marginBottom: 8 }}>Edit variants</h2>
            {variants.length === 0 && <p style={{ color: '#616161' }}>No variants.</p>}
            {variants.map((edge) => {
              const variant = edge.node;
              const edit = variant.id ? variantEdits[variant.id] || {} : {};
              return (
                <div key={variant.id} style={{ padding: '10px 0', borderBottom: '1px solid #f1f2f4' }}>
                  <div style={{ fontWeight: 600 }}>{variant.sku || 'No SKU'}</div>
                  <label style={{ display: 'block', fontSize: 12, color: '#616161', marginTop: 8, marginBottom: 4 }}>Price</label>
                  <input
                    value={edit.price ?? variant.price ?? ''}
                    onChange={(e) => setVariantEdits((current) => ({
                      ...current,
                      [variant.id!]: { ...(current[variant.id!] || {}), price: e.target.value },
                    }))}
                    style={{ width: '100%', padding: 8, marginBottom: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                  />
                  <label style={{ display: 'block', fontSize: 12, color: '#616161', marginBottom: 4 }}>Inventory</label>
                  <input
                    value={edit.inventoryQuantity ?? (variant.inventoryQuantity != null ? String(variant.inventoryQuantity) : '')}
                    onChange={(e) => setVariantEdits((current) => ({
                      ...current,
                      [variant.id!]: { ...(current[variant.id!] || {}), inventoryQuantity: e.target.value },
                    }))}
                    style={{ width: '100%', padding: 8, marginBottom: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                  />
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
        <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Description</h2>
          <div
            style={{ color: '#202223' }}
            dangerouslySetInnerHTML={{ __html: product.descriptionHtml || '' }}
          />

          {images.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <h2 style={{ fontSize: 16, marginBottom: 8 }}>Images</h2>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {images.map((image) => (
                  <img
                    key={image.node?.id || image.node?.url}
                    src={image.node?.url || ''}
                    alt={image.node?.altText || ''}
                    style={{ width: 160, height: 160, objectFit: 'cover', borderRadius: 8, border: '1px solid #e1e3e5' }}
                  />
                ))}
              </div>
            </div>
          )}
        </div>

        <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Details</h2>
          <div style={{ color: '#616161' }}>{product.status || '—'}</div>
          <div style={{ color: '#616161' }}>{product.vendor ? `Vendor: ${product.vendor}` : ''}</div>
          <div style={{ color: '#616161' }}>{product.productType ? `Type: ${product.productType}` : ''}</div>

          <h2 style={{ fontSize: 16, marginBottom: 8, marginTop: 16 }}>Variants</h2>
          {variants.length === 0 && <p style={{ color: '#616161' }}>No variants.</p>}
          {variants.map((edge) => {
            const variant = edge.node;
            return (
              <div
                key={variant.id}
                style={{ padding: '10px 0', borderBottom: '1px solid #f1f2f4' }}
              >
                <div style={{ fontWeight: 600 }}>{variant.sku || 'No SKU'}</div>
                <div style={{ color: '#616161' }}>{variant.price ? `Price: ${variant.price}` : ''}</div>
                <div style={{ color: '#616161' }}>{variant.inventoryQuantity ?? '—'} in stock</div>
                {(variant.selectedOptions ?? []).map((option, index) => (
                  <div key={index} style={{ color: '#616161', fontSize: 12 }}>
                    {option.name}: {option.value}
                  </div>
                ))}
              </div>
            );
          })}

          <h2 style={{ fontSize: 16, marginBottom: 8, marginTop: 16 }}>Tags</h2>
          {tags.length === 0 ? (
            <p style={{ color: '#616161' }}>No tags.</p>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {tags.map((tag) => (
                <span
                  key={tag}
                  style={{
                    padding: '4px 8px',
                    background: '#f1f2f4',
                    borderRadius: 999,
                    fontSize: 12,
                  }}
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {editing && (
        <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
          <button
            onClick={saveProduct}
            disabled={saving}
            style={{ padding: '9px 14px', borderRadius: 6, border: 'none', background: saving ? '#8c9196' : '#008060', color: 'white', cursor: saving ? 'not-allowed' : 'pointer', fontWeight: 600 }}
          >
            {saving ? 'Saving…' : 'Save changes'}
          </button>
          <button
            onClick={cancelEditing}
            disabled={saving}
            style={{ padding: '9px 14px', borderRadius: 6, border: '1px solid #e1e3e5', background: 'white', cursor: saving ? 'not-allowed' : 'pointer' }}
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
