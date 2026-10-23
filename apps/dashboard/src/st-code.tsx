import React, {useEffect, useRef, useState} from 'react';
import {officeApi, type Schema} from './office-api';
import './st-code.css';

type Run = Schema['STRun'];
type Action = Schema['STStart']['actions'][number];
const actions: {id:Action; label:string; description:string}[] = [
  {id:'comment', label:'代码注释', description:'解释变量、逻辑与扫描行为'},
  {id:'test', label:'测试用例', description:'正常、边界、异常与回归'},
  {id:'review', label:'问题检查', description:'原文行号、证据与风险'},
  {id:'fix', label:'修复建议', description:'修复已确认问题，保留差异'},
];
const statusLabel:Record<string,string> = {queued:'排队中', running:'模型处理中', completed:'已完成 · 待工程验证', failed:'处理失败', interrupted:'已中断', cancelled:'已取消'};
const active = (status:string) => status==='queued'||status==='running';
const stageLabel:Record<string,string>={review:'缺陷审查',annotation:'添加注释',repair:'修复代码',tests:'设计测试',validation:'产物校验'};
const example = `FUNCTION_BLOCK FB_Ratio
VAR_INPUT
    Enable : BOOL;
    Numerator : REAL;
    Denominator : REAL;
END_VAR
VAR_OUTPUT
    Ratio : REAL;
    Valid : BOOL;
END_VAR

IF Enable THEN
    Ratio := Numerator / Denominator;
    Valid := TRUE;
END_IF;
END_FUNCTION_BLOCK
`;

