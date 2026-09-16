import { useEffect, useState } from 'react';
import api from '../services/api';

type Product = { id: string; title: string; handle?: string; status?: string };
type SEOResult = {
  seo_title: string; meta_description: string; optimized_title: string;
  optimized_description_html: string; keywords: string[]; tags: string[];
  handle_suggestion: string; image_alt_text: string[]; social_title: string;
  social_description: string; seo_score: number; issues: string[]; recommendations: string[];
};

export default function Marketing() {
  const [products, setProducts] = useState<Product[]>([]);
  const [productId, setProductId] = useState('');
  const [current, setCurrent] = useState<any>(null);
  const [result, setResult] = useState<SEOResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<any>(null);

  useEffect(() => {
    api.get('/auth/session').then(async (session) => {
      if (!session.data?.connected) throw new Error('Shopify is not connected');
      const res = await api.get('/products/?first=250');
      const edges = res.data?.products?.edges || [];
      const rows = edges.map((e: any) => e.node).filter((p: any) => p?.id);
      setProducts(rows);
      if (rows[0]) setProductId(rows[0].id);
    }).catch((err) => setError(err?.message || 'Unable to load Shopify products')).finally(() => setLoading(false));
  }, []);

  async function generate() {
    if (!productId) return;
    setBusy(true); setError(null); setAnalysis(null);
    try {
      const res = await api.post('/seo/generate', { product_id: productId });
      setCurrent(res.data.product); setResult(res.data.result);
    } catch (err: any) { setError(err?.response?.data?.detail || err?.message || 'SEO generation failed'); }
    finally { setBusy(false); }
  }

  async function analyze() {
    if (!productId) return;
    setBusy(true); setError(null);
    try { const res = await api.post('/seo/analyze', { product_id: productId }); setCurrent(res.data.product); setAnalysis(res.data); }
    catch (err: any) { setError(err?.response?.data?.detail || err?.message || 'SEO analysis failed'); }
    finally { setBusy(false); }
  }

  async function applyChanges(applyEverything = false) {
    if (!result || !current) return;
    const changes = {
      title: result.optimized_title,
      descriptionHtml: result.optimized_description_html,
      seo: { title: result.seo_title, description: result.meta_description },
      tags: result.tags,
      ...(applyEverything ? { handle: result.handle_suggestion } : {}),
    };
    const summary = `Title: ${current.title} → ${changes.title}\nSEO title: ${current.seo_title || '(empty)'} → ${result.seo_title}\nSEO description: ${current.seo_description || '(empty)'} → ${result.meta_description}\nTags: ${(current.tags || []).join(', ')} → ${result.tags.join(', ')}${applyEverything ? `\nHandle: ${current.handle} → ${result.handle_suggestion}` : ''}`;
    if (!window.confirm(`Confirm these Shopify changes?\n\n${summary}`)) return;
    setBusy(true); setError(null);
    try {
      const res = await api.post('/seo/apply', { product_id: productId, changes, confirmed: true, apply_handle: applyEverything });
      setCurrent(res.data.product); setResult(null); setAnalysis(null);
    } catch (err: any) { setError(err?.response?.data?.detail || err?.message || 'SEO update failed'); }
    finally { setBusy(false); }
  }

  if (loading) return <p>Loading Marketing & SEO…</p>;
  if (error && !products.length) return <p style={{ color: '#d72c0d' }}>Error: {error}</p>;

  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 6 }}>Marketing &amp; SEO</h1>
      <p style={{ color: '#616161', marginBottom: 18 }}>Generate AI recommendations server-side, review every change, then explicitly approve Shopify updates.</p>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 18, flexWrap: 'wrap' }}>
        <label style={{ fontWeight: 600 }}>Select Product</label>
        <select value={productId} onChange={e => { setProductId(e.target.value); setResult(null); setAnalysis(null); }} style={{ padding: 9, minWidth: 360, border: '1px solid #e1e3e5', borderRadius: 6 }}>
          {products.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
        </select>
        <button onClick={generate} disabled={busy || !productId} style={buttonStyle(true)}>{busy ? 'Working…' : 'Generate SEO'}</button>
        <button onClick={analyze} disabled={busy || !productId} style={buttonStyle(false)}>Analyze SEO</button>
      </div>

      {error && <div style={{ marginBottom: 14, padding: 10, background: '#fff4f4', color: '#d72c0d', borderRadius: 6 }}>{error}</div>}

      {analysis && <div style={cardStyle()}><h2 style={headingStyle()}>Current SEO Score: {analysis.seo_score}/100</h2><div><strong>Issues</strong>{analysis.issues.length ? analysis.issues.map((x: string) => <div key={x}>⚠ {x}</div>) : <div>✓ No major issues detected</div>}</div><div style={{ marginTop: 10 }}><strong>Recommendations</strong>{analysis.recommendations.map((x: string) => <div key={x}>• {x}</div>)}</div></div>}

      {result && current && <>
        <div style={{ ...cardStyle(), marginBottom: 16 }}><div style={{ fontSize: 13, color: '#616161' }}>AI SEO SCORE</div><div style={{ fontSize: 36, fontWeight: 700 }}>{result.seo_score}/100</div></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
          <div style={cardStyle()}><h2 style={headingStyle()}>Current</h2><Field label="SEO title" value={current.seo_title || '—'} /><Field label="SEO description" value={current.seo_description || '—'} /><Field label="Product title" value={current.title || '—'} /><Field label="Handle" value={current.handle || '—'} /></div>
          <div style={cardStyle()}><h2 style={headingStyle()}>Generated</h2><Field label="SEO title" value={result.seo_title} /><Field label="Meta description" value={result.meta_description} /><Field label="Optimized title" value={result.optimized_title} /><Field label="Suggested handle" value={result.handle_suggestion} /></div>
          <div style={cardStyle()}><h2 style={headingStyle()}>Keywords &amp; Tags</h2><div style={{ marginBottom: 12 }}>{result.keywords.map(k => <span key={k} style={chipStyle()}>{k}</span>)}</div><strong>Shopify tags</strong><div>{result.tags.map(k => <span key={k} style={chipStyle()}>{k}</span>)}</div></div>
          <div style={cardStyle()}><h2 style={headingStyle()}>Image Alt Text</h2>{result.image_alt_text.map((x, i) => <div key={i} style={{ padding: '7px 0', borderBottom: '1px solid #f1f2f4' }}>{x}</div>)}</div>
          <div style={{ ...cardStyle(), gridColumn: '1 / -1' }}><h2 style={headingStyle()}>Optimized Product Description</h2><div dangerouslySetInnerHTML={{ __html: result.optimized_description_html }} /></div>
          <div style={cardStyle()}><h2 style={headingStyle()}>Issues</h2>{result.issues.length ? result.issues.map(x => <div key={x}>⚠ {x}</div>) : <div>✓ No issues reported</div>}</div>
          <div style={cardStyle()}><h2 style={headingStyle()}>Recommendations</h2>{result.recommendations.map(x => <div key={x}>• {x}</div>)}</div>
        </div>
        <div style={{ marginTop: 18, display: 'flex', gap: 10, flexWrap: 'wrap' }}><button onClick={() => applyChanges(false)} disabled={busy} style={buttonStyle(true)}>Apply SEO</button><button onClick={() => applyChanges(true)} disabled={busy} style={buttonStyle(false)}>Apply Everything</button><button onClick={generate} disabled={busy} style={buttonStyle(false)}>Regenerate</button></div>
      </>}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) { return <div style={{ marginBottom: 14 }}><div style={{ fontSize: 12, color: '#616161', marginBottom: 4 }}>{label}</div><div style={{ padding: 10, background: '#f6f6f7', borderRadius: 6, whiteSpace: 'pre-wrap' }}>{value}</div></div>; }
function cardStyle() { return { padding: 18, border: '1px solid #e1e3e5', borderRadius: 8, background: 'white' }; }
function headingStyle() { return { fontSize: 16, marginBottom: 12 }; }
function buttonStyle(primary: boolean) { return { padding: '9px 14px', borderRadius: 6, border: primary ? 'none' : '1px solid #e1e3e5', background: primary ? '#008060' : 'white', color: primary ? 'white' : '#202223', cursor: 'pointer', fontWeight: 600 }; }
function chipStyle() { return { display: 'inline-block', padding: '5px 8px', margin: '3px', background: '#f1f2f4', borderRadius: 999, fontSize: 12 }; }
