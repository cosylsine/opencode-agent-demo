import React, {useEffect, useLayoutEffect, useRef, useState} from 'react';
import openCodeLogo from './assets/opencode-logo.svg';

export type View = 'chat'|'console'|'tuning'|'agents'|'skills'|'tools';
const destinations: {id:View;label:string;path:string}[] = [
  {id:'chat',label:'接待对话',path:'M4 4h16v11H9l-5 4V4Z'},
  {id:'console',label:'Monitor 诊断',path:'M3 12h4l3-7 4 14 3-7h4'},
  {id:'tuning',label:'B 回路仿真调优',path:'M5 3v18M12 3v18M19 3v18M2 8h6M9 16h6M16 9h6'},
  {id:'agents',label:'Agent 中心',path:'M8 8V5h8v3M5 8h14v12H5V8Zm3 5h1m6 0h1M9 17h6M12 2v3'},
  {id:'skills',label:'Skill 中心',path:'M5 3h10l4 4v14H5V3Zm10 0v5h4M8 12h8M8 16h5'},
  {id:'tools',label:'Tool 中心',path:'m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18'},
];

function SidebarIcon(){
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" stroke="currentColor" strokeWidth="1.6"/><path d="M9 4v16" stroke="currentColor" strokeWidth="1.6"/></svg>;
}

