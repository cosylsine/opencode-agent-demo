import {useLayoutEffect, useRef, useState} from 'react';

export function useChatScroll(sessionId:string|null,active:boolean,revision:unknown){
  const transcript=useRef<HTMLDivElement>(null),content=useRef<HTMLDivElement>(null);
  const following=useRef(true),position=useRef(0);
  const [showLatest,setShowLatest]=useState(false);
  const scrollToLatest=()=>{
    following.current=true;setShowLatest(false);
    const el=transcript.current;if(el)el.scrollTop=el.scrollHeight;
  };
  const onScroll=()=>{
    const el=transcript.current;if(!el)return;
    position.current=el.scrollTop;
    following.current=el.scrollHeight-el.scrollTop-el.clientHeight<64;
    setShowLatest(!following.current);
  };
  useLayoutEffect(()=>{following.current=true;position.current=0;setShowLatest(false)},[sessionId]);
  useLayoutEffect(()=>{
    if(!active||!transcript.current)return;
    transcript.current.scrollTop=following.current?transcript.current.scrollHeight:position.current;
  },[active,sessionId]);
  useLayoutEffect(()=>{if(active&&following.current)scrollToLatest()},[active,revision]);
  useLayoutEffect(()=>{
    if(!active||!content.current||!transcript.current)return;
    const observer=new ResizeObserver(()=>{
      if(following.current)scrollToLatest();
      else setShowLatest(transcript.current!.scrollHeight-transcript.current!.scrollTop-transcript.current!.clientHeight>=64);
    });
    observer.observe(content.current);observer.observe(transcript.current);
    return()=>observer.disconnect();
  },[active]);
  return {transcript,content,onScroll,showLatest,scrollToLatest};
}
