// Only a transport adapter. The actual loop, tools and Standard preset are upstream DSH.
import fs from 'node:fs';
import crypto from 'node:crypto';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { installModelSelection } from '@deepseek-ai/dsh-agent';
export const name = 'eval-driver';
export const inject = ['agents', 'sessions', 'agentPresets'];
export function apply(ctx) {
  const exit = ctx.get('appExit');
  const write = (type, data) => fs.appendFileSync('/logs/agent/events.jsonl', JSON.stringify({time:new Date().toISOString(),type,data})+'\n');
  async function run() {
    await ctx.get('loader').await();
    for (const service of ['schedule','sessionController','workspaceRegistry']) {
      if (!ctx.get(service)) throw Error(`Standard host dependency unavailable: ${service}`);
    }
    const preset = await ctx.agentPresets.resolve('standard');
    if(preset.id !== 'standard') throw Error('Standard preset did not resolve');
    ctx.on('session/event', (session,event) => write('session_event',{session_id:String(session.id),event}));
    let failure;
    ctx.on('agent/error', ({error}) => {failure=String(error);write('agent_error',{error:failure});});
    const selection = {provider:'deepseek-official',model:process.env.EVAL_MODEL};
    const handle = await ctx.agents.create({
      sessionId:'eval-'+crypto.randomUUID(), meta:{cwd:process.cwd(),agentPreset:'standard'},
      agentOptions:{...selection,maxTokens:Number(process.env.EVAL_MAX_TOKENS)},
      setup:async agentCtx => {
        installModelSelection(agentCtx,{current:selection,assembled:undefined});
        await ctx.agentPresets.mount(agentCtx,'standard');
      },
    });
    const agent=handle.agent;
    const compositions=ctx.agentPresets.inspectCompositions(agent.ctx);
    if(compositions.length!==1 || compositions[0].id!=='standard') throw Error('Standard composition audit failed');
    write('standard_runtime_audit',{preset:preset.id,compositions});
    await agent.whenIdle();
    agent.followup(createUserMessage({content:[{type:'text',text:fs.readFileSync('/installed-agent/prompt.txt','utf8')}],source:{kind:'user'}}));
    while(true){
      await agent.whenIdle();const seq=agent.session.seq;
      if(!await ctx.get('subagents')?.waitForChildren(agent) && agent.status==='idle' && seq===agent.session.seq)break;
    }
    await ctx.sessions.flush(agent.session);
    let reason;
    for(let i=0;i<agent.session.seq;i++){const event=agent.session.eventAt(i);if(event?.type==='turn/end')reason=event.data.reason;}
    write('run_end',{reason:reason?.kind??'missing_terminal',failure:failure??null});
    exit(reason?.kind==='completed'&&!failure?0:1);
  }
  void run().catch(error=>{write('adapter_error',{error:String(error)});console.error(error);exit(1);});
}