export function STWorkspace(){
  const [code,setCode]=useState(''), [filename,setFilename]=useState('source.st');
  const [title,setTitle]=useState('ST 代码优化'), [dialect,setDialect]=useState<Schema['STStart']['dialect']>('generic');
  const [instructions,setInstructions]=useState(''), [selectedActions,setSelectedActions]=useState<Action[]>(actions.map(a=>a.id));
  const [history,setHistory]=useState<Schema['STRunSummary'][]>([]), [selected,setSelected]=useState<string|null>(()=>location.hash.split('/')[1]||null);
  const [run,setRun]=useState<Run|null>(null), [error,setError]=useState(''), [fileNotice,setFileNotice]=useState('');
  const [busy,setBusy]=useState(false), [ready,setReady]=useState<boolean|null>(null), [tab,setTab]=useState('overview');
  const [submitting,setSubmitting]=useState(false);
  const [diff,setDiff]=useState('');
  const submission=useRef<{body:string;key:string}|null>(null);
  const runId=useRef(selected);
  runId.current=selected;

  async function refresh(){
    const [runs,models]=await Promise.all([officeApi<Schema['STRuns']>('/st-runs'),officeApi<Schema['AvailableModels']>('/models')]);
    setHistory(runs.items);setReady(models.items.some(model=>model.ready));
    setSelected(old=>old||runs.items[0]?.run_id||null);
  }
  useEffect(()=>{let alive=true;
    const load=async()=>{try{const [runs,models]=await Promise.all([officeApi<Schema['STRuns']>('/st-runs'),officeApi<Schema['AvailableModels']>('/models')]);if(alive){setHistory(runs.items);setReady(models.items.some(m=>m.ready));setSelected(old=>old||runs.items[0]?.run_id||null)}}catch(e){if(alive)setError((e as Error).message)}};
    void load();const timer=setInterval(load,5000);return()=>{alive=false;clearInterval(timer)};
  },[]);
  useEffect(()=>{setRun(null);setDiff('');setTab('overview');if(!selected)return;let alive=true;
    const poll=async()=>{try{const value=await officeApi<Run>('/st-runs/'+selected);if(alive)setRun(value)}catch(e){if(alive)setError((e as Error).message)}};
    void poll();const timer=setInterval(poll,1800);return()=>{alive=false;clearInterval(timer)};
  },[selected]);
  useEffect(()=>{if(tab!=='diff'||!run?.result?.fixed_code)return;let alive=true;
    fetch(`/api/v1/st-runs/${run.run_id}/artifacts/changes.diff`).then(async r=>{if(!r.ok)throw new Error('差异文件读取失败');return r.text()}).then(value=>{if(alive)setDiff(value||'有效代码没有变化。')}).catch(e=>{if(alive)setError(e.message)});
    return()=>{alive=false};
  },[tab,run?.run_id,run?.status]);

  const choose=(id:string)=>{setSelected(id);historyReplace(id)};
  function historyReplace(id:string){window.history.replaceState(null,'','#st-code/'+id)}
  async function upload(file:File){
    setError('');setFileNotice('');
    try{
      if(!/\.(st|scl|txt)$/i.test(file.name))throw new Error('请选择 .st、.scl 或 .txt 源码文件');
      if(file.size>48000)throw new Error('文件过大，请拆分为最多 12000 字符、600 行的代码');
      const bytes=await file.arrayBuffer();let value:string, encoding='UTF-8';
      try{value=new TextDecoder('utf-8',{fatal:true}).decode(bytes)}catch{value=new TextDecoder('gb18030',{fatal:true}).decode(bytes);encoding='GB18030'}
      if(!value.trim()||value.includes('\0'))throw new Error('文件为空或不是纯文本源码');
      if(value.length>12000||value.split(/\r?\n/).length>600)throw new Error('源码超过 12000 字符或 600 行，请分段提交');
      setCode(value);setFilename(file.name);setFileNotice(`已读取 ${file.name}（${encoding}），可继续编辑。`);
    }catch(e){setError((e as Error).message)}
  }
  async function start(e:React.FormEvent){
    e.preventDefault();setError('');setBusy(true);setSubmitting(true);
    const body={schema_version:'1.0',title,filename,code,dialect,instructions,actions:selectedActions};
    const serialized=JSON.stringify(body);
    if(submission.current?.body!==serialized)submission.current={body:serialized,key:crypto.randomUUID?.()??String(Date.now())};
    try{
      const value=await officeApi<Run>('/st-runs',body,'POST',submission.current!.key);
      submission.current=null;choose(value.run_id);setRun(value);await refresh();
    }catch(e){setError((e as Error).message)}finally{setBusy(false);setSubmitting(false)}
  }
  async function cancel(){
    if(!run)return;setBusy(true);setError('');
    try{const value=await officeApi<Run>(`/st-runs/${run.run_id}/cancel`,{});if(runId.current===value.run_id)setRun(value);await refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  async function retry(){
    if(!run)return;setBusy(true);setSubmitting(true);setError('');
    const body={...run.request,retry_from:run.run_id};const serialized=JSON.stringify(body);
    if(submission.current?.body!==serialized)submission.current={body:serialized,key:crypto.randomUUID?.()??String(Date.now())};
    try{const value=await officeApi<Run>('/st-runs',body,'POST',submission.current!.key);submission.current=null;choose(value.run_id);setRun(value);await refresh()}
    catch(e){setError((e as Error).message)}finally{setBusy(false);setSubmitting(false)}
  }
  const ownActive=history.some(r=>active(r.status));
  const result=run?.result;
  const category:Record<string,string>={normal:'正常',boundary:'边界',abnormal:'异常',regression:'回归',scan_cycle:'扫描周期'};
  const disposition:Record<string,string>={fixed:'已给出修复',not_fixed:'未修复',needs_context:'待补充上下文'};
  return <div className="st-workspace">
    <section className="st-intro"><div><span className="of-eyebrow">平台定制 · 工程代码</span><h2>让 ST 代码更清楚、更可靠</h2><p>注释、测试设计、缺陷审查与修复建议。任务和代码仅对当前账号可见。</p></div><span className="st-version">ST / SCL</span></section>
    {error&&<p className="error" role="alert">{error}<button onClick={()=>setError('')}>关闭</button></p>}
    {ready===false&&<p className="of-callout">模型暂未就绪，仍可编辑代码和查看历史结果。服务恢复后可提交任务。</p>}
    <div className="st-columns">
      <form className="st-panel st-input" onSubmit={start}>
        <div className="st-heading"><h3>提交代码</h3><button type="button" className="of-link" onClick={()=>{setCode(example);setFilename('FB_Ratio.st');setTitle('比值功能块审查');setFileNotice('已载入含除零与输出保持问题的示例，可直接提交。')}}>载入示例</button></div>
        <div className="st-fields"><label>任务名称<input required maxLength={80} value={title} onChange={e=>setTitle(e.target.value)}/></label><label>ST 方言<select value={dialect} onChange={e=>setDialect(e.target.value as typeof dialect)}><option value="generic">通用 IEC ST</option><option value="codesys">CODESYS</option><option value="siemens">Siemens SCL</option><option value="twincat">TwinCAT ST</option></select></label></div>
        <div className="st-file-row"><label className="st-upload">上传源码<input type="file" accept=".st,.scl,.txt" aria-label="上传 ST 源码" onChange={e=>{const file=e.target.files?.[0];if(file)void upload(file);e.target.value=''}}/></label><span title={filename}>{filename}</span></div>
        {fileNotice&&<p className="st-file-notice" role="status">{fileNotice}</p>}
        <label>ST 源代码<textarea className="st-editor" required spellCheck={false} maxLength={12000} value={code} onChange={e=>setCode(e.target.value)} placeholder="粘贴 PROGRAM、FUNCTION 或 FUNCTION_BLOCK 代码…"/></label>
        <div className="st-code-meta"><span>{code.length.toLocaleString()} / 12,000 字符</span><span>{code?code.split('\n').length:0} / 600 行</span></div>
        <fieldset className="st-actions"><legend>本次操作</legend>{actions.map(action=><label key={action.id} className={selectedActions.includes(action.id)?'is-selected':undefined}><input type="checkbox" checked={selectedActions.includes(action.id)} onChange={e=>setSelectedActions(old=>e.target.checked?[...old,action.id]:old.filter(id=>id!==action.id))}/><span><strong>{action.label}</strong><small>{action.description}</small></span></label>)}</fieldset>
        <label>工程上下文与要求（选填）<textarea rows={3} maxLength={2000} value={instructions} onChange={e=>setInstructions(e.target.value)} placeholder="例如：扫描周期 100 ms，输入单位、初始状态、输出保持要求、厂商版本…"/></label>
        <p className="st-help">修复操作包含缺陷检查。修复版单独保存；注释版保留原代码逻辑。每次最多 12000 字符、600 行。</p>
        <button className="of-primary st-submit" disabled={busy||ready!==true||ownActive||!code.trim()||!selectedActions.length}>{busy?'正在提交…':ownActive?'已有任务处理中':'开始优化'}</button>
      </form>
      <div className="st-output-column">
        <section className="st-panel"><div className="st-heading"><h3>任务与结果</h3><span className="st-badge">固定工作流 v1</span></div>
          <label>历史任务<select aria-label="ST 历史任务" disabled={submitting} value={selected??''} onChange={e=>choose(e.target.value)}><option value="" disabled>尚无任务</option>{history.map(item=><option key={item.run_id} value={item.run_id}>{item.title} · {statusLabel[item.status]} · {new Date(item.created_at).toLocaleString('zh-CN')}</option>)}</select></label>
          {submitting&&<p role="status">正在提交新任务…</p>}
          {!submitting&&!run&&<div className="st-empty"><span aria-hidden="true">{'{ }'}</span><h3>{selected?'正在加载任务':'从一段代码开始'}</h3><p>提交后会在这里展示注释、问题证据、测试用例和修复差异。</p></div>}
          {!submitting&&run&&<><div className="st-run-state" role="status"><span className={'st-status st-status-'+run.status}>{statusLabel[run.status]}</span>{active(run.status)&&<button disabled={busy} onClick={()=>void cancel()}>取消任务</button>}</div>
            {active(run.status)&&<p>{run.stage?`当前阶段：${stageLabel[run.stage]}。`:'正在准备任务。'}处理需要几分钟，可离开本页后再查看结果。</p>}
            {run.validation_feedback.length>0&&<details className="st-validation"><summary>查看阶段校验与纠正记录</summary><ul>{run.validation_feedback.map((message,i)=><li key={i}>{message}</li>)}</ul></details>}
            {run.error&&<p className="of-callout">{run.error}</p>}
            {['failed','cancelled','interrupted'].includes(run.status)&&<button disabled={busy||ready!==true||ownActive} onClick={()=>void retry()}>重试原任务</button>}
            {run.reused_stages?.length>0&&<p>已复用并重新校验：{run.reused_stages.map(stage=>stageLabel[stage]||stage).join('、')}。</p>}
            <div className="st-tabs" role="tablist" aria-label="ST 结果视图">{[['overview','概览'],['original','原始代码'],...(result?.annotated_code?[['comment','注释代码']]:[]),...(result?.tests.length?[['test','测试用例']]:[]),...(result&&run.request.actions.some(action=>action==='review'||action==='fix')?[['review',`问题 (${result.bugs.length})`]]:[]),...(result?.fixed_code?[['fix','修复代码'],['diff','代码差异']]:[])].map(([id,label])=><button role="tab" id={'st-tab-'+id} aria-selected={tab===id} aria-controls="st-result-panel" key={id} onClick={()=>setTab(id)}>{label}</button>)}</div>
            <div className="st-result" role="tabpanel" id="st-result-panel" aria-labelledby={'st-tab-'+tab}>
              {tab==='overview'&&<><h3>{run.request.title}</h3>{result&&<p className="st-summary">{result.summary}</p>}<dl className="st-facts"><dt>原始文件</dt><dd>{run.request.filename}</dd><dt>执行模型</dt><dd>{run.model_id}</dd><dt>所选操作</dt><dd>{actions.filter(a=>run.request.actions.includes(a.id)).map(a=>a.label).join('、')}</dd><dt>原文 SHA-256</dt><dd className="st-hash">{run.source_sha256}</dd></dl>{run.checks&&<div className="of-callout"><strong>产物检查</strong><p>{run.checks.annotation_tokens_preserved?'注释版有效词元与原文一致。':''}{run.checks.fixed_lexically_valid?'修复版通过基础词法检查。':''}{run.checks.note}</p></div>}{result&&<><h3>假设与待确认项</h3><ul>{result.assumptions.map((text,i)=><li key={i}>{text}</li>)}</ul></>}</>}
              {tab==='original'&&<Code text={run.request.code}/>}
              {tab==='comment'&&result?.annotated_code&&<Code text={result.annotated_code}/>}
              {tab==='fix'&&result?.fixed_code&&<><p>修复建议尚未经过 PLC 编译和执行验证，请结合工程环境复核。</p><Code text={result.fixed_code}/></>}
              {tab==='diff'&&<Code text={diff||'正在读取差异…'}/>}
              {tab==='review'&&result&&<>{!result.bugs.length&&<p>本轮未报告问题，不代表代码不存在缺陷。</p>}{result.bugs.map(bug=><article className="st-case" key={bug.id}><div className="st-heading"><h3>{bug.id} · {bug.title}</h3><span className="st-badge">{{high:'高',medium:'中',low:'低'}[bug.severity]}风险</span></div><p>原文第 {bug.line_start}–{bug.line_end} 行 · {bug.confidence==='confirmed'?'已确认缺陷':'潜在问题'} · {disposition[bug.disposition]}</p><Code text={bug.evidence}/><p>{bug.explanation}</p><p>建议：{bug.suggestion}</p><p>处置：{bug.fix_explanation}</p></article>)}</>}
              {tab==='test'&&result&&<><p className="of-callout">以下为测试设计和 ST 测试草稿，尚未执行。AllPassed 仅汇总 ST 检查；人工与时钟用例需在目标环境单独验证。</p>{result.tests.map(test=><article className="st-case" key={test.id}><h3>{test.id} · {test.title} <span className="st-badge">{category[test.category]}</span></h3><p>执行方式：{test.execution==='manual'?'人工 / 目标 PLC 时钟验证':'ST 检查草稿'}</p>{test.adaptation_note&&<p>适配要求：{test.adaptation_note}</p>}<p>前提：{test.preconditions}</p><ol>{test.steps.map((step,i)=><li key={i}>{step}</li>)}</ol><p><strong>预期：</strong>{test.expected}</p>{test.related_bug_ids.length>0&&<small>关联问题：{test.related_bug_ids.join('、')}</small>}</article>)}{result.test_code&&<details><summary>查看 ST 测试草稿</summary><Code text={result.test_code}/></details>}</>}
            </div>
            {run.artifacts.length>0&&<div className="st-downloads" aria-label="ST 产物下载"><h3>下载产物</h3>{run.artifacts.map(file=><a key={file.name} href={file.download_url} download>{file.name} ↓</a>)}</div>}
          </>}
        </section>
        <p className="st-boundary">平台定制智能体由程序维护固定流程；普通智能体的创建和发布不会修改此能力。源码只用于分析，未连接 PLC，未下发设备。</p>
      </div>
    </div>
  </div>;
}

function Code({text}:{text:string}){return <pre className="st-code"><code>{text}</code></pre>}
