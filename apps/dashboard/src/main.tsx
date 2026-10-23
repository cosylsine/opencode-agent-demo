import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import * as echarts from 'echarts/core';
import {LineChart} from 'echarts/charts';
import {GridComponent, TooltipComponent, LegendComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);
import type {components} from './api.generated';
import {CatalogCenter} from './catalog';
import {TuningLab} from './tuning';
import {WorkspaceShell, type View} from './workspace';
import {useChatScroll} from './chat-scroll';
import {OfficeApp} from './office-ui';
import './style.css';
import './workspace.css';
import './chat.css';
import './business.css';

type Schema = components['schemas'];
type Run = Schema['RunDetail'];
type Series = Schema['SeriesResponse'];
type Scheduler = Schema['SchedulerConfig'];
type ChatSession = Schema['ChatSession'];
type ChatMessage = Schema['ChatMessage'];
const terminal = new Set(['NO_TUNING_REQUIRED','MANUAL_REVIEW_REQUIRED','SUCCEEDED',
  'STOPPED_NO_IMPROVEMENT','STOPPED_MAX_ITERATIONS','STOPPED_TIMEOUT','FAILED_FINAL','CANCELLED']);
const labels:Record<string,string>={CREATED:'已创建',DATA_LOADING:'加载快照',MONITOR_REQUESTED:'Monitor 分析中',
  MONITOR_COMPLETED:'Monitor 已完成',BASELINE_REQUESTED:'准备基线',BASELINE_RUNNING:'基线评测中',
  BASELINE_COMPLETED:'基线已完成',CANDIDATE_REQUESTED:'Control 建议中',CANDIDATE_VALIDATED:'参数校验通过',
  TRIAL_REQUESTED:'准备候选评测',TRIAL_RUNNING:'候选评测中',TRIAL_COMPLETED:'评测完成',EVALUATING:'指标裁决中',
  CANDIDATE_ACCEPTED:'已接受建议',CANDIDATE_REJECTED:'候选被拒绝',SUCCEEDED:'示例目标达成',
  NO_TUNING_REQUIRED:'无需调参',MANUAL_REVIEW_REQUIRED:'需要人工复核',STOPPED_NO_IMPROVEMENT:'连续无改善停止',
  STOPPED_MAX_ITERATIONS:'达到轮次上限',STOPPED_TIMEOUT:'总时限到期',FAILED_FINAL:'失败',CANCELLED:'已取消'};