// One viewport owns the application; each workspace owns its reading surface.
export function WorkspaceShell({view,onNavigate,title,subtitle,status,source,sidebar,notices,overlays,children,embedded=false}:{
  view:View;onNavigate:(view:View)=>void;title:string;subtitle?:string;status:React.ReactNode;source:string;
  sidebar:(close:()=>void)=>React.ReactNode;notices:React.ReactNode;
  overlays?:React.ReactNode;children:React.ReactNode;embedded?:boolean;
}){
  const [width,setWidth]=useState(()=>Math.max(220,Math.min(420,Number(localStorage.getItem('pid-rail-width'))||260)));
  const [closed,setClosed]=useState(()=>localStorage.getItem('pid-sidebar-closed')==='true');
  const [mobile,setMobile]=useState(()=>matchMedia('(max-width: 900px)').matches);
  const [drawerOpen,setDrawerOpen]=useState(false);
  const sidebarRef=useRef<HTMLElement>(null),toggleRef=useRef<HTMLButtonElement>(null);
  const restoreFocus=useRef(false);
  const hasContext=!embedded||view==='chat'||view==='console';
  const visible=hasContext&&(mobile?drawerOpen:!closed);
  const label=view==='chat'?'对话':view==='console'?'运行历史':'导航';
  const resize=(next:number)=>setWidth(Math.max(220,Math.min(420,next)));
  const closeDrawer=()=>{restoreFocus.current=mobile;setDrawerOpen(false)};

  useLayoutEffect(()=>{
    // Focus can return only after React has removed inert from the main area.
    if(!drawerOpen&&restoreFocus.current){toggleRef.current?.focus();restoreFocus.current=false}
  },[drawerOpen]);

  useEffect(()=>{localStorage.setItem('pid-rail-width',String(width))},[width]);
  useEffect(()=>{localStorage.setItem('pid-sidebar-closed',String(closed))},[closed]);
  useEffect(()=>{
    const query=matchMedia('(max-width: 900px)');
    const update=()=>{setMobile(query.matches);setDrawerOpen(false)};
    query.addEventListener('change',update);
    return()=>query.removeEventListener('change',update);
  },[]);
  useEffect(()=>{
    // Mobile keyboards resize the visual viewport, even when 100dvh stays unchanged.
    const viewport=window.visualViewport;
    const update=()=>{
      if(!viewport||viewport.scale!==1)return;
      document.documentElement.style.setProperty('--app-height',`${viewport.height}px`);
    };
    update();viewport?.addEventListener('resize',update);
    return()=>{viewport?.removeEventListener('resize',update);document.documentElement.style.removeProperty('--app-height')};
  },[]);
  useEffect(()=>{
    if(!mobile||!drawerOpen)return;
    sidebarRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const handleKey=(event:KeyboardEvent)=>{
      if(document.querySelector('.session-menu, dialog[open]'))return;
      if(event.key==='Escape'){event.preventDefault();closeDrawer();return}
      if(event.key!=='Tab')return;
      const controls=Array.from(sidebarRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select, [tabindex="0"]')??[]).filter(el=>el.getClientRects().length);
      const first=controls[0],last=controls.at(-1);
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
    };
    document.addEventListener('keydown',handleKey);
    return()=>document.removeEventListener('keydown',handleKey);
  },[mobile,drawerOpen]);

  return <div className={'app-shell'+(embedded?' embedded-workspace':'')+(!visible?' sidebar-closed':'')+(mobile?' is-mobile':'')} style={{'--rail-width':width+'px'} as React.CSSProperties}>
    {mobile&&drawerOpen&&<div className="sidebar-backdrop" onClick={closeDrawer} aria-hidden="true"/>}
    {visible&&<aside id="workspace-sidebar" className="app-sidebar" ref={sidebarRef} role={mobile?'dialog':undefined} aria-modal={mobile?true:undefined} aria-label="工作区导航">
      <div className="sidebar-brand">{embedded?<strong className="context-title">{label==='对话'?'对话历史':label}</strong>:<div className="brand"><span className="brand-mark">PID</span><span className="brand-name">CONTROL LAB</span></div>}<button className="icon-button sidebar-close" aria-label={mobile?'关闭导航':'收起导航'} onClick={()=>mobile?closeDrawer():setClosed(true)}><SidebarIcon/></button></div>
      <div className="sidebar-body">
        {!embedded&&<nav className="view-nav" aria-label="工作区">{destinations.map((item,index)=><React.Fragment key={item.id}>{index===3&&<span className="nav-group-label">能力目录</span>}<button className={view===item.id?'active':''} aria-current={view===item.id?'page':undefined} onClick={()=>{onNavigate(item.id);closeDrawer()}}><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={item.path}/></svg>{item.label}</button></React.Fragment>)}</nav>}
        <div className="sidebar-context">{sidebar(closeDrawer)}</div>
      </div>
      {!embedded&&<div className="sidebar-foot"><span className="sidebar-foot-mark" aria-hidden="true">◇</span><div><strong>PID 建议工作台</strong><p>示例与仿真 · 建议不会下发设备</p></div></div>}
    </aside>}
    {visible&&!mobile&&<div className="rail-resizer" role="separator" aria-label="拖动调整侧栏宽度" aria-orientation="vertical" aria-valuemin={220} aria-valuemax={420} aria-valuenow={width} tabIndex={0} title="拖动调整宽度；双击恢复默认宽度"
      onPointerDown={e=>{e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId)}}
      onPointerMove={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId))resize(e.clientX-(e.currentTarget.parentElement?.getBoundingClientRect().left??0))}}
      onPointerUp={e=>e.currentTarget.releasePointerCapture(e.pointerId)} onDoubleClick={()=>resize(260)}
      onKeyDown={e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();resize(width+(e.key==='ArrowRight'?20:-20))}}}/>}
    <main className="app-main" inert={mobile&&drawerOpen}>
      {(!embedded||hasContext)&&<header className="workspace-header"><button ref={toggleRef} className="icon-button sidebar-toggle" aria-controls="workspace-sidebar" aria-expanded={visible} aria-label={mobile?'打开导航':`${visible?'收起':'展开'}${label}侧栏`} title={mobile?'打开导航':`${visible?'收起':'展开'}侧栏`} onClick={()=>mobile?setDrawerOpen(true):setClosed(!closed)}><SidebarIcon/></button><div className="workspace-title">{!embedded&&<h1>{title}</h1>}{subtitle&&<p title={subtitle}>{subtitle}</p>}{embedded&&!subtitle&&<p>{source}</p>}</div><div className="workspace-meta">{!embedded&&<div className="workspace-opencode"><img src={openCodeLogo} width="117" height="21" alt="OpenCode"/></div>}{status}</div></header>}
      <div className="workspace-notices">{notices}</div>
      <div className="workspace-content" key={view}>{children}</div>
      <footer className="workspace-foot">{source}</footer>
    </main>
    {overlays}
  </div>;
}
