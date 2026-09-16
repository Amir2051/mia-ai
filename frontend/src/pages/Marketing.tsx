import { useEffect, useMemo, useState } from 'react';
import api from '../services/api';

type Product = { id: string; title: string; handle?: string; status?: string; descriptionHtml?: string; images?: { nodes?: Array<{ url?: string; altText?: string }> } };
type SEOResult = {
  seo_title: string; meta_description: string; optimized_title: string;
  optimized_description_html: string; keywords: string[]; tags: string[];
  handle_suggestion: string; image_alt_text: string[]; social_title: string;
  social_description: string; seo_score: number; issues: string[]; recommendations: string[];
};
type MarketingResult = {
  product_id: string; product_title: string; image_url?: string | null;
  facebook: { copy: string; cta: string };
  instagram: { copy: string; cta: string };
  tiktok: { copy: string; cta: string };
  email: { subject: string; body: string };
  ad: { primary_text: string; headline: string; description: string; cta: string };
  creative_prompt: string; hashtags: string[];
};

export default function Marketing() {
  const [products, setProducts] = useState<Product[]>([]);
  const [productId, setProductId] = useState('');
  const [current, setCurrent] = useState<any>(null);
  const [result, setResult] = useState<SEOResult | null>(null);
  const [marketing, setMarketing] = useState<MarketingResult | null>(null);
  const [creative, setCreative] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<any>(null);

  useEffect(() => {
    api.get('/auth/session').then(async (session) => {
      if (!session.data?.connected) throw new Error('Shopify is not connected');
      const res = await api.get('/products/?query=');
      const edges = res.data?.data?.products?.edges || [];
      const rows = edges.map((e: any) => e.node).filter((p: any) => p?.id);
      setProducts(rows);
      if (rows[0]) setProductId(rows[0].id);
    }).catch((err) => setError(err?.message || 'Unable to load Shopify products')).finally(() => setLoading(false));
  }, []);

  const selected = useMemo(() => products.find(p => p.id === productId), [products, productId]);

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

  async function generateMarketing() {
    if (!productId) return;
    setBusy(true); setError(null);
    try {
      const res = await api.post('/seo/marketing', { product_id: productId });
      setMarketing(res.data.result);
      setCurrent(res.data.product);
    } catch (err: any) { setError(err?.response?.data?.detail || err?.message || 'Marketing generation failed'); }
    finally { setBusy(false); }
  }

  async function generateCreative() {
    if (!productId) return;
    setBusy(true); setError(null);
    try {
      const res = await api.post('/seo/creative', { product_id: productId });
      setCreative(res.data.image);
    } catch (err: any) { setError(err?.response?.data?.detail || err?.message || 'AI creative generation failed'); }
    finally { setBusy(false); }
  }

  async function applyChanges(applyEverything = false) {
    if (!result || !current) return;
    const changes = {
      title: result.optimized_title,
      descriptionHtml: result.optimized_description_html,
      seo: { title: result.seo_title, description: result.meta_description },
      tags: result.tags,
      image_alt_text: result.image_alt_text,
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

  if (loading) return <p>Loading Marketing &amp; SEO…</p>;
  if (error && !products.length) return <p style={{ color: '#d72c0d' }}>Error: {error}</p>;

  return (
    <div className="marketing-workspace">
      <div className="mia-hero" style={{ marginBottom: 18 }}>
        <div className="eyebrow">Mia Growth Engine · LIVE SHOPIFY</div>
        <h1 className="mia-title">SEO + Marketing Studio</h1>
        <div className="mia-subtitle">Select a live Shopify product. Mia reads the actual product, sends grounded context to the AI engine, generates publish-ready SEO and channel content, then waits for your approval before changing Shopify.</div>
      </div>

      <div className="panel" style={{ marginBottom: 18 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontWeight: 700 }}>Live Product</label>
          <select value={productId} onChange={e => { setProductId(e.target.value); setResult(null); setAnalysis(null); setMarketing(null); setCreative(null); }} style={{ padding: 10, minWidth: 360, border: '1px solid #cbd5e1', borderRadius: 8 }}>
            {products.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
          </select>
          <button onClick={analyze} disabled={busy || !productId} style={buttonStyle(false)}>{busy ? 'Working…' : 'Analyze SEO'}</button>
          <button onClick={generate} disabled={busy || !productId} style={buttonStyle(true)}>✦ Generate Real SEO</button>
          <button onClick={generateMarketing} disabled={busy || !productId} style={buttonStyle(true)}>🚀 Generate Marketing</button>
          <button onClick={generateCreative} disabled={busy || !productId} style={buttonStyle(true)}>🎨 Generate AI Creative</button>
          <button onClick={async () => { await generate(); await generateMarketing(); }} disabled={busy || !productId} style={buttonStyle(true)}>⚡ Full SEO + Marketing Campaign</button>
        </div>
        {selected && <div style={{ marginTop: 12, fontSize: 12, color: '#64748b' }}>Shopify product ID: {selected.id} · Status: {selected.status || '—'}</div>}
      </div>

      {error && <div style={{ marginBottom: 14, padding: 10, background: '#fff4f4', color: '#d72c0d', borderRadius: 6 }}>{error}</div>}
      {analysis && <div style={cardStyle()}><h2 style={headingStyle()}>Current SEO Score: {analysis.seo_score}/100</h2><div><strong>Issues</strong>{analysis.issues.length ? analysis.issues.map((x: string) => <div key={x}>⚠ {x}</div>) : <div>✓ No major issues detected</div>}</div><div style={{ marginTop: 10 }}><strong>Recommendations</strong>{analysis.recommendations.map((x: string) => <div key={x}>• {x}</div>)}</div></div>}

      {result && current && <>
        <div style={{ ...cardStyle(), marginTop: 16, marginBottom: 16, background: 'linear-gradient(135deg,#eef2ff,#ecfeff)' }}><div style={{ fontSize: 13, color: '#475569' }}>AI SEO SCORE</div><div style={{ fontSize: 36, fontWeight: 800 }}>{result.seo_score}/100</div><div style={{ color: '#475569' }}>Generated from the selected live Shopify product.</div></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
          <div style={cardStyle()}><h2 style={headingStyle()}>Current Shopify SEO</h2><Field label="SEO title" value={current.seo_title || '—'} /><Field label="SEO description" value={current.seo_description || '—'} /><Field label="Product title" value={current.title || '—'} /><Field label="Handle" value={current.handle || '—'} /></div>
          <div style={cardStyle()}><h2 style={headingStyle()}>AI-Generated SEO</h2><Field label="SEO title" value={result.seo_title} /><Field label="Meta description" value={result.meta_description} /><Field label="Optimized title" value={result.optimized_title} /><Field label="Suggested handle" value={result.handle_suggestion} /></div>
          <div style={cardStyle()}><h2 style={headingStyle()}>Keywords &amp; Tags</h2><div style={{ marginBottom: 12 }}>{result.keywords.map(k => <span key={k} style={chipStyle()}>{k}</span>)}</div><strong>Shopify tags</strong><div>{result.tags.map(k => <span key={k} style={chipStyle()}>{k}</span>)}</div></div>
          <div style={cardStyle()}><h2 style={headingStyle()}>Image SEO</h2>{result.image_alt_text.map((x, i) => <div key={i} style={{ padding: '7px 0', borderBottom: '1px solid #e2e8f0' }}>{x}</div>)}</div>
          <div style={{ ...cardStyle(), gridColumn: '1 / -1' }}><h2 style={headingStyle()}>Publish-Ready Product Description</h2><div dangerouslySetInnerHTML={{ __html: result.optimized_description_html }} /></div>
          <div style={cardStyle()}><h2 style={headingStyle()}>SEO Issues</h2>{result.issues.length ? result.issues.map(x => <div key={x}>⚠ {x}</div>) : <div>✓ No issues reported</div>}</div>
          <div style={cardStyle()}><h2 style={headingStyle()}>SEO Recommendations</h2>{result.recommendations.map(x => <div key={x}>• {x}</div>)}</div>
        </div>
        <div style={{ marginTop: 18, display: 'flex', gap: 10, flexWrap: 'wrap' }}><button onClick={() => applyChanges(false)} disabled={busy} style={buttonStyle(true)}>✓ Apply SEO to Shopify</button><button onClick={() => applyChanges(true)} disabled={busy} style={buttonStyle(false)}>Apply SEO + Handle</button><button onClick={generate} disabled={busy} style={buttonStyle(false)}>Regenerate</button></div>
      </>}

      {creative && <div style={{ ...cardStyle(), marginTop: 18, background: 'linear-gradient(135deg,#f5f3ff,#ecfeff)' }}><h2 style={headingStyle()}>🎨 AI-Generated Product Creative</h2><p style={{ color: '#475569' }}>Generated from the selected Shopify product and its live product image when available.</p><img src={creative} alt={current?.title || selected?.title || 'AI generated product creative'} style={{ width: '100%', maxWidth: 720, borderRadius: 16, display: 'block' }} /><a href={creative} download="mia-product-creative.png" style={{ ...buttonStyle(false), display: 'inline-block', marginTop: 12, textDecoration: 'none' }}>Open / Save Creative</a></div>}
      {marketing && <MarketingPanel result={marketing} />}
    </div>
  );
}

function MarketingPanel({ result }: { result: MarketingResult }) {
  return <div style={{ marginTop: 18 }}>
    <div style={{ ...cardStyle(), background: 'linear-gradient(135deg,#111827,#172554)', color: 'white', marginBottom: 14 }}>
      <div className="eyebrow" style={{ color: '#67e8f9' }}>MIA CREATIVE ENGINE · READY TO POST</div>
      <h2 style={{ fontSize: 22, margin: '6px 0' }}>{result.product_title}</h2>
      <p style={{ opacity: .82 }}>Real channel copy generated from this product. Use the Shopify image below as the source creative, or use Mia's creative prompt with an image generator.</p>
      {result.image_url && <img src={result.image_url} alt={result.product_title} style={{ width: 240, height: 240, objectFit: 'cover', borderRadius: 14, marginTop: 10 }} />}
      <div style={{ marginTop: 12 }}>{result.hashtags.map(h => <span key={h} style={{ display: 'inline-block', margin: 3, padding: '4px 8px', borderRadius: 999, background: 'rgba(255,255,255,.12)' }}>{h}</span>)}</div>
    </div>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 14 }}>
      <CopyCard title="Facebook · Ready to Post" text={result.facebook.copy} cta={result.facebook.cta} />
      <CopyCard title="Instagram · Ready to Post" text={result.instagram.copy} cta={result.instagram.cta} />
      <CopyCard title="TikTok · Ready to Post" text={result.tiktok.copy} cta={result.tiktok.cta} />
      <CopyCard title="Paid Ad · Ready to Launch" text={`${result.ad.primary_text}\n\nHeadline: ${result.ad.headline}\nDescription: ${result.ad.description}\nCTA: ${result.ad.cta}`} />
      <CopyCard title="Email Campaign" text={`Subject: ${result.email.subject}\n\n${result.email.body}`} />
      <CopyCard title="Creative Brief / Image Prompt" text={result.creative_prompt} />
    </div>
  </div>;
}

function CopyCard({ title, text, cta }: { title: string; text: string; cta?: string }) {
  return <div style={cardStyle()}><h2 style={headingStyle()}>{title}</h2><div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.55 }}>{text}</div>{cta && <div style={{ marginTop: 10, fontWeight: 800 }}>CTA: {cta}</div>}<button onClick={() => navigator.clipboard?.writeText(text)} style={{ ...buttonStyle(false), marginTop: 12 }}>Copy</button></div>;
}

function Field({ label, value }: { label: string; value: string }) { return <div style={{ marginBottom: 14 }}><div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>{label}</div><div style={{ padding: 10, background: '#f8fafc', borderRadius: 6, whiteSpace: 'pre-wrap' }}>{value}</div></div>; }
function cardStyle() { return { padding: 18, border: '1px solid #dbe3ef', borderRadius: 12, background: 'white' }; }
function headingStyle() { return { fontSize: 16, marginBottom: 12 }; }
function buttonStyle(primary: boolean) { return { padding: '9px 14px', borderRadius: 8, border: primary ? 'none' : '1px solid #cbd5e1', background: primary ? 'linear-gradient(135deg,#6366f1,#06b6d4)' : 'white', color: primary ? 'white' : '#0f172a', cursor: 'pointer', fontWeight: 700 }; }
function chipStyle() { return { display: 'inline-block', padding: '5px 8px', margin: '3px', background: '#eef2ff', borderRadius: 999, fontSize: 12 }; }
