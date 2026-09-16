import { FormEvent, useEffect, useState } from 'react';
import api from '../services/api';

type Product = { id:string; title:string; inventory:number|null; status:string; description:string; createdAt?:string|null };
type Context = { shop:string; revenue:number; counts:{products:number;orders:number;customers:number;inventory_units:number}; products:Product[]; low_stock:Array<{title:string;inventory:number}>; warnings:string[] };
type Message = { role:'mia'|'user'; text:string };

const quick = ['Show me all products with low inventory','Find products imported recently','Which products have weak descriptions?','Which products should I advertise?','What can you do?'];

export default function Mia(){
  const [context,setContext]=useState<Context|null>(null);
  const [messages,setMessages]=useState<Message[]>([{role:'mia',text:'I’m Mia — your Shopify product copilot. I scan the connected store catalog and can analyze, enhance, market, and apply merchant-approved product changes.'}]);
  const [input,setInput]=useState(''); const [busy,setBusy]=useState(false); const [loading,setLoading]=useState(true);
  useEffect(()=>{api.get('/mia/context').then(r=>setContext(r.data)).catch(()=>setContext(null)).finally(()=>setLoading(false));},[]);
  async function send(e?:FormEvent, preset?:string){e?.preventDefault(); const q=(preset ?? input).trim(); if(!q||busy)return; setInput(''); setMessages(m=>[...m,{role:'user',text:q}]); setBusy(true); try{const r=await api.post('/mia/chat',{message:q}); setMessages(m=>[...m,{role:'mia',text:r.data.answer}]);}catch{setMessages(m=>[...m,{role:'mia',text:'I could not reach the live Shopify data right now. Please retry.'}]);}finally{setBusy(false);}}
  return <div className="mia-shell">
    <section>
      <div className="mia-hero"><div className="eyebrow">Mia Intelligence · Full Catalog Copilot</div><h1 className="mia-title">Your store, understood.</h1><div className="mia-subtitle">Mia scans the full Shopify catalog, including products created or imported by other apps, then turns live store data into product and marketing actions.</div>
      <div className="metric-grid">{[['Products',context?.counts.products??'—'],['Inventory',context?.counts.inventory_units??'—'],['Customers',context?.counts.customers??'—'],['Orders',context?.counts.orders??'—']].map(([l,v])=><div className="metric-card" key={l as string}><div className="metric-label">{l}</div><div className="metric-value">{loading?'…':v}</div></div>)}</div></div>
      <div className="panel" style={{marginTop:18}}><div className="panel-title">Copilot actions</div><div style={{display:'flex',flexWrap:'wrap',gap:8}}>{quick.map(q=><button key={q} className="chip" onClick={()=>send(undefined,q)} disabled={busy}>{q}</button>)}</div></div>
      <div className="panel" style={{marginTop:18}}><div className="panel-title">Live catalog signals</div>{context?.low_stock?.length ? context.low_stock.slice(0,10).map(p=><div className="data-row" key={p.title}><span>{p.title}</span><strong>{p.inventory} left</strong></div>):<p>{loading?'Scanning the full catalog…':'No low-stock products found.'}</p>}{context?.warnings?.map(w=><p key={w} style={{fontSize:12,color:'#fbbf24'}}>⚠ {w}</p>)}</div>
    </section>
    <aside className="panel chat"><div className="panel-title"><span style={{color:'#67e8f9'}}>●</span> Mia Copilot <span style={{float:'right',fontSize:11,color:'#7f8ba5'}}>LIVE SHOPIFY</span></div><div className="chat-log">{messages.map((m,i)=><div key={i} className={`bubble ${m.role}`}>{m.text}</div>)}{busy&&<div className="bubble mia">Scanning live store intelligence…</div>}</div><form className="chat-form" onSubmit={send}><input className="chat-input" value={input} onChange={e=>setInput(e.target.value)} placeholder="Ask Mia to find, analyze, improve, or market…"/><button className="glow-button" disabled={busy}>Ask</button></form></aside>
  </div>
}