async function api<T>(path:string,body?:unknown,method='POST'):Promise<T>{
  const headers:Record<string,string>={};
  if(body!==undefined){
    const csrf=await fetch('/api/v1/csrf-token').then(r=>r.json());
    headers['X-CSRF-Token']=csrf.token;
    headers['Idempotency-Key']=crypto.randomUUID?.() ?? Array.from(
      crypto.getRandomValues(new Uint8Array(16)), b=>b.toString(16).padStart(2,'0')).join('');
    headers['Content-Type']='application/json';
  }
  const response=await fetch('/api/v1'+path,{method:body===undefined?'GET':method,headers,
    body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok)throw new Error(`${data.error?.code ?? response.status}: ${data.error?.message ?? '请求失败'}`);
  return data;
}
const n=(value:number|null|undefined)=>value==null?'—':value.toFixed(6);
function Pid({pid}:{pid:Schema['PID']|null|undefined}){
  return <div className="pid">{(['kp','ki','kd'] as const).map(k=><span key={k}><small>{k.toUpperCase()}</small><strong>{n(pid?.[k])}</strong></span>)}</div>;
}
type Curve = {label:string;series:Series};
const curveColors=['#627e8d','#087e7c','#c47a32','#78649c','#b75d70','#368774','#9b6b43'];
function spectrum(points:Series['points']):[number,number][]{
  const valid=points.filter(p=>p.quality==='GOOD'&&p.pressure!==null);
  if(valid.length<8||valid.length!==points.length)return [];
  const dt=(valid.at(-1)!.elapsed_seconds-valid[0].elapsed_seconds)/(valid.length-1);
  if(dt<=0||valid.some((p,i)=>Math.abs(p.elapsed_seconds-valid[0].elapsed_seconds-i*dt)>dt*0.01))return [];
  const values=valid.map(p=>p.pressure!-p.target_pressure);
  const mean=values.reduce((sum,v)=>sum+v,0)/values.length;
  const window=values.map((_,i)=>0.5-0.5*Math.cos(2*Math.PI*i/(values.length-1)));
  const scale=2/window.reduce((sum,v)=>sum+v,0);
  return Array.from({length:Math.floor(values.length/2)},(_,index)=>{
    const bin=index+1,frequency=bin/(values.length*dt);
    let real=0,imaginary=0;
    values.forEach((value,i)=>{const angle=2*Math.PI*bin*i/values.length;real+=(value-mean)*window[i]*Math.cos(angle);imaginary-=(value-mean)*window[i]*Math.sin(angle)});
    return [frequency,Math.hypot(real,imaginary)*scale] as [number,number];
  });
}
function Chart({data,kind}:{data:Curve[];kind:'response'|'spectrum'}){
  const ref=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(!ref.current)return;const chart=echarts.init(ref.current);
    const compact=ref.current.clientWidth<470;
    const isResponse=kind==='response';
    const first=data[0]?.series.points;
    const start=first?.[0]?.elapsed_seconds??0;
    const end=Math.max(start+1,...data.map(item=>item.series.points.at(-1)?.elapsed_seconds??start));
    const targets=first?.filter(p=>Number.isFinite(p.target_pressure)).map(p=>[p.elapsed_seconds,p.target_pressure])??[];
    const frequencyRanges=data.map(item=>spectrum(item.series.points)).filter(values=>values.length);
    const minFrequency=frequencyRanges.length?Math.min(...frequencyRanges.map(values=>values[0][0])):undefined;
    const maxFrequency=frequencyRanges.length?Math.max(...frequencyRanges.map(values=>values.at(-1)![0])):undefined;
    const unit=data[0]?.series.pressure_unit??'demo_unit';
    chart.setOption({color:curveColors,
      animationDuration:240,
      tooltip:{trigger:'axis',axisPointer:{type:'cross',label:{backgroundColor:'#25424c'}},backgroundColor:'#172b37',borderWidth:0,textStyle:{color:'#f5f3ec',fontSize:11},valueFormatter:(value:unknown)=>{const reading=Array.isArray(value)?value[1]:value;return typeof reading==='number'?`${reading.toFixed(isResponse?4:5)} ${unit}`:'—'}},
      legend:{bottom:0,type:'scroll',itemWidth:14,itemHeight:3,textStyle:{color:'#405b66',fontSize:11},selectedMode:true},
      grid:{left:compact?55:66,right:compact?18:26,top:24,bottom:64,containLabel:false},
      xAxis:{type:isResponse?'value':'log',logBase:10,name:isResponse?'时间 / s':'频率 / Hz',nameLocation:'middle',nameGap:28,min:isResponse?start:minFrequency,max:isResponse?end:maxFrequency,axisLabel:{color:'#526874',fontSize:10,formatter:(value:number)=>isResponse?String(value):value.toPrecision(2)},axisLine:{lineStyle:{color:'#b9c9cd'}},splitLine:{lineStyle:{color:'#edf1ef'}}},
      yAxis:{type:'value',name:(isResponse?'压力 / ':'幅值 / ')+unit,nameGap:compact?28:39,nameLocation:'middle',scale:true,axisLabel:{color:'#526874',fontSize:10,formatter:(value:number)=>isResponse?value.toFixed(2):value.toPrecision(2)},axisLine:{lineStyle:{color:'#b9c9cd'}},splitLine:{lineStyle:{color:'#edf1ef'}}},
      series:[...data.map(({label,series},i)=>({name:label,type:'line',showSymbol:false,connectNulls:false,sampling:isResponse?'lttb':undefined,
        lineStyle:{width:i===0?1.8:2.3,opacity:i===0?0.78:1},
        data:isResponse?series.points.map(p=>[p.elapsed_seconds,p.quality==='GOOD'?p.pressure:null]):spectrum(series.points)})),
        ...(isResponse&&targets.length?[{name:'目标压力',type:'line',showSymbol:false,silent:true,itemStyle:{color:'#879da1'},lineStyle:{type:'dashed',color:'#879da1',width:1.4},data:targets}]:[])]});
    const observer=new ResizeObserver(()=>chart.resize());observer.observe(ref.current);
    return()=>{observer.disconnect();chart.dispose()};},[data,kind]);
  return <div ref={ref} className="chart" role="img" aria-label={kind==='response'?'压力时间响应图，含各次评测和目标压力':'压力偏差单边幅值谱；不是 Bode 图'}/>;
}
function Diagnostics({data}:{data:Curve[]}){
  const spectralCurves=useMemo(()=>data.filter(item=>spectrum(item.series.points).length>0),[data]);
  const first=data[0]?.series;
  const points=first?.points??[];
  const good=points.filter(p=>p.quality==='GOOD'&&p.pressure!==null);
  const duration=points.length?points.at(-1)!.elapsed_seconds-points[0].elapsed_seconds:null;
  const final=good.at(-1);
  const maxDeviation=good.length?Math.max(...good.map(p=>Math.abs(p.pressure!-p.target_pressure))):null;
  return <section className="panel diagnostics"><div className="section-heading"><h2>响应与频域诊断</h2><small>示例数据 · 离线评测</small></div>
    <div className="diagnostic-facts"><span>目标 <strong>{points[0]?.target_pressure.toFixed(2)??'—'} {first?.pressure_unit??'demo_unit'}</strong></span><span>观测窗口 <strong>{duration==null?'—':`${duration.toFixed(0)} s`}</strong></span><span>有效样本 <strong>{good.length}/{points.length||'—'}</strong></span><span>末点偏差 <strong>{final?(final.pressure!-final.target_pressure).toFixed(4):'—'}</strong></span><span>最大绝对偏差 <strong>{maxDeviation?.toFixed(4)??'—'}</strong></span></div>
    <div className="diagnostic-grid"><div className="diagnostic-plot"><div className="plot-heading"><h3>压力时间响应</h3><span>观测 / 基线 / 候选</span></div>{data.length?<Chart data={data} kind="response"/>:<p className="plot-empty">运行产生压力序列后显示响应曲线。</p>}<p className="plot-caption">虚线为序列中的目标值；悬停查看采样读数，图例可切换曲线。无效样本以断点表示。</p></div>
      <div className="diagnostic-plot"><div className="plot-heading"><h3>压力偏差频谱</h3><span>幅值 · Hz</span></div>{spectralCurves.length?<Chart data={spectralCurves} kind="spectrum"/>:<p className="plot-empty">有效、等间隔的压力样本不足，暂不能计算频谱。</p>}<p className="plot-caption">去均值后使用 Hann 窗计算单边幅值谱。Bode 增益与相位需要输入激励和被控对象响应；当前接口仅提供压力时间序列，不能据此推算。</p></div></div>
  </section>;
}
function App({initialView='chat',onPlatformNavigate}:{initialView?:View;onPlatformNavigate?:(view:string)=>void}){
  const [scenarios,setScenarios]=useState<Schema['Scenario'][]>([]),[scenario,setScenario]=useState('improvable');
  const [health,setHealth]=useState<Schema['HealthResponse']|null>(null),[runs,setRuns]=useState<Run[]>([]);
  const [availableModels,setAvailableModels]=useState<Schema['AvailableModel'][]|null>(null);
  const [selected,setSelected]=useState<string|null>(null),[run,setRun]=useState<Run|null>(null);
  const [curves,setCurves]=useState<{label:string;series:Series}[]>([]),[events,setEvents]=useState<Schema['AuditEvent'][]>([]);
  const [error,setError]=useState(''),[pending,setPending]=useState(false),[scheduler,setScheduler]=useState<Scheduler|null>(null);
  const [cursor,setCursor]=useState<string|null>(null),[review,setReview]=useState('');
  const [view,setView]=useState<View>(initialView);
  useEffect(()=>setView(initialView),[initialView]);
  const [chats,setChats]=useState<ChatSession[]>([]),[chatId,setChatId]=useState<string|null>(null);
  const [chatQuery,setChatQuery]=useState('');
  const [menu,setMenu]=useState<{id:string;x:number;y:number}|null>(null);
  const [renaming,setRenaming]=useState<string|null>(null),[renameDraft,setRenameDraft]=useState('');
  const [deleteTarget,setDeleteTarget]=useState<ChatSession|null>(null),[chatMutation,setChatMutation]=useState(false);
  const [messages,setMessages]=useState<ChatMessage[]>([]),[draft,setDraft]=useState(''),[sending,setSending]=useState(false);
  const [chatRuns,setChatRuns]=useState<Record<string,Run>>({});
  const [chatEvents,setChatEvents]=useState<Record<string,Schema['AuditEvent'][]>>({});
  const composerRef=useRef<HTMLTextAreaElement>(null);
  const creatingChat=useRef<Promise<string>|null>(null);
  const deleteDialogRef=useRef<HTMLDialogElement>(null);
  const chatScroll=useChatScroll(chatId,view==='chat',`${messages.at(-1)?.message_id}:${sending}`);
  const cache=useRef(new Map<string,Series>());
  const paged=useRef(false);
  useLayoutEffect(()=>{const el=composerRef.current;if(el){el.style.height='auto';el.style.height=el.scrollHeight+'px'}},[draft,view]);
  useEffect(()=>{if(!menu)return;
    const close=()=>setMenu(null);
    const keyboard=(event:KeyboardEvent)=>{if(event.key==='Escape')close()};
    document.addEventListener('pointerdown',close);document.addEventListener('keydown',keyboard);
    return()=>{document.removeEventListener('pointerdown',close);document.removeEventListener('keydown',keyboard)};
  },[menu]);
  useEffect(()=>{if(deleteTarget&&!deleteDialogRef.current?.open)deleteDialogRef.current?.showModal()},[deleteTarget]);
  async function refresh(){
    const [h,list]=await Promise.all([api<Schema['HealthResponse']>('/health'),api<Schema['RunList']>('/runs')]);
    setHealth(h);setRuns(old=>!paged.current?list.items:[...list.items,...old.filter(r=>!list.items.some(n=>n.run_id===r.run_id))]);if(!paged.current)setCursor(list.next_cursor);
  }
  useEffect(()=>{let alive=true;
    api<Schema['Scenarios']>('/scenarios').then(x=>alive&&setScenarios(x.items)).catch(e=>setError(e.message));
    api<Scheduler>('/scheduler').then(x=>alive&&setScheduler(x)).catch(e=>setError(e.message));
    api<Schema['AvailableModels']>('/models').then(x=>alive&&setAvailableModels(x.items)).catch(()=>alive&&setAvailableModels([]));
    const tick=()=>refresh().catch(e=>alive&&setError(e.message));void tick();
    const timer=setInterval(tick,2500);return()=>{alive=false;clearInterval(timer)};},[]);
  useEffect(()=>{api<Schema['ChatSessions']>('/chat/sessions').then(x=>{
    setChats(old=>[...old.filter(item=>!x.items.some(fresh=>fresh.session_id===item.session_id)),...x.items]);if(x.items.length)setChatId(current=>current||x.items[0].session_id);
  }).catch(e=>setError(e.message));},[]);
  useEffect(()=>{if(!chatId){setMessages([]);return}if(sending)return;let alive=true;
    api<Schema['ChatMessages']>('/chat/sessions/'+chatId+'/messages').then(x=>alive&&setMessages(x.items)).catch(e=>alive&&setError(e.message));
    return()=>{alive=false};},[chatId,sending]);
  useEffect(()=>{let alive=true;const ids=[...new Set(messages.flatMap(m=>m.run_id?[m.run_id]:[]))];
    if(ids.length)setSelected(current=>current||ids[ids.length-1]);
    void Promise.all(ids.filter(id=>!chatRuns[id]).map(async id=>{try{return await api<Run>('/runs/'+id)}catch{return null}})).then(found=>{
      if(alive)setChatRuns(old=>({...old,...Object.fromEntries(found.filter((r):r is Run=>!!r).map(r=>[r.run_id,r]))}));
    });return()=>{alive=false};},[messages]);
  useEffect(()=>{if(!selected){setRun(null);return}let alive=true;
    setRun(null);setCurves([]);setEvents([]);
    async function poll(){try{
      const result=await api<Run>('/runs/'+selected);
      const audit=await api<Schema['EventsResponse']>('/runs/'+selected+'/events');
      const refs=[{label:'观测 · 实际 PID',ref:result.observation_series_ref},
        {label:'基线',ref:result.baseline_series_ref},...result.iterations.map(i=>({label:`候选 ${i.iteration_no}`,ref:i.series_ref}))];
      const values=await Promise.all(refs.filter(r=>r.ref).map(async r=>{
        let value=cache.current.get(r.ref!);if(!value){value=await api<Series>('/series/'+r.ref);cache.current.set(r.ref!,value)}
        return {label:r.label,series:value};}));
      if(alive){setRun(result);setRuns(old=>old.map(r=>r.run_id===result.run_id?result:r));setEvents(audit.items);setCurves(old=>old.length===values.length&&old.every((item,index)=>item.label===values[index].label&&item.series.series_ref===values[index].series.series_ref)?old:values)}
    }catch(e){if(alive)setError((e as Error).message)}}
    void poll();const timer=setInterval(poll,1500);return()=>{alive=false;clearInterval(timer)};},[selected]);
  async function action(fn:()=>Promise<unknown>){setPending(true);setError('');try{await fn();await refresh()}catch(e){setError((e as Error).message)}finally{setPending(false)}}
  const trigger=()=>action(async()=>{const r=await api<Schema['TriggerResponse']>('/triggers',{schema_version:'1.0',scenario_id:scenario});setSelected(r.run_id)});
  async function newChat(){
    if(creatingChat.current)return creatingChat.current;
    const pending=api<ChatSession>('/chat/sessions',{schema_version:'1.0'}).then(item=>{
      setChats(old=>[item,...old]);setChatId(item.session_id);setMessages([]);return item.session_id;
    });
    creatingChat.current=pending;
    try{return await pending}finally{creatingChat.current=null}
  }
  async function saveRename(id:string){
    const title=renameDraft.trim();if(!title){setError('会话名称不能为空');return}
    setChatMutation(true);setError('');
    try{const item=await api<ChatSession>('/chat/sessions/'+id,{schema_version:'1.0',title},'PATCH');setChats(old=>old.map(chat=>chat.session_id===id?item:chat));setRenaming(null)}
    catch(e){setError((e as Error).message)}finally{setChatMutation(false)}
  }
  async function deleteChat(item:ChatSession){
    setChatMutation(true);setError('');
    try{await api<ChatSession>('/chat/sessions/'+item.session_id,{},'DELETE');setChats(old=>old.filter(chat=>chat.session_id!==item.session_id));if(chatId===item.session_id){setChatId(chats.find(chat=>chat.session_id!==item.session_id)?.session_id||null);setMessages([])}setDeleteTarget(null)}
    catch(e){setError((e as Error).message)}finally{setChatMutation(false)}
  }
  async function send(message=draft,chosenScenario=scenario){
    const content=message.trim();if(!content||sending)return;
    chatScroll.scrollToLatest();setSending(true);setError('');setDraft('');
    try{
      const id=creatingChat.current?await creatingChat.current:chatId||await newChat();
      const turn=await api<Schema['ChatTurn']>('/chat/sessions/'+id+'/messages',{schema_version:'1.0',message:content,scenario_id:chosenScenario});
      setMessages(old=>[...old,turn.user_message,turn.response]);
      setChats(old=>old.map(item=>item.session_id===id&&item.title==='新对话'?{...item,title:content.slice(0,40)}:item));
      if(turn.response.run_id){setSelected(turn.response.run_id);await refresh()}
    }catch(e){setDraft(content);setError((e as Error).message)}finally{setSending(false)}
  }
  const navigate=(next:View)=>{setView(next);onPlatformNavigate?.(next)};
  const openRun=(id:string)=>{setSelected(id);navigate('console')};
  function loadChatEvents(id:string){
    if(chatEvents[id])return;
    api<Schema['EventsResponse']>('/runs/'+id+'/events').then(x=>setChatEvents(old=>({...old,[id]:x.items}))).catch(e=>setError(e.message));
  }
  const modelReady=health?.components.find(c=>c.name==='opencode/model')?.ready===true;
  const historyReady=health?.components.find(c=>c.name==='database')?.ready===true;
  const chatReady=modelReady&&health?.components.every(c=>c.name==='observation'||c.ready)===true;
  const unavailable=health?.components.filter(c=>!c.ready).map(c=>c.name==='observation'&&c.error_code==='TELEMETRY_WINDOW_NOT_READY'
    ?'Monitor 观测窗口尚未就绪：示例回路须持续运行，所有工况均需具备最近连续 120 秒的完整采样。采样缺秒后需重新积累连续窗口，刷新页面不会重新计时。此条件仅影响 Monitor 检查，B 回路仿真调优不依赖该窗口'
    :`${c.name}: ${c.error_code}`).join(' · ');
  const filteredChats=chats.filter(item=>item.title.toLocaleLowerCase().includes(chatQuery.trim().toLocaleLowerCase()));
  const sidebar=(close:()=>void)=>view==='chat'?(<section className="chat-rail" aria-label="对话列表"><div className="chat-rail-head"><h2>对话</h2><button disabled={sending||chatMutation} onClick={()=>newChat().then(()=>{setChatQuery('');close()}).catch(e=>setError(e.message))}>＋ 新对话</button></div><label className="session-filter"><span className="sr-only">搜索对话</span><input type="search" placeholder="搜索对话" value={chatQuery} onChange={e=>setChatQuery(e.target.value)}/></label><p className="rail-list-label">最近的对话 <span>{chats.length}</span></p><div className="chat-session-list">{filteredChats.length===0?<p className="chat-list-empty">{chatQuery?'没有匹配的对话。':'暂无对话，开始一段新对话。'}</p>:filteredChats.map(item=><div key={item.session_id} data-session-id={item.session_id} className={'chat-session-row'+(chatId===item.session_id?' selected':'')}>
        {renaming===item.session_id?<form className="chat-rename" onSubmit={e=>{e.preventDefault();void saveRename(item.session_id)}}><input autoFocus aria-label="会话名称" maxLength={80} value={renameDraft} onChange={e=>setRenameDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Escape')setRenaming(null)}}/><div><button type="submit" disabled={chatMutation}>保存</button><button type="button" onClick={()=>setRenaming(null)}>取消</button></div></form>:<><button className="chat-session-select" disabled={sending||chatMutation} onClick={()=>{setChatId(item.session_id);close()}} aria-current={chatId===item.session_id?'true':undefined}><strong>{item.title}</strong><small>{new Date(item.created_at).toLocaleDateString('zh-CN')}</small></button><button className="chat-session-more" aria-label={'管理会话：'+item.title} title="更多操作" disabled={sending||chatMutation} onClick={e=>{e.stopPropagation();const r=e.currentTarget.getBoundingClientRect();setMenu({id:item.session_id,x:r.right,y:r.bottom+4})}}><svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="4" cy="10" r="1.4" fill="currentColor"/><circle cx="10" cy="10" r="1.4" fill="currentColor"/><circle cx="16" cy="10" r="1.4" fill="currentColor"/></svg></button></>}
      </div>)}</div></section>):view==='console'?(<section className="history" aria-label="运行历史"><div className="section-heading"><h2>运行历史 <small>{runs.length}</small></h2><span className="history-hint">选择记录查看详情</span></div><div className="run-list">{runs.length===0?<p className="empty">暂无记录。配置模型后开始第一次检查。</p>:runs.map(r=><button key={r.run_id} className={'run '+(selected===r.run_id?'selected':'')} onClick={()=>{setSelected(r.run_id);close()}} aria-current={selected===r.run_id?'true':undefined}>
      <span className="run-top"><strong>{scenarios.find(s=>s.scenario_id===r.scenario_id)?.name||r.scenario_id}</strong><span className="run-indicator" aria-hidden="true"/></span><span className="run-status">{labels[r.status]}</span><small>{new Date(r.created_at).toLocaleString('zh-CN')} · {r.run_id.slice(0,8)}</small></button>)}</div>
      {cursor&&<button onClick={()=>action(async()=>{const r=await api<Schema['RunList']>('/runs?cursor='+encodeURIComponent(cursor));paged.current=true;setRuns(old=>[...old,...r.items]);setCursor(r.next_cursor)})}>加载更多</button>}</section>):<div className="sidebar-guide"><strong>{view==='tuning'?'先评测，再复核':'项目能力目录'}</strong><p>{view==='tuning'?'在四种负荷变化下比较 PID 候选。历史记录可在主区上方切换。':'查看 Agent 职责、技能和工具授权。可随时回到接待对话继续提问。'}</p></div>;
  return <WorkspaceShell embedded={!!onPlatformNavigate} view={view} onNavigate={navigate}
    title={view==='chat'?'接待对话':view==='console'?'Monitor 诊断工作台':view==='tuning'?'B 回路仿真调优':view==='agents'?'Agent 中心':view==='skills'?'Skill 中心':'Tool 中心'}
    subtitle={view==='chat'?(chats.find(item=>item.session_id===chatId)?.title||'接待 Agent · 问答与检查'):undefined}
    source={view==='tuning'?'SIMULATION · 简化仿真 / 未下发回路 A':'EXAMPLE · 示例数据 / 未连接真实设备'}
    sidebar={sidebar}
    status={<span className={'health '+(health?.ready?'ready':'')} role="status" aria-label={health?.ready?'服务就绪':'依赖未就绪'} title={health?.ready?'服务就绪':'依赖未就绪'}>{health?.ready?'● 服务就绪':'○ 依赖未就绪'}</span>}
    notices={<>{!health?.ready&&<details className="service-notice"><summary>{health?'部分服务暂不可用':'正在检查服务'}{historyReady?' · 历史记录仍可查看':''}</summary><p>{unavailable||'正在连接服务，请稍候。'}</p></details>}{error&&<div className="error" role="alert">{error}<button onClick={()=>setError('')}>关闭</button></div>}</>}
    overlays={<>    {menu&&<div className="session-menu" role="menu" style={{left:Math.min(menu.x,window.innerWidth-168),top:Math.min(menu.y,window.innerHeight-110)}} onPointerDown={e=>e.stopPropagation()}>{(()=>{const item=chats.find(chat=>chat.session_id===menu.id);return item?<><button role="menuitem" onClick={()=>{setRenaming(item.session_id);setRenameDraft(item.title);setMenu(null)}}>重命名</button><button role="menuitem" className="danger" onClick={()=>{setDeleteTarget(item);setMenu(null)}}>删除会话</button></>:null})()}</div>}
    {deleteTarget&&<dialog ref={deleteDialogRef} className="session-dialog" role="alertdialog" aria-labelledby="delete-chat-title" aria-describedby="delete-chat-description" onClose={()=>setDeleteTarget(null)} onCancel={e=>{if(chatMutation)e.preventDefault()}} onClick={e=>{if(e.target===e.currentTarget&&!chatMutation){const rect=e.currentTarget.getBoundingClientRect();if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom)e.currentTarget.close()}}}><h2 id="delete-chat-title">删除这段对话？</h2><p id="delete-chat-description">“{deleteTarget.title}”及其中的消息会永久删除。已产生的运行记录仍可在控制台查看。</p><div><button autoFocus onClick={()=>deleteDialogRef.current?.close()} disabled={chatMutation}>取消</button><button className="danger" disabled={chatMutation} onClick={()=>void deleteChat(deleteTarget)}>{chatMutation?'删除中…':'删除会话'}</button></div></dialog>}
</>}
  >
    {(view==='agents'||view==='skills'||view==='tools')&&<div className="page-scroll" key={view}><CatalogCenter kind={view} request={api}/></div>}
    {view==='tuning'&&<div className="page-scroll"><TuningLab request={api} modelReady={modelReady}/></div>}
    {view==='chat'&&<section className="chat-shell" aria-label="接待对话">
      <div className="chat-main">
        <div className="chat-reading"><div className="chat-transcript" ref={chatScroll.transcript} onScroll={chatScroll.onScroll} tabIndex={0} role="region" aria-label="对话消息"><div className="chat-messages" ref={chatScroll.content}>{messages.length===0?<div className="chat-welcome"><div className="chat-symbol" aria-hidden="true">◌</div><h2>从一个问题开始</h2><p>可以询问工作台如何运行，或直接说“检查 PID 状态”。检查由 Monitor 诊断；B 回路调优可从侧栏进入。</p><div className="chat-suggestions"><button disabled={sending||!chatReady} onClick={()=>send('这个工作台如何给出 PID 建议？')}>这个工作台如何给出建议？</button><button disabled={sending||!health?.ready} onClick={()=>send('请检查 PID 状态')}>检查 PID 状态</button></div></div>:messages.map(item=>{const linked=item.run_id?(run?.run_id===item.run_id?run:chatRuns[item.run_id]||runs.find(r=>r.run_id===item.run_id)):null;const trace=item.run_id?(run?.run_id===item.run_id?events:chatEvents[item.run_id]||[]):[];return <div className={'chat-entry '+item.role} key={item.message_id}><div className="chat-avatar" aria-hidden="true">{item.role==='user'?'我':item.role==='assistant'?'AI':'↳'}</div><div className="chat-entry-body"><div className="chat-entry-meta"><strong>{item.role==='user'?'你':item.role==='assistant'?'接待 Agent':'工作流'}</strong><time>{new Date(item.created_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}</time></div>{item.role==='assistant'?<div className="chat-markdown"><Markdown remarkPlugins={[remarkGfm]} components={{img:()=>null}}>{item.content.replaceAll('719 Control Lab', 'PID Control Lab')}</Markdown></div>:<p>{item.content}</p>}{item.run_id&&<div className="chat-run"><div className="chat-run-head"><strong>检查运行 · {item.run_id.slice(0,8)}</strong><span>{linked?labels[linked.status]:'读取中'}</span></div>{linked?.monitor_output&&<div className="chat-run-step"><b>Monitor</b><p>{linked.monitor_output.reason}</p></div>}{linked?.iterations.map(i=><div className="chat-run-step" key={i.iteration_id}><b>Control · 第 {i.iteration_no} 轮</b><p>{i.control_output?.reason||'生成候选中'}</p><small>{i.accepted===null?'待裁决':i.accepted?'接受为建议':'候选被拒绝'}</small></div>)}<details onToggle={e=>{if(e.currentTarget.open)loadChatEvents(item.run_id!)}}><summary>执行轨迹 · {trace.length?`${trace.length} 条事件`:'展开查看'}</summary><ol>{trace.map(e=><li key={e.event_id}><time>{new Date(e.created_at).toLocaleTimeString('zh-CN')}</time> {labels[e.event]||e.event}</li>)}</ol></details><button onClick={()=>openRun(item.run_id!)}>打开控制台与曲线 →</button></div>}</div></div>})}{sending&&<div className="chat-entry assistant"><div className="chat-avatar">AI</div><div className="chat-entry-body"><div className="chat-entry-meta"><strong>接待 Agent</strong></div><p className="chat-thinking" role="status">正在处理消息…</p></div></div>}</div></div>{chatScroll.showLatest&&<button className="jump-latest" onClick={chatScroll.scrollToLatest}>↓ 回到最新消息</button>}</div>
        <div className="chat-compose"><div className="chat-quick"><span>快捷检查</span><label>场景<select value={scenario} onChange={e=>setScenario(e.target.value)}>{scenarios.map(s=><option key={s.scenario_id} value={s.scenario_id}>{s.name}</option>)}</select></label><button disabled={sending||!health?.ready} onClick={()=>send('请检查 PID 状态',scenario)}>开始检查 →</button></div><form onSubmit={e=>{e.preventDefault();void send()}}><textarea ref={composerRef} rows={2} aria-label="发送消息给接待 Agent" maxLength={2000} placeholder="向接待 Agent 提问，或输入“检查 PID 状态”" value={draft} onChange={e=>setDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing&&e.keyCode!==229){e.preventDefault();void send()}}}/><div className="chat-compose-foot"><div className="chat-compose-info"><div className="chat-available-models"><span className="chat-model-label">可用模型</span>{availableModels===null?<span className="chat-model-empty">读取中…</span>:availableModels.length?availableModels.map(item=><span className="chat-model-tag" key={item.provider+'/'+item.model_id} title={item.provider+' / '+item.model_id}><span className={'chat-model-indicator '+(modelReady?'ready':'')} aria-hidden="true"/>{item.provider==='local-vllm'?'vLLM':item.provider} · {item.model_id}</span>):<span className="chat-model-empty">未配置</span>}</div><span className="chat-keyhint">Enter 发送 · Shift + Enter 换行</span></div><button className="primary" type="submit" disabled={!draft.trim()||sending||!chatReady}>发送消息 ↗</button></div></form></div>
      </div></section>}
    {view==='console'&&<section className="workspace" aria-label="运行控制台">
      <div className="console-main"><section className="toolbar" aria-label="发起检查"><div className="toolbar-intro"><div className="console-intro-title"><strong>发起一次检查</strong></div><span>Monitor 只作诊断；调优由操作员在 B 回路单独启动</span></div><div className="toolbar-actions"><label>演示场景<select value={scenario} onChange={e=>setScenario(e.target.value)}>{scenarios.map(s=><option key={s.scenario_id} value={s.scenario_id}>{s.name}</option>)}</select></label>
      <button className="primary" disabled={pending||!health?.ready} onClick={trigger}>开始检查 →</button></div></section>
      <div className="console-scroll"><article className="detail">{!run?<section className="panel empty-state"><div className="empty-graphic" aria-hidden="true"><svg viewBox="0 0 190 100" fill="none"><path d="M0 66H35L52 66L68 28L90 82L108 49L126 66H190" stroke="currentColor" strokeWidth="3" strokeLinejoin="round"/><circle cx="108" cy="49" r="6" fill="currentColor"/></svg></div><h2>{selected?'正在读取运行记录':'选择运行，查看分析与建议'}</h2><p>曲线、Agent 理由和裁决均来自后端持久化记录。</p></section>:<>
        <section className="panel summary"><div className="row summary-head"><div><p className="run-id">RUN {run.run_id.slice(0,8)} · EXAMPLE</p><h2>{labels[run.status]}{run.cancel_requested&&!terminal.has(run.status)?' · 取消处理中':''}</h2></div>
          {!terminal.has(run.status)?<button disabled={pending||run.cancel_requested} onClick={()=>action(()=>api('/runs/'+run.run_id+'/cancel',{schema_version:'1.0'}))}>取消运行</button>:<button disabled={pending||!health?.ready} onClick={()=>action(async()=>{const r=await api<Schema['TriggerResponse']>('/runs/'+run.run_id+'/retry',{schema_version:'1.0'});setSelected(r.run_id)})}>新建检查</button>}</div>
          {run.stop_reason==='SIMULATION_TUNING_SUGGESTED'?<div className="notice">Monitor 发现异常。可由操作员进入 B 回路仿真调优，先评测再决定后续动作。 <button onClick={()=>navigate('tuning')}>查看 B 回路调优</button></div>:run.error&&<p className="error">{run.error.code} · {run.error.message}</p>}
          <div className="pid-grid"><div><h3>实际 PID <small>冻结快照</small></h3><Pid pid={run.actual_pid?.pid}/></div><div><h3>本次推荐 PID <small>未下发</small></h3><Pid pid={run.best_pid}/></div></div>
          <div className="metrics">{[['稳态误差',run.best_metrics?.steady_state_error],['综合分数 J',run.best_score],['超调比例',run.best_metrics?.overshoot_ratio],['稳定时间 / s',run.best_metrics?.settling_time_s]].map(([label,v])=><div key={label as string}><small>{label}</small><strong>{n(v as number|null|undefined)}</strong></div>)}</div></section>
        <Diagnostics data={curves}/>
        <section className="panel"><h2>Agent 分析与候选</h2><div className="reason"><strong>Monitor</strong><p>{run.monitor_output?.reason||'等待分析结果'}</p>{run.monitor_output&&<small>{run.monitor_output.assessment} · 置信度 {run.monitor_output.confidence}</small>}</div>
          {run.iterations.map(i=><div className="iteration" key={i.iteration_id}><div className="row"><h3>Control · 第 {i.iteration_no} 轮</h3><span>{i.accepted===null?'待裁决':i.accepted?'接受为建议':'已拒绝'} · {i.reason_code}</span></div><Pid pid={i.candidate_pid}/><p>{i.control_output?.reason||'等待候选'}</p><small>{i.control_output?.expected_effect} · J {n(i.score)}</small></div>)}</section>
        <section className="panel"><h2>执行轨迹</h2><ol className="timeline">{events.map(e=><li key={e.event_id}><time>{new Date(e.created_at).toLocaleTimeString()}</time><span>{labels[e.event]||e.event}</span><small>{Object.entries(e.detail).map(([k,v])=>`${k}: ${v}`).join(' · ')}</small></li>)}</ol></section>
        {run.status==='MANUAL_REVIEW_REQUIRED'&&<section className="panel"><h2>人工复核</h2><textarea aria-label="复核意见" maxLength={2000} placeholder="记录意见（不授权实际下发）" value={review} onChange={e=>setReview(e.target.value)}/><button disabled={pending} onClick={()=>action(()=>api('/runs/'+run.run_id+'/manual-review',{schema_version:'1.0',action:'ACKNOWLEDGE',comment:review}))}>记录已阅</button></section>}
      </>}</article>
    {scheduler&&<section className="panel schedule"><div className="schedule-intro"><h2>定时检查</h2><p>忙时合并相同场景；关机期间不补跑。</p></div><div className="schedule-controls"><label className="toggle"><input type="checkbox" checked={scheduler.enabled} onChange={e=>setScheduler({...scheduler,enabled:e.target.checked})}/>启用</label><label>间隔（秒）<input type="number" min={30} max={86400} value={scheduler.interval_seconds} onChange={e=>setScheduler({...scheduler,interval_seconds:Number(e.target.value)})}/></label><label>演示场景<select value={scheduler.scenario_id} onChange={e=>setScheduler({...scheduler,scenario_id:e.target.value})}>{scenarios.map(s=><option key={s.scenario_id} value={s.scenario_id}>{s.name}</option>)}</select></label><button disabled={pending} onClick={()=>action(()=>api('/scheduler',scheduler,'PUT'))}>保存配置</button></div></section>}</div></div></section>}
    </WorkspaceShell>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><OfficeApp renderLegacy={(view,onNavigate)=><App key="legacy" initialView={view} onPlatformNavigate={onNavigate}/>}/></React.StrictMode>);
