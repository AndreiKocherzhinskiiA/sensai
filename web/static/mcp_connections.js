(() => {
  const dialog = document.createElement('dialog');
  dialog.id = 'mcp-connections';
  dialog.style.cssText = 'width:min(900px,94vw);max-height:88vh;overflow:auto;border:1px solid #bbb;border-radius:16px;padding:24px;color:inherit;background:var(--bg,#fff)';
  dialog.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;position:sticky;top:-24px;background:var(--bg,#fff);padding:10px 0;z-index:1"><h2>MCP connections</h2><button class="btn" type="button" id="mcp-close-panel">Close panel</button></div>
    <p>Connect independent servers and use their tools in Chat. Closing a connection removes its tools until you reconnect. Disabling it keeps it off after restarting Sensai.</p>
    <p id="mcp-management-error" role="alert" style="color:#bc2525"></p>
    <button class="btn" type="button" id="mcp-refresh">Refresh status</button><div id="mcp-server-list"></div>
    <details><summary>Add an MCP server</summary><p>Paste the server's mcpServers configuration, a servers list, or one named connection. Local commands run where Sensai runs (for example, inside WSL); HTTP connects to an existing MCP endpoint. Install local server dependencies first. Tools and argument schemas are discovered automatically. New tools require approval by default.</p>
    <p><button class="btn" type="button" id="mcp-stdio-example">Local process template</button> <button class="btn" type="button" id="mcp-http-example">HTTP template</button></p>
    <label for="mcp-config-input">Server configuration (JSON)</label>
    <textarea id="mcp-config-input" rows="12" spellcheck="false" style="width:100%;font-family:monospace"></textarea>
    <button class="btn" type="button" id="mcp-add-server">Add connections</button><p>Environment variables and HTTP headers are stored in the local registry and are not included in chat or connection status. Adding a connection does not execute a tool.</p></details>`;
  document.body.append(dialog);
  const examples = {
    stdio: {mcpServers:{'my-server':{command:'REPLACE_WITH_EXECUTABLE',args:[],env:{}}}},
    http: {mcpServers:{'my-server':{type:'http',url:'http://127.0.0.1:9000/mcp',headers:{}}}}
  };
  const showExample = kind => { dialog.querySelector('#mcp-config-input').value=JSON.stringify(examples[kind],null,2); };
  showExample('stdio');
  dialog.querySelector('#mcp-stdio-example').onclick=()=>showExample('stdio');
  dialog.querySelector('#mcp-http-example').onclick=()=>showExample('http');
  const error = dialog.querySelector('#mcp-management-error');
  let loading = false;
  async function api(path, body) {
    const response=await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'MCP request failed');
    return data;
  }
  function details(title,data,parent) {
    const d=document.createElement('details'),s=document.createElement('summary'),p=document.createElement('pre');
    s.textContent=title;p.textContent=JSON.stringify(data,null,2);p.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px';
    d.append(s,p);parent.append(d);
  }
  async function refresh() {
    const servers=await api('/api/mcp/servers'),list=dialog.querySelector('#mcp-server-list');list.replaceChildren();
    for(const server of servers){
      const card=document.createElement('section');card.dataset.server=server.name;
      card.style.cssText='border:1px solid #bbb;border-radius:10px;margin:14px 0;padding:16px';
      const title=document.createElement('h3');title.textContent=server.name+' · '+server.status+(server.busy?' · running a tool':'');card.append(title);
      const info=document.createElement('p');info.textContent=server.server_info?`${server.server_info.name} ${server.server_info.version} · ${server.transport} · MCP ${server.protocol_version} · connection ${server.generation}`:`Transport: ${server.transport} · connect to discover tools`;card.append(info);
      if(server.error){const e=document.createElement('p');e.textContent=server.error;e.style.color='#bc2525';card.append(e);}
      for(const [action,label] of [['connect','Connect'],['close','Close'],['restart','Restart'],[server.enabled?'disable':'enable',server.enabled?'Disable':'Enable'],['remove','Remove']]){
        const b=document.createElement('button');b.type='button';b.className='btn';b.textContent=label;b.style.cssText='margin:4px 8px 8px 0';
        b.disabled=(!server.enabled&&['connect','close','restart'].includes(action))||(server.status==='connected'&&action==='connect');
        b.onclick=()=>perform(async()=>{if(action==='remove'&&!confirm('Remove '+server.name+' from the local MCP registry?'))return;await api('/api/mcp/servers/'+encodeURIComponent(server.name)+'/'+action,{});});card.append(b);
      }
      if(server.tools.length){
        const note=document.createElement('p');note.textContent='Discovered tools · Off hides a tool from the model. Ask requires approval for every call. Automatic permits calls without confirmation.';card.append(note);
        const selections=[];
        for(const tool of server.tools){
          const row=document.createElement('div'),label=document.createElement('label'),select=document.createElement('select');
          row.style.cssText='margin:12px 0;padding:8px;border-top:1px solid #ddd';
          label.textContent=tool.remote_name+' ';select.className='btn';select.dataset.tool=tool.remote_name;
          select.setAttribute('aria-label','Permission for '+tool.remote_name);
          for(const [value,text] of [['off','Off'],['ask','Ask every time'],['automatic','Automatic']]){
            const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);
          }
          select.value=tool.enabled?tool.approval:'off';label.append(select);row.append(label);
          selections.push({name:tool.remote_name,select});
          details('Description and arguments',{model_name:tool.name,description:tool.description,inputSchema:tool.inputSchema},row);card.append(row);
        }
        const save=document.createElement('button');save.type='button';save.className='btn';save.textContent='Save tool permissions';
        save.onclick=()=>perform(async()=>{
          await api('/api/mcp/servers/'+encodeURIComponent(server.name)+'/tools',{
            allow_tools:selections.filter(t=>t.select.value!=='off').map(t=>t.name),
            auto_approve:selections.filter(t=>t.select.value==='automatic').map(t=>t.name)
          });
        });card.append(save);
      }
      if(server.server_instructions)details('Server usage notes (untrusted)',server.server_instructions,card);
      details('Connection and protocol events',server.events,card);list.append(card);
    }
    if(!servers.length)list.textContent='No servers registered. Add a server below.';
    const toggle=document.getElementById('tools-toggle');if(toggle)toggle.hidden=!servers.length;
  }
  async function perform(fn){
    if(loading)return;loading=true;error.textContent='';
    dialog.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{await fn();}catch(e){error.textContent=e.message;}
    finally{try{await refresh();}catch(e){error.textContent=e.message;}loading=false;dialog.querySelector('#mcp-refresh').disabled=false;dialog.querySelector('#mcp-close-panel').disabled=false;dialog.querySelector('#mcp-add-server').disabled=false;dialog.querySelector('#mcp-stdio-example').disabled=false;dialog.querySelector('#mcp-http-example').disabled=false;}
  }
  window.addEventListener('mcp:open',()=>{dialog.showModal();perform(async()=>{});});
  dialog.querySelector('#mcp-close-panel').onclick=()=>dialog.close();
  dialog.querySelector('#mcp-refresh').onclick=()=>perform(async()=>{});
  dialog.querySelector('#mcp-add-server').onclick=()=>perform(async()=>{await api('/api/mcp/servers',JSON.parse(dialog.querySelector('#mcp-config-input').value));dialog.querySelector('#mcp-config-input').value='';});
})();
