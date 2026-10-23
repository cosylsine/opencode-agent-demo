import React, {useEffect, useRef, useState} from 'react';
import * as echarts from 'echarts/core';
import {LineChart} from 'echarts/charts';
import {GridComponent, LegendComponent, MarkLineComponent, TooltipComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import type {components} from './api.generated';
import './tuning.css';

echarts.use([LineChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

type Event = {at_s:number;until_s:number|null;target_kw:number;kind:string};
type PlantState = components['schemas']['PlantState'];
type Aging = Required<components['schemas']['ComponentAging']>;
type AgingKey = keyof Aging;
type Result = {safe:boolean;score:number;worst_scenario:string;scenarios:Record<string,{safe:boolean;score:number;violations:string[];components:Record<string,number>}>};
type Trial = {trial_id:string;iteration:number;pid:Record<string,number>;result:Result;accepted:boolean;reason:string;agent_output?:{reason:string;expected_effect:string}|null};
type Run = {run_id:string;status:string;created_at:string;source_kind:string;model_version:string;scenario_version:string;plant_state?:PlantState;plant_state_hash?:string;search_reviews?:{iteration:number;reason:string}[];events:Record<string,Event[]>;assumptions:Record<string,unknown>;baseline_pid:Record<string,number>;best_pid:Record<string,number>|null;best_trial_id:string|null;trials:Trial[];rejections:{iteration:number;reason:string;agent_output?:{reason:string}}[];max_iterations:number;error:string|null;cancel_requested:boolean};
type Trace = {points:Record<string,number>[]};
type Request = <T>(path:string,body?:unknown,method?:string)=>Promise<T>;
const scenarioNames:Record<string,string>={step_up:'负荷上升阶跃',step_down:'负荷下降阶跃',ramp_up:'负荷上升斜坡',repeated:'负荷多次升降'};
const statusNames:Record<string,string>={QUEUED:'排队中',BASELINE_RUNNING:'基线仿真中',SEARCHING:'准备下一轮',AGENT_RUNNING:'Control 推理中',TRIAL_RUNNING:'候选仿真中',COMPLETED:'调优结束',NO_SAFE_CANDIDATE:'无安全候选',AGENT_ABSTAINED:'Agent 已停止继续提案',FAILED:'运行失败',CANCELLED:'已取消'};
const reasonNames:Record<string,string>={IMPROVED:'已改善，接受为当时最佳',NO_IMPROVEMENT:'未达到改善阈值',SAFETY_VIOLATION:'未通过安全评测'};
const terminal=new Set(['COMPLETED','NO_SAFE_CANDIDATE','AGENT_ABSTAINED','FAILED','CANCELLED']);
const agingComponents:{key:AgingKey;name:string;effect:string}[]=[
  {key:'feedwater_pump',name:'给水泵',effect:'扬程下降，转速与流量响应变慢'},
  {key:'feedwater_valve',name:'给水调节阀',effect:'通流能力下降，开度响应变慢'},
  {key:'boiler',name:'锅炉',effect:'有效热效率下降，热响应变慢'},
  {key:'turbine',name:'汽轮机',effect:'蒸汽比功下降，流量响应变慢'},
  {key:'condenser',name:'凝汽器',effect:'凝汽背压升高，循环比功下降'},
];
function uniformAging(value:number):Aging{return {feedwater_pump:value,feedwater_valve:value,boiler:value,turbine:value,condenser:value}}
const nominalState:PlantState={name:'标称回路',aging:uniformAging(0)};
const agingPresets=[{name:'标称回路',value:0},{name:'轻度老化',value:0.25},{name:'中度老化',value:0.5},{name:'重度老化',value:0.85}];
const channels=[
  {name:'汽轮机功率',unit:'kW',actual:'power_kw',target:'target_power_kw'},
  {name:'给水流量',unit:'kg/s',actual:'feed_flow_kg_s',target:'feed_target_kg_s'},
  {name:'泵速',unit:'额定占比',actual:'pump_speed_fraction'},
  {name:'汽包水位',unit:'容积占比',actual:'drum_level_fraction'},
  {name:'主蒸汽压力',unit:'MPa',actual:'steam_pressure_mpa'},
];

function ResponseChart({baseline,candidate,events}:{baseline:Record<string,number>[];candidate:Record<string,number>[];events:Event[]}){
  const el=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(!el.current||!baseline.length)return;const chart=echarts.init(el.current);
    const grids=channels.map((_,i)=>({left:74,right:24,top:30+i*142,height:98}));
    const axes=channels.map((_,i)=>({type:'value',gridIndex:i,min:0,max:420,name:i===4?'时间 / s':'',nameLocation:'middle',nameGap:27,axisLabel:{show:i===4},axisLine:{show:false},splitLine:{show:false}}));
    const yaxes=channels.map((c,i)=>({type:'value',gridIndex:i,name:`${c.name} / ${c.unit}`,nameLocation:'middle',nameGap:51,scale:true,axisLabel:{fontSize:10},splitLine:{lineStyle:{color:'#e5ebea'}}}));
    const series:unknown[]=[];
    channels.forEach((c,i)=>{
      const common={type:'line',xAxisIndex:i,yAxisIndex:i,showSymbol:false,animation:false};
      series.push({...common,name:`${c.name} · 基线`,lineStyle:{color:'#9baab3',width:1.5,type:'dashed'},data:baseline.map(p=>[p.time_s,p[c.actual]])});
      series.push({...common,name:`${c.name} · 所选`,lineStyle:{color:'#087e7c',width:2.1},data:candidate.map(p=>[p.time_s,p[c.actual]]),
        ...(i===0?{markLine:{silent:true,symbol:'none',label:{formatter:'负荷变化',fontSize:10},lineStyle:{color:'#c47a32',type:'dashed'},data:events.map(e=>({xAxis:e.at_s}))}}:{})});
      if(c.target)series.push({...common,name:`${c.name} · 目标`,lineStyle:{color:'#ba7a3d',width:1,type:'dotted'},data:candidate.map(p=>[p.time_s,p[c.target!]])});
    });
    chart.setOption({grid:grids,xAxis:axes,yAxis:yaxes,series,tooltip:{trigger:'axis',axisPointer:{type:'cross',link:[{xAxisIndex:'all'}]}},
      legend:{show:false},animation:false});
    const resize=new ResizeObserver(()=>chart.resize());resize.observe(el.current);return()=>{resize.disconnect();chart.dispose()};
  },[baseline,candidate,events]);
  return <div ref={el} className="tuning-chart" role="img" aria-label="同一时间轴上的汽轮机功率、给水流量、泵速、汽包水位和主蒸汽压力；灰线为基线，绿线为所选参数，橙线为目标与负荷事件"/>;
}

export function TuningLab({request,modelReady}:{request:Request;modelReady:boolean}){
  const [runs,setRuns]=useState<Run[]>([]),[selected,setSelected]=useState<string|null>(null),[run,setRun]=useState<Run|null>(null);
  const [scenario,setScenario]=useState('step_up'),[trialId,setTrialId]=useState<string|null>(null);
  const [baseline,setBaseline]=useState<Record<string,number>[]>([]),[candidate,setCandidate]=useState<Record<string,number>[]>([]);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [plantState,setPlantState]=useState<PlantState>(nominalState);
  const aging={...uniformAging(0),...plantState.aging};
  useEffect(()=>{let alive=true;async function poll(){try{const result=await request<{items:Run[]}>('/tuning-runs');if(!alive)return;setRuns(result.items);setSelected(previous=>previous||result.items[0]?.run_id||null)}catch(e){if(alive)setError((e as Error).message)}}void poll();const timer=setInterval(poll,2500);return()=>{alive=false;clearInterval(timer)}},[request]);
  useEffect(()=>{if(!selected){setRun(null);return}let alive=true;async function poll(){try{const value=await request<Run>('/tuning-runs/'+selected);if(alive){setRun(value);setTrialId(current=>current&&value.trials.some(t=>t.trial_id===current)?current:(value.best_trial_id||value.trials.at(-1)?.trial_id||null))}}catch(e){if(alive)setError((e as Error).message)}}void poll();const timer=setInterval(poll,1500);return()=>{alive=false;clearInterval(timer)}},[selected,request]);
  const base=run?.trials.find(t=>t.iteration===0),chosen=run?.trials.find(t=>t.trial_id===trialId);
  const decisions=run?[
    ...run.trials.map(trial=>({kind:'trial' as const,iteration:trial.iteration,trial})),
    ...run.rejections.map((rejection,index)=>({kind:'rejection' as const,iteration:rejection.iteration,rejection,index})),
  ].sort((a,b)=>a.iteration-b.iteration):[];
  const completedIterations=run?run.trials.filter(trial=>trial.iteration>0).length+run.rejections.length:0;
  useEffect(()=>{if(!run||!base||!chosen){setBaseline([]);setCandidate([]);return}let alive=true;
    const path=(trial:string)=>`/tuning-runs/${run.run_id}/trials/${trial}/scenarios/${scenario}/trace`;
    Promise.all([request<Trace>(path(base.trial_id)),request<Trace>(path(chosen.trial_id))]).then(([a,b])=>{if(alive){setBaseline(a.points);setCandidate(b.points)}}).catch(e=>alive&&setError((e as Error).message));return()=>{alive=false};
  },[run?.run_id,base?.trial_id,chosen?.trial_id,scenario,request]);
  async function start(){setBusy(true);setError('');try{const item=await request<Run>('/tuning-runs',{schema_version:'1.0',plant_state:plantState});setRuns(old=>[item,...old.filter(r=>r.run_id!==item.run_id)]);setSelected(item.run_id);setTrialId(null);setScenario('step_up')}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  function randomizeState(){const values=uniformAging(0);for(const {key} of agingComponents)values[key]=Math.floor(Math.random()*71)/100;setPlantState({name:'随机老化回路',aging:values})}
  function updateAging(key:AgingKey,value:number){setPlantState(previous=>({name:agingPresets.some(preset=>preset.name===previous.name)?'自定义老化回路':previous.name,aging:{...uniformAging(0),...previous.aging,[key]:value}}))}
  async function cancel(){if(!run)return;setBusy(true);setError('');try{await request(`/tuning-runs/${run.run_id}/cancel`,{schema_version:'1.0'})}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  return <section className="tuning-lab" aria-label="B 回路仿真调优">
    <div className="tuning-toolbar"><div><strong>B 回路 · 汽水循环仿真调优</strong><p>同一组给水泵 PID 通过四种负荷变化评测。SIMULATION · 简化模型 · 未下发回路 A</p></div><button className="primary" onClick={start} disabled={busy||!plantState.name?.trim()||!modelReady||runs.some(r=>!terminal.has(r.status))}>开始仿真调优</button></div>
    {error&&<p className="tuning-error" role="alert">{error}</p>}
    <div className="tuning-card tuning-state-editor">
      <div className="tuning-state-heading"><div><h3>创建回路状态</h3><p>设置各部件老化程度，再开始仿真调优。0% 为标称，100% 为模型最大老化强度。</p></div><button disabled={busy} onClick={randomizeState}>随机生成状态</button></div>
      <div className="tuning-state-presets" aria-label="老化预设">{agingPresets.map(preset=><button key={preset.name} disabled={busy} aria-pressed={Object.values(aging).every(value=>value===preset.value)} onClick={()=>setPlantState({name:preset.name,aging:uniformAging(preset.value)})}>{preset.name}</button>)}</div>
      <label className="tuning-state-name">状态名称<input value={plantState.name||''} maxLength={80} disabled={busy} onChange={e=>setPlantState({...plantState,name:e.target.value})} placeholder="例如：检修前的 B 回路"/></label>
      <div className="tuning-aging-grid">{agingComponents.map(component=><label className="tuning-aging-control" key={component.key}><span><b>{component.name}</b><output>{Math.round(aging[component.key]*100)}%</output></span><input type="range" min={0} max={100} step={1} aria-label={`${component.name}老化程度`} value={Math.round(aging[component.key]*100)} disabled={busy} onChange={e=>updateAging(component.key,Number(e.target.value)/100)}/><small>{component.effect}</small></label>)}</div>
      <p className="tuning-state-help">一次调优中的基线和全部候选使用同一状态。老化强度不等同于设备年限；严重老化可能无法仅靠 PID 通过安全评测。</p>
    </div>
    <label className="tuning-history-select">调优记录<select aria-label="调优记录" value={selected||''} disabled={!runs.length} onChange={e=>{setSelected(e.target.value);setTrialId(null)}}>{!runs.length&&<option value="">暂无记录，启动后将先评测初始 PID</option>}{runs.map(item=><option key={item.run_id} value={item.run_id}>{item.plant_state?.name||'标称回路'} · {statusNames[item.status]||item.status} · {new Date(item.created_at).toLocaleString('zh-CN')} · {item.run_id.slice(0,8)}</option>)}</select></label>
    <div className="tuning-layout">
      <div className="tuning-content">{run?<>
        <div className="tuning-card tuning-summary"><div><h2>{statusNames[run.status]||run.status}</h2><p>{run.model_version} · {run.scenario_version} · 第 {completedIterations}/{run.max_iterations} 轮</p></div>{!terminal.has(run.status)&&<button disabled={busy||run.cancel_requested} onClick={cancel}>{run.cancel_requested?'取消处理中':'取消调优'}</button>}</div>
        <div className="tuning-card tuning-saved-state"><div className="tuning-state-heading"><h3>本次回路状态 · {run.plant_state?.name||'标称回路'}</h3><button disabled={busy} onClick={()=>setPlantState(run.plant_state||nominalState)}>复用此状态</button></div><div className="tuning-aging-summary">{agingComponents.map(component=><span key={component.key}>{component.name}<strong>{Math.round((run.plant_state?.aging?.[component.key]||0)*100)}%</strong></span>)}</div><p>此状态已固定。上方配置用于下一次调优；不同回路状态的分数分别比较。</p></div>
        {run.status==='AGENT_ABSTAINED'&&<p className="tuning-note" role="status">Agent 已停止提出新候选，当前最佳合规 PID 与已完成的评测结果均已保留。停止提案不会撤销此前的改善，也不代表已证明参数最优。</p>}
        {!!run.search_reviews?.length&&<p className="tuning-note" role="status">Agent 曾在探索初期停止提案，已安排一次复核。复核不增加仿真轮次；后续候选仍须通过相同的安全和改善判定。</p>}
        {run.error&&<p className={run.status==='FAILED'||run.status==='NO_SAFE_CANDIDATE'?'tuning-error':'tuning-note'} role="status">{run.status==='AGENT_ABSTAINED'&&'停止原因（Agent 判断）：'}{run.error}</p>}
        <div className="tuning-card"><h3>统一参数与结果</h3><div className="tuning-comparison"><div><small>初始 PID</small><strong>{JSON.stringify(run.baseline_pid)}</strong></div><div><small>最佳合规 PID · 未下发</small><strong>{run.best_pid?JSON.stringify(run.best_pid):'暂无合规参数'}</strong></div></div><p>评测规则：全部工况先通过安全边界，再按流量、水位、压力、功率和泵速变化综合评分；总分为平均分 70%＋最差工况分 30%，越低越好。</p></div>
        <div className="tuning-card"><h3>负荷事件</h3><div className="tuning-events">{Object.entries(run.events).map(([key,events])=><div key={key}><b>{scenarioNames[key]||key}</b>{events.map((event,i)=><span key={i}>{event.at_s}s{event.until_s?`–${event.until_s}s`:''} · {event.kind==='ramp'?'斜坡':'阶跃'}至 {event.target_kw} kW</span>)}</div>)}</div></div>
        <div className="tuning-card"><div className="tuning-controls"><h3>响应对比</h3><label>工况 <select value={scenario} onChange={e=>setScenario(e.target.value)}>{Object.keys(run.events).map(key=><option key={key} value={key}>{scenarioNames[key]||key}</option>)}</select></label><label>候选 <select value={trialId||''} onChange={e=>setTrialId(e.target.value)}>{run.trials.map(t=><option key={t.trial_id} value={t.trial_id}>{t.iteration===0?'初始参数':`第 ${t.iteration} 轮`}{run.best_trial_id===t.trial_id?' · 最佳':''}</option>)}</select></label></div>
          <p className="tuning-legend"><span>━━ 所选</span><span>┄┄ 初始</span><span>┄ 目标与负荷事件</span></p>{baseline.length&&candidate.length?<ResponseChart baseline={baseline} candidate={candidate} events={run.events[scenario]||[]}/>:<p>等待仿真时序。</p>}
          {chosen&&<div className="tuning-metrics"><strong>{chosen.result.safe?'全部工况安全':'存在安全越界'} · 综合分 {chosen.result.score.toFixed(5)} · 最差工况 {scenarioNames[chosen.result.worst_scenario]}</strong>{Object.entries(chosen.result.scenarios).map(([key,value])=><span key={key}>{scenarioNames[key]}：{value.score.toFixed(5)}{value.violations.length?` · 越界 ${value.violations.join('、')}`:''}</span>)}</div>}</div>
        <div className="tuning-card"><h3>Control 候选与裁决</h3><p>候选须通过全部工况的安全评测，且综合分比当前最佳降低超过 0.00001，才接受为新的最佳参数；尚无合规参数时，首个通过安全评测的结果用于建立初始最佳。</p>{decisions.map(entry=>entry.kind==='trial'?<div className="tuning-iteration" data-iteration={entry.iteration} data-decision="trial" key={entry.trial.trial_id}><b>{entry.iteration===0?'基线':`第 ${entry.iteration} 轮`} · {entry.iteration===0&&entry.trial.accepted?'建立初始最佳':reasonNames[entry.trial.reason]||entry.trial.reason} · {entry.trial.result.score.toFixed(5)}</b><span>{JSON.stringify(entry.trial.pid)}</span>{entry.trial.agent_output?.reason&&<p>{entry.trial.agent_output.reason}</p>}</div>:<div className="tuning-iteration" data-iteration={entry.iteration} data-decision="rejection" key={'reject-'+entry.iteration+'-'+entry.index}><b>第 {entry.iteration} 轮 · 参数提案被拒绝</b><span>{entry.rejection.reason}</span>{entry.rejection.agent_output?.reason&&<p>{entry.rejection.agent_output.reason}</p>}</div>)}</div>
      </>:<div className="tuning-card"><h2>选择一条调优记录</h2><p>记录包含负荷事件、逐工况响应和每轮候选的裁决依据。</p></div>}</div></div>
  </section>;
}
