/* Read-only secondary sales detail. The caller supplies the exact chart scope
   and summary; monthly store amounts are not invoice-level sales records. */
(function(){
  'use strict';
  const PAGE_SIZE=100;
  let active=null,restoreSequence=0;
  const amount=value=>value===null||value===undefined||value===''||!Number.isFinite(Number(value))?null:Number(value);
  const cents=value=>Math.round(Number(value)*100);
  const money=value=>value===null?'Unreported':'₹'+Number(value).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const text=value=>value===null||value===undefined?'':String(value);
  const key=(id,month)=>String(id)+'|'+month;

  function model(payload){
    const months=[...new Set((payload.months||[]).map(String))];
    const stores=[...new Map((payload.stores||[]).map(store=>[String(store.id),store])).values()];
    const ids=new Set(stores.map(store=>String(store.id))),wanted=new Set(months);
    const groups=new Map((payload.distributors||[]).map(group=>[String(group.id),text(group.name)]));
    const observations=new Map(),targets=new Map();
    // Match the dashboard's last valid record per store/month, including zero.
    for(const [source,destination] of [[payload.sales,observations],[payload.targets,targets]]){
      for(const row of source||[]){
        const value=amount(row.amount);
        if(ids.has(String(row.storeId))&&wanted.has(row.month)&&value!==null)destination.set(key(row.storeId,row.month),value);
      }
    }
    const ordered=stores.slice().sort((a,b)=>
      (groups.get(String(a.distributorId))||'').localeCompare(groups.get(String(b.distributorId))||'')||
      Number(!!a.isAggregate)-Number(!!b.isAggregate)||text(a.name).localeCompare(text(b.name))||String(a.id).localeCompare(String(b.id)));
    const rows=[];
    let total=0,reported=0,namedReported=0,namedSlots=0;
    for(const store of ordered)for(const month of months){
      const id=key(store.id,month),has=observations.has(id),actual=has?observations.get(id):null;
      const aggregate=!!store.isAggregate;
      if(!aggregate){namedSlots++;if(has)namedReported++;}
      if(has){total+=cents(actual);reported++;}
      const row={storeId:store.id,distributorId:store.distributorId,
        group:groups.get(String(store.distributorId))||'Group not recorded',
        store:text(store.name)||'Store not named',month,aggregate,actual,
        target:aggregate?null:(targets.has(id)?targets.get(id):null),
        status:has?(actual===0?'Reported zero':'Reported'):'Unreported'};
      row.search=[row.group,row.store,row.month,row.status,aggregate?'aggregate':''].join(' ').toLocaleLowerCase();
      rows.push(row);
    }
    const actual=reported?total/100:null,summary=payload.summary||{};
    const chartActual=Object.prototype.hasOwnProperty.call(summary,'actual')?amount(summary.actual):actual;
    const target=amount(summary.target),targetComplete=!!summary.targetComplete;
    const targetReported=Number(summary.targetReported)||0;
    const reconciled=actual===null||chartActual===null?actual===chartActual:cents(actual)===cents(chartActual);
    return {rows,months,actual,chartActual,reported,namedReported,namedSlots,reconciled,
      target,targetComplete,targetReported,
      targetText:target!==null&&(targetComplete||targetReported)?money(target)+(targetComplete?'':' (partial)'):'Not set',
      achievement:actual!==null&&targetComplete&&target>0?(actual/target*100).toFixed(1)+'%':'—'};
  }

  const CSS=`
.dt-secondary-detail{box-sizing:border-box;width:min(1180px,calc(100vw - 28px));max-width:none;max-height:92vh;padding:0;border:1px solid #dce3eb;border-radius:16px;background:#f7f9fc;color:#152335;box-shadow:0 24px 80px #13233640;font:14px/1.5 Arial,sans-serif;overflow:auto}
.dt-secondary-detail::backdrop{background:#1020308c}
.dt-secondary-detail *{box-sizing:border-box}
.dt-secondary-detail button,.dt-secondary-detail input,.dt-secondary-detail select{font:inherit}
.dt-secondary-detail button{border:1px solid #c5d1df;border-radius:7px;padding:8px 13px;background:white;color:#1d354e;cursor:pointer}
.dt-secondary-detail button:hover{background:#eaf1f6}
.dt-secondary-detail button:focus-visible,.dt-secondary-detail input:focus-visible,.dt-secondary-detail select:focus-visible{outline:3px solid #318796;outline-offset:2px}
.dt-secondary-detail button:disabled{cursor:default;opacity:.45}
.dt-secondary-detail__head{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding:24px 26px 16px;border-bottom:1px solid #e2e8ef;background:white}
.dt-secondary-detail__eyebrow{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#477585;margin-bottom:5px}
.dt-secondary-detail h2{font-size:23px;line-height:1.25;margin:0 0 7px;color:#142c42}
.dt-secondary-detail__period{margin:0;color:#586b7d}
.dt-secondary-detail__close{flex:none}
.dt-secondary-detail__body{padding:20px 26px 24px}
.dt-secondary-detail__cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.dt-secondary-detail__card{background:white;border:1px solid #e0e7ef;border-radius:10px;padding:15px}
.dt-secondary-detail__label{color:#586b7d;font-size:12px;margin-bottom:5px}
.dt-secondary-detail__value{font-size:21px;font-weight:700;color:#163a4a;overflow-wrap:anywhere}
.dt-secondary-detail__hint,.dt-secondary-detail__note{color:#586b7d;font-size:12px}
.dt-secondary-detail__hint{margin-top:5px}
.dt-secondary-detail__note{margin:13px 0}
.dt-secondary-detail__warning{padding:12px 15px;border:1px solid #e4a65c;border-radius:8px;background:#fff3df;color:#77420e;margin-bottom:16px}
.dt-secondary-detail__tools{display:flex;align-items:end;gap:14px;flex-wrap:wrap;margin:18px 0 12px}
.dt-secondary-detail__field{display:flex;flex-direction:column;gap:5px;font-size:12px;color:#486074}
.dt-secondary-detail__field:first-child{flex:1;min-width:200px}
.dt-secondary-detail__field input,.dt-secondary-detail__field select{height:38px;border:1px solid #bdcad8;border-radius:7px;padding:8px 10px;background:white;color:#24384b;min-width:170px}
.dt-secondary-detail__filters{display:flex;gap:5px;flex-wrap:wrap}
.dt-secondary-detail__filters button{font-size:12px;padding:8px}
.dt-secondary-detail__filters button[aria-pressed="true"]{background:#1d6170;border-color:#1d6170;color:white}
.dt-secondary-detail__table-wrap{overflow:auto;max-height:49vh;border:1px solid #dce4ed;border-radius:9px;background:white}
.dt-secondary-detail table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}
.dt-secondary-detail caption{text-align:left;padding:12px 14px;font-weight:700;font-size:14px;background:white;color:#193b50}
.dt-secondary-detail th{position:sticky;top:0;background:#edf3f8;text-align:left;font-size:11px;color:#4d6479;padding:10px 12px;z-index:1}
.dt-secondary-detail td{padding:10px 12px;border-top:1px solid #eaf0f5;vertical-align:top}
.dt-secondary-detail tbody tr:hover{background:#f4f8fb}
.dt-secondary-detail .dt-secondary-detail__number{text-align:right;font-variant-numeric:tabular-nums}
.dt-secondary-detail__status{display:inline-block;border-radius:5px;padding:2px 6px;font-size:11px;background:#e9f4f0;color:#24654d}
.dt-secondary-detail__status--missing{background:#f0f2f5;color:#647286}
.dt-secondary-detail__aggregate{display:block;color:#7a6a4c;font-size:10px}
.dt-secondary-detail__footer{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-top:12px;color:#586b7d;font-size:12px}
.dt-secondary-detail__pages{display:flex;gap:9px;align-items:center}
.dt-secondary-detail--fallback{position:fixed;inset:4vh 0 auto;margin:0 auto;z-index:2147483000;display:block;box-shadow:0 0 0 100vmax #1020308c,0 24px 80px #13233640}
@media(max-width:650px){.dt-secondary-detail__head{padding:18px 16px 14px}.dt-secondary-detail__body{padding:16px}.dt-secondary-detail__cards{grid-template-columns:repeat(2,minmax(0,1fr))}.dt-secondary-detail__value{font-size:18px}.dt-secondary-detail h2{font-size:20px}.dt-secondary-detail__table-wrap{max-height:43vh}.dt-secondary-detail__field{width:100%}}
`;
  function element(tag,className,value){
    const node=document.createElement(tag);
    if(className)node.className=className;
    if(value!==undefined)node.textContent=text(value);
    return node;
  }
  function append(parent,...children){for(const child of children)parent.appendChild(child);return parent;}
  function close(){
    if(!active)return;
    const current=active,sequence=++restoreSequence;active=null;
    if(typeof current.dialog.close==='function'&&current.dialog.open)current.dialog.close();
    current.dialog.remove();
    document.body.style.overflow=current.overflow;
    const restore=()=>{
      // Restoring the scrollbar can redraw the chart. Resolve its current bar
      // through the caller instead of retaining only a detached SVG element.
      if(active||sequence!==restoreSequence)return;
      if(typeof current.restoreFocus==='function'){
        try{if(current.restoreFocus()!==false)return;}catch(_){/* Fall back to the original control when it still exists. */}
      }
      if(current.trigger&&current.trigger.isConnected&&typeof current.trigger.focus==='function')current.trigger.focus();
    };
    if(typeof window.requestAnimationFrame==='function')window.requestAnimationFrame(restore);
    else restore();
  }
  function open(payload,trigger){
    const report=model(payload);
    close();
    if(!document.getElementById('dt-secondary-detail-style')){
      const style=element('style');style.id='dt-secondary-detail-style';style.textContent=CSS;document.head.appendChild(style);
    }
    const dialog=element('dialog','dt-secondary-detail');
    dialog.setAttribute('aria-labelledby','dt-secondary-detail-title');
    dialog.setAttribute('aria-describedby','dt-secondary-detail-period');
    const head=element('header','dt-secondary-detail__head'),heading=element('div');
    const title=element('h2','',payload.title||'Secondary sales breakdown');title.id='dt-secondary-detail-title';
    const period=element('p','dt-secondary-detail__period',payload.periodLabel||report.months.join(', '));period.id='dt-secondary-detail-period';
    append(heading,element('div','dt-secondary-detail__eyebrow',text(payload.channel)+' · Secondary sales'),title,period);
    const dismiss=element('button','dt-secondary-detail__close','Close');dismiss.type='button';dismiss.addEventListener('click',close);
    append(head,heading,dismiss);
    const body=element('div','dt-secondary-detail__body');
    if(!report.reconciled){
      const warning=element('div','dt-secondary-detail__warning',
        'The monthly records total '+money(report.actual)+', while the chart shows '+money(report.chartActual)+'. Reload the dashboard before using this breakdown.');
      warning.setAttribute('role','alert');body.appendChild(warning);
    }
    const cards=element('div','dt-secondary-detail__cards');
    for(const [label,value,hint] of [
      ['Achieved',money(report.actual),'Total of reported monthly amounts'],
      ['Target',report.targetText,payload.targetBasis==='branch'?'Selected branch target':'Selected group target'],
      ['Achievement',report.achievement,'Shown when the target is complete and positive'],
      ['Reporting',report.namedReported+' / '+report.namedSlots,'Named-store months reported; aggregate rows excluded']
    ])append(cards,append(element('div','dt-secondary-detail__card'),element('div','dt-secondary-detail__label',label),element('div','dt-secondary-detail__value',value),element('div','dt-secondary-detail__hint',hint)));
    body.appendChild(cards);
    const notes=[payload.fullYear?'This annual bar covers the full financial year, regardless of the dashboard Month filter.':'This detail uses the months and stores selected for the chart bar.',
      'Each row is one store’s monthly amount. Blank amounts are unreported; an entered zero is reported. Aggregate rows contribute to achieved sales.'];
    notes.push(payload.targetBasis==='branch'
      ?'The target above uses the chart’s selected branch plan. For a full year it retains the explicit yearly target; the table shows each month’s branch target.'
      :'The target above uses the chart’s group plan. The branch targets below are shown separately and are not used to rescale the group target.');
    body.appendChild(element('p','dt-secondary-detail__note',notes.join(' ')));
    const tools=element('div','dt-secondary-detail__tools');
    const searchLabel=element('label','dt-secondary-detail__field','Find a store, group or month');
    const search=element('input');search.type='search';search.placeholder='Search these records';append(searchLabel,search);
    const statusLabel=element('div','dt-secondary-detail__field','Report status'),status=element('div','dt-secondary-detail__filters');
    status.setAttribute('role','group');status.setAttribute('aria-label','Report status');
    let statusValue='all';
    for(const [value,label] of [['all','All statuses'],['reported','Reported (including zero)'],['zero','Reported zero'],['missing','Unreported']]){
      const option=element('button','',label);option.type='button';option.setAttribute('aria-pressed',String(value===statusValue));
      option.addEventListener('click',()=>{statusValue=value;page=0;
        for(const button of status.children)button.setAttribute('aria-pressed',String(button===option));renderRows();});
      status.appendChild(option);
    }
    append(statusLabel,status);append(tools,searchLabel,statusLabel);body.appendChild(tools);
    const wrap=element('div','dt-secondary-detail__table-wrap');
    const table=element('table'),caption=element('caption','','Store and month breakdown'),thead=element('thead'),tr=element('tr');
    for(const [index,label] of ['Group','Store / customer','Month','Branch target','Achieved','Report status'].entries()){
      const th=element('th',index===3||index===4?'dt-secondary-detail__number':'',label);th.scope='col';tr.appendChild(th);
    }
    thead.appendChild(tr);const tbody=element('tbody');append(table,caption,thead,tbody);wrap.appendChild(table);body.appendChild(wrap);
    const footer=element('div','dt-secondary-detail__footer'),totals=element('div');
    totals.setAttribute('aria-live','polite');totals.setAttribute('aria-atomic','true');
    const pages=element('div','dt-secondary-detail__pages'),previous=element('button','','Previous'),next=element('button','','Next'),pageLabel=element('span');
    previous.type=next.type='button';append(pages,previous,pageLabel,next);append(footer,totals,pages);body.appendChild(footer);
    let page=0;
    function renderRows(){
      const query=search.value.trim().toLocaleLowerCase(),filter=statusValue;
      const rows=report.rows.filter(row=>(!query||row.search.includes(query))&&
        (filter==='all'||filter==='missing'&&row.actual===null||filter==='reported'&&row.actual!==null||filter==='zero'&&row.actual===0));
      const count=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));page=Math.max(0,Math.min(page,count-1));
      tbody.replaceChildren();
      for(const row of rows.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE)){
        const tr=element('tr');
        const store=element('td','',row.store);
        if(row.aggregate)store.appendChild(element('span','dt-secondary-detail__aggregate','Aggregate amount'));
        const state=element('td');state.appendChild(element('span','dt-secondary-detail__status'+(row.actual===null?' dt-secondary-detail__status--missing':''),row.status));
        append(tr,element('td','',row.group),store,element('td','',row.month),
          element('td','dt-secondary-detail__number',row.aggregate?'Not applicable':row.target===null?'Not set':money(row.target)),
          element('td','dt-secondary-detail__number',money(row.actual)),state);tbody.appendChild(tr);
      }
      if(!rows.length){const cell=element('td','','No store-month records match this view.');cell.colSpan=6;tbody.appendChild(append(element('tr'),cell));}
      const recorded=rows.filter(row=>row.actual!==null),total=recorded.length?recorded.reduce((sum,row)=>sum+cents(row.actual),0)/100:null;
      totals.textContent=rows.length+' of '+report.rows.length+' store-month rows · Filtered achieved total: '+money(total);
      pageLabel.textContent='Page '+(page+1)+' of '+count;previous.disabled=page===0;next.disabled=page===count-1;
      wrap.scrollTop=0;
    }
    search.addEventListener('input',()=>{page=0;renderRows();});
    previous.addEventListener('click',()=>{page--;renderRows();});next.addEventListener('click',()=>{page++;renderRows();});
    append(dialog,head,body);
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    dialog.addEventListener('close',()=>{if(active&&active.dialog===dialog)close();});
    dialog.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();return;}
      if(event.key!=='Tab')return;
      const controls=[...dialog.querySelectorAll('button,input,select,[tabindex="0"]')].filter(node=>!node.disabled&&!node.hidden&&node.getClientRects().length);
      const first=controls[0],last=controls[controls.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    });
    active={dialog,trigger:trigger||document.activeElement,restoreFocus:payload.restoreFocus,overflow:document.body.style.overflow};
    document.body.appendChild(dialog);document.body.style.overflow='hidden';renderRows();
    if(typeof dialog.showModal==='function')dialog.showModal();
    else{dialog.classList.add('dt-secondary-detail--fallback');dialog.setAttribute('open','');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');}
    dismiss.focus();
    return dialog;
  }
  window.dtSecondaryBreakdown={open,close,model};
})();
