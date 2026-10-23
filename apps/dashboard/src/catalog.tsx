import React,{useEffect,useState} from 'react';
import type {components} from './api.generated';

// Read-only compatibility view for the original industrial runtime catalog.
export function CatalogCenter({kind,request}:{kind:'agents'|'skills'|'tools';request:<T>(path:string)=>Promise<T>}){
  const [catalog,setCatalog]=useState<components['schemas']['CatalogResponse']|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{request<components['schemas']['CatalogResponse']>('/catalog').then(setCatalog).catch(e=>setError(e.message))},[request]);
  return <section className="catalog"><div className="catalog-top"><h2>内置业务能力</h2><p>办公智能体和技能请在部门工作台中管理。</p></div>{error&&<p className="error">{error}</p>}<div className="panel">{catalog?.[kind].map(item=><article key={item.name}><h3>{item.name}</h3><p>{item.description}</p></article>)}</div></section>;
}
