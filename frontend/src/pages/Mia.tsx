import { FormEvent, useEffect, useState } from 'react';
import api from '../services/api';

type Context = { shop:string; revenue:number; counts:{products:number;orders:number;customers:number;inventory_units:number}; low_stock:Array<{title:string;inventory:number}>; warnings:string[] };
type Message = { role:'mia'|'user'; text:string };

export default function Mia(){
  const [context,setContext]=useState<Context|null>(null);
  const [messages,setMessages]=useState<Message[]>([{role:'mia',text:'I’m Mia. I can work with this organization’s live Shopify data and turn it into actionable insights.'}]);
  const [input,setInput]=useState(''); const [busy,setBusy]=useState(false); const [loading,setLoading]=useState(true);
  useEffect(()=>{api.get('/mia/context').then(r=>setContext(r.data)).catch(()=>setContext(null)).finally(()=>setLoading(false));},[]);
  async function send(e:FormEvent){e.preventDefault(); if(!input.trim()||busy)return; const q=input.trim(); setInput(''); setMessages(m=>[...m,{role:'user',text:q}]); setBusy(true); try{const r=await api.post('/mia/chat',{message:q}); setMessages(m=>[...m,{role:'mia',text:r.data.answer}]);}catch{setMessages(m=>[...m,{role:'mia',text:'I could not reach the live Shopify data right now. Please retry in a moment.'}]);}finally{setBusy(false);}}
  const money=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'});
  return <div className="mia-shell">
    <section>
      <div className="mia-hero"><div className="eyebrow">Mia Intelligence · Live Store Context</div><h1 className="mia-title">Your store, understood.</h1><div className="mia-subtitle">Mia reads the connected organization’s Shopify catalog, inventory, orders and available customer data—without exposing credentials to the browser.</div>
      <div className="metric-grid">{[['Products',context?.counts.products??'—'],['Orders',context?.counts.orders??'—'],['Customers',context?.counts.customers??'—'],['Order value',context?money.format(context.revenue):'—']].map(([l,v])=><div className="metric-card" key={l as string}><div className="metric-label">{l}</div><div className="metric-value">{loading?'…':v}</div></div>)}</div></div>
      <div className="panel" style={{marginTop:18}}><div className="panel-title">Mia can access</div><span className="chip">Products</span><span className="chip">Inventory</span><span className="chip">Orders</span><span className="chip">Customer insights</span><span className="chip">Store analytics</span><span className="chip">Marketing context</span></div>
      <div className="panel" style={{marginTop:18}}><div className="panel-title">Live signals</div>{context?.low_stock?.length ? context.low_stock.slice(0,6).map(p=><div className="data-row" key={p.title}><span>{p.title}</span><strong>{p.inventory} left</strong></div>):<p>{loading?'Scanning store data…':'No low-stock products in the current data window.'}</p>}{context?.warnings?.map(w=><p key={w} style={{fontSize:12,color:'#fbbf24'}}>⚠ {w}</p>)}</div>
    </section>
    <aside className="panel chat"><div className="panel-title"><span style={{color:'#67e8f9'}}>●</span> Mia Copilot <span style={{float:'right',fontSize:11,color:'#7f8ba5'}}>LIVE DATA</span></div><div className="chat-log">{messages.map((m,i)=><div key={i} className={`bubble ${m.role}`}>{m.text}</div>)}{busy&&<div className="bubble mia">Analyzing live store context…</div>}</div><form className="chat-form" onSubmit={send}><input className="chat-input" value={input} onChange={e=>setInput(e.target.value)} placeholder="Ask Mia about your store…"/><button className="glow-button" disabled={busy}>Ask</button></form></aside>
  </div>
}
