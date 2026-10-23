/* Executive drilldown uses the same Direct Sales rows as the team chart. */
let executiveSearch='',executiveReturn=['All'],executiveFilter={channel:['All'],city:['All'],state:['All']};
const EXECUTIVE_TABLE_PARAMS=[['channel','tableChannel'],['city','tableCity'],['state','tableState']];  // URL names
function executiveMonths(){
  return ['Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec','Jan','Feb','Mar'].map((m,i)=>`${m}-${Number(FY)+(i>8?1:0)}`);
}
const supplierKey=value=>String(value||'').trim().replace(/\s+/g,' ').toLowerCase();
function executiveCustomers(rows){
  const groups=new Map();
  for(const r of rows){
    const key=supplierKey(r[SUPPLIER]),named=!!key&&key!=='unknown';
    const id=named?key:'';
    const group=groups.get(id)||{name:named?String(r[SUPPLIER]).trim().replace(/\s+/g,' '):'Supplier not recorded',named,entries:0,units:0,sales:0,cities:new Set(),states:new Set(),channels:new Set()};
    group.entries++;group.units+=r[QT];group.sales+=r[TX];
    for(const [field,index] of [['cities',CT],['states',ST],['channels',RG]]){
      if(r[index]&&r[index]!=='Unknown')group[field].add(r[index]);
    }
    groups.set(id,group);
  }
  return [...groups.values()].sort((a,b)=>b.sales-a.sales||a.name.localeCompare(b.name));
}
function executiveTargetAllocations(person){
  const regions=new Set([...CHKEY.map(([region])=>region),...EXT().filter(plan=>rkey(plan.person)===rkey(person))
    .map(plan=>plan.region||'D&R - S')]);
  return [...regions].flatMap(region=>channelExecutiveAllocations(person,{region}));
}
function executiveScopeMatches(row,allocation){
  return channelExecutiveScopeMatches(row,allocation);
}
function executiveAllocationTarget(allocations,months){
  if(!allocations.length||!months.length)return null;
  const values=allocations.flatMap(allocation=>months.map(month=>{
    const short=month.split('-')[0];
    return Array.isArray(allocation.reportedMonths)&&!allocation.reportedMonths.includes(short)?undefined:allocation.months[short];
  }));
  return values.every(value=>value!==null&&value!==undefined&&
    (typeof value==='number'||typeof value==='string'&&value.trim()!=='')&&Number.isFinite(Number(value))&&Number(value)>=0)
    ?values.reduce((total,value)=>total+Number(value),0):null;
}
function executiveDetails(person,filters=F){
  const yearRows=fyRows().filter(r=>filterMatches(person,r[EX])&&
    filterMatches(filters.dtt,r[DT])&&filterMatches(filters.dist,r[GP])&&filterMatches(filters.state,r[ST])&&filterMatches(filters.customer,r[12]));
  const rows=yearRows.filter(r=>filterMatches(filters.month,r[M]));
  const months=executiveMonths().filter(month=>filterMatches(filters.month,month));
  // A channel target shared by two selected names is one assigned amount.
  // Target allocations remain separate. Achievement includes the person's
  // entire assigned channel, regardless of where a target was allocated.
  const allocationMap=new Map();
  if(!filterAll(person))for(const name of filterValues(person))for(const allocation of executiveTargetAllocations(name)){
    const existing=allocationMap.get(allocation.targetKey);
    if(existing)existing.people=[...new Set([...existing.people,...allocation.people])];
    else allocationMap.set(allocation.targetKey,allocation);
  }
  const targetModel=filterAll(person)?null:channelTargetSelection({...filters,exec:person},{});
  const allocations=targetModel?targetModel.allocations:[...allocationMap.values()];
  const targetFor=ms=>{
    if(!targetModel)return executiveAllocationTarget(allocations,ms);
    const matched=targetModel.monthly.filter(row=>ms.includes(row.month));
    return matched.length===ms.length&&matched.length>0&&matched.every(row=>row.target!==null)
      ?matched.reduce((total,row)=>total+row.target,0):null;
  };
  const inScope=r=>allocations.some(a=>r[RG]===a.region&&
    (!a.people||a.people.some(name=>rkey(name)===rkey(r[EX]))));
  const customers=executiveCustomers(rows);
  const monthly=months.map(month=>{const rs=yearRows.filter(r=>r[M]===month);return {month,target:targetFor([month]),sales:sumTX(rs),targetSales:allocations.length?sumTX(rs.filter(inScope)):null,entries:rs.length};});
  return {rows,yearRows,months,allocations,customers,monthly,
    customerCount:customers.filter(x=>x.named).length,entries:rows.length,units:rows.reduce((a,r)=>a+r[QT],0),sales:sumTX(rows),
    targetYear:targetFor(executiveMonths()),targetPeriod:targetFor(months),targetSales:allocations.length?sumTX(rows.filter(inScope)):null,
    targetNote:targetModel?.targetNote||''};
}

function executiveDetailUrl(person,home=false){
  const destination=home?dashboardReturnPage():'team';
  const query=new URLSearchParams({page:destination,fy:FY});
  const append=(key,value,includeAll=false)=>{
    for(const option of filterAll(value)?(includeAll?['All']:[]):filterValues(value))query.append(key,option);
  };
  append('executive',home?executiveReturn:person,true);
  if(home&&!['home','team'].includes(destination)){
    query.set('drilldown','1');append('exec',executiveReturn,true);
  }
  for(const key of ['month','dtt','dist','state','customer'])append(key,F[key]);
  if(!home){
    append('returnExecutive',page==='team'?executiveReturn:F.exec,true);
    // The table filters are shared by the all-executives and executive pages.
    for(const [key,param] of EXECUTIVE_TABLE_PARAMS)append(param,executiveFilter[key]);
    if(executiveSearch.trim())query.set('tableSearch',executiveSearch.trim());
  }
  return '/?'+query.toString();
}
function initExecutiveLocation(){
  const query=new URLSearchParams(window.location.search);
  if(!['team','home'].includes(query.get('page')))return;
  page=query.get('page');
  const fy=query.get('fy');
  if((DATA.fys||[DATA.fy]).map(String).includes(fy))setFY(fy);
  for(const [key,options] of [['month',page==='team'?executiveMonths():MONTHS],['dtt',uniq(DT)],['dist',uniq(GP)],['state',uniq(ST)],['customer',uniq(12)]]){
    F[key]=filterTrim(query.getAll(key),options);
  }
  F.exec=filterTrim(query.getAll('executive'),uniq(EX));
  executiveReturn=filterTrim(query.getAll('returnExecutive'),uniq(EX));
  for(const [[key,param],index] of EXECUTIVE_TABLE_PARAMS.map((p,i)=>[p,[RG,CT,ST][i]])){
    executiveFilter[key]=filterTrim(query.getAll(param),uniq(index));
  }
  executiveSearch=(query.get('tableSearch')||'').slice(0,100);
}
const executiveMoney=value=>value===null?'Not set':inrF(value);
const executivePercent=(sales,target)=>sales!==null&&target>0?(sales/target*100).toFixed(1)+'%':'&ndash;';
/* Channel, City and State filter the sales rows behind the table: the branches
   table on an executive's page, the executives table on the all-executives
   page. Each name's entries, units and sales then cover only the chosen values.
   Each list offers the values left by the other two filters. The filters do
   not change the cards, targets or other tables on the page. */
const executiveFilterDefs=()=>[['channel','Channel',RG],['city','City',CT],['state','State',ST]];
const executiveFiltered=()=>executiveFilterDefs().some(([key])=>!filterAll(executiveFilter[key]));
function executiveFilterRows(rows,skip){
  return rows.filter(r=>executiveFilterDefs().every(([key,,index])=>
    key===skip||!dashboardExecutiveFilterAllowed(key)||filterMatches(executiveFilter[key],r[index])));
}
function executiveFilterOptions(detail,key,index){
  const values=new Set(executiveFilterRows(detail.rows,key).map(r=>r[index]).filter(v=>v&&v!=='Unknown'));
  if(!filterAll(executiveFilter[key]))for(const value of filterValues(executiveFilter[key]))values.add(value);  // kept choices stay visible
  return ['All',...[...values].sort((a,b)=>String(a).localeCompare(String(b)))];
}
function executiveCustomerFilters(detail){
  return executiveFilterDefs().filter(([key])=>dashboardExecutiveFilterAllowed(key)).map(([key,label,index])=>`<label class="customer-filter" for="executive-filter-${key}">${label} <select id="executive-filter-${key}" data-executive-filter="${key}" multiple>${
    executiveFilterOptions(detail,key,index).map(o=>`<option value="${esc(o)}"${(filterAll(executiveFilter[key])?o==='All':filterValues(executiveFilter[key]).includes(o))?' selected':''}>${esc(o)}</option>`).join('')}</select></label>`).join('');
}
function executiveTableTools(detail){
  return `<div class="customer-tools"><div class="customer-filters" id="executive-customer-filters">${executiveCustomerFilters(detail)}</div><label data-dashboard-filter="customer" class="customer-search" for="executive-customer-search">Search <input id="executive-customer-search" type="search" placeholder="Branch / retailer / supplier name" value="${esc(executiveSearch)}"></label></div>`;
}
/* Sales rows passing the filters and the name search, as the tables count them. */
function executiveTableRows(rows){
  const search=dashboardCan('filters','customer')?supplierKey(executiveSearch):'';
  return executiveFilterRows(rows).filter(r=>{const key=supplierKey(r[SUPPLIER]);
    return (key&&key!=='unknown'?key:'supplier not recorded').includes(search);});
}
function resetExecutiveTable(){
  executiveSearch='';executiveFilter={channel:['All'],city:['All'],state:['All']};
}
const executiveSingle=()=>!filterAll(F.exec)&&filterValues(F.exec).length===1;
const executiveTableHtml=detail=>executiveSingle()?executiveCustomerTable(detail):executiveSummaryTable(detail);
function refreshExecutiveTable(){
  const table=document.getElementById('executive-customer-table');if(table)table.innerHTML=dashboardMarkup(executiveTableHtml(executiveDetails(F.exec)));
  if(page==='team')window.history.replaceState(null,'',executiveDetailUrl(F.exec));
}
function refreshExecutiveCustomers(){
  const filters=document.getElementById('executive-customer-filters');if(filters)filters.innerHTML=executiveCustomerFilters(executiveDetails(F.exec));
  refreshExecutiveTable();
}
function executiveSummaryTable(){
  const filtered=!!supplierKey(executiveSearch)||executiveFiltered(),names=uniq(EX).filter(name=>filterMatches(F.exec,name)),monthly=!filterAll(F.month);
  const periodTarget=filterValues(F.month).length>1?'Selected-period target (₹)':'Monthly target (₹)';
  const people=names.map(name=>{
    const detail=executiveDetails(name),rows=executiveTableRows(detail.rows);
    return {name,rows,targetYear:detail.targetYear,targetPeriod:detail.targetPeriod,sales:sumTX(rows),
      customers:executiveCustomers(rows).filter(x=>x.named).length,entries:rows.length,units:rows.reduce((a,r)=>a+r[QT],0)};
  }).filter(x=>!filtered||x.entries>0).sort((a,b)=>b.sales-a.sales);
  const rows=people.flatMap(x=>x.rows);
  const clear=filtered?' Filters change the sales, supplier, entry and unit columns; targets keep their assigned amounts. <button type="button" class="detail-clear" data-executive-clear>Clear filters</button>':'';
  return `<p class="detail-note">${cnt(people.length)} of ${cnt(names.length)} executives shown. Select an executive to open their sales, targets and billed branches / retailers / suppliers.${clear}</p><div class="scroll"><table><thead><tr><th>Executive</th><th class="n">Yearly target (₹)</th>${monthly?`<th class="n">${periodTarget}</th>`:''}<th class="n">Total sales (₹)</th><th class="n">Unique suppliers</th><th class="n">Sales entries</th><th class="n">Units</th></tr></thead><tbody>${
    people.map(x=>`<tr><td><a class="detail-link" href="${esc(executiveDetailUrl(x.name))}">${esc(x.name)}</a></td><td class="n">${executiveMoney(x.targetYear)}</td>${monthly?`<td class="n">${executiveMoney(x.targetPeriod)}</td>`:''}<td class="n">${inrF(x.sales)}</td><td class="n">${cnt(x.customers)}</td><td class="n">${cnt(x.entries)}</td><td class="n">${cnt(x.units)}</td></tr>`).join('')||`<tr><td colspan="${monthly?7:6}">No matching sales entries.</td></tr>`}
    <tr class="tot"><td>${filtered?'Filtered total':'Total'}</td><td></td>${monthly?'<td></td>':''}<td class="n">${inrF(sumTX(rows))}</td><td class="n">${cnt(executiveCustomers(rows).filter(x=>x.named).length)}</td><td class="n">${cnt(rows.length)}</td><td class="n">${cnt(rows.reduce((a,r)=>a+r[QT],0))}</td></tr></tbody></table></div>`;
}
function executiveCustomerTable(detail){
  const search=dashboardCan('filters','customer')?supplierKey(executiveSearch):'',filtered=executiveFiltered();
  const customers=filtered?executiveCustomers(executiveFilterRows(detail.rows)):detail.customers;
  const rows=customers.filter(x=>supplierKey(x.name).includes(search));  // search matches names only
  const total=rows.reduce((a,x)=>({entries:a.entries+x.entries,units:a.units+x.units,sales:a.sales+x.sales}),{entries:0,units:0,sales:0});
  const share=sales=>detail.sales>0?(sales/detail.sales*100).toFixed(1)+'%':'&ndash;';
  const clear=search||filtered?' Filters apply to this table only. <button type="button" class="detail-clear" data-executive-clear>Clear filters</button>':'';
  return `<p class="detail-note">${cnt(rows.filter(x=>x.named).length)} of ${cnt(detail.customerCount)} unique names shown. Repeated names count once; blank names are excluded from the count.${clear}</p><div class="scroll"><table><thead><tr><th>Branch / retailer / supplier</th><th>Channel</th><th>City</th><th>State</th><th class="n">Sales entries</th><th class="n">Units</th><th class="n">Sales (₹)</th><th class="n">Sales share</th></tr></thead><tbody>
    ${rows.map(x=>`<tr><td class="wrap">${esc(x.name)}</td><td class="wrap">${esc([...x.channels].sort().join(', '))}</td><td class="wrap">${esc([...x.cities].sort().join(', ')||'—')}</td><td class="wrap">${esc([...x.states].sort().join(', ')||'—')}</td><td class="n">${cnt(x.entries)}</td><td class="n">${cnt(x.units)}</td><td class="n">${inrF(x.sales)}</td><td class="n">${share(x.sales)}</td></tr>`).join('')||'<tr><td colspan="8">No matching sales entries.</td></tr>'}
    <tr class="tot"><td>${search||filtered?'Filtered total':'Total'}</td><td></td><td></td><td></td><td class="n">${cnt(total.entries)}</td><td class="n">${cnt(total.units)}</td><td class="n">${inrF(total.sales)}</td><td class="n">${share(total.sales)}</td></tr></tbody></table></div>`;
}
function pgExecutiveDetails(){
  if(!dashboardPageAllowed('team'))return dashboardEmpty();
  const person=filterLabel(F.exec,'All executives'),detail=executiveDetails(F.exec),yearly=filterAll(F.month);
  const period=yearly?'Full financial year '+fyName():detail.months.map(month=>month.replace('-',' ')).join(', ');
  const selectedPeriod=detail.months.length>1?'Selected-period':'Monthly';
  const toolbar=`<div class="executive-toolbar"><p><a class="detail-link" href="${esc(executiveDetailUrl('All',true))}">Dashboard</a> &rsaquo; <a class="detail-link" href="${esc(executiveDetailUrl('All'))}">Sales team — all executives</a>${filterAll(F.exec)?'':` &rsaquo; <strong>${esc(person)}</strong>`}</p><p>${esc(period)} · Direct Sales entries</p></div>`;
  if(!executiveSingle()){
    return `<div class="canvas executive-detail">${toolbar}<div class="vis" data-dashboard-widget="executive_sales"><div class="executive-toolbar"><h3>Sales team — ${filterAll(F.exec)?'all executives':'selected executives'}</h3>${executiveTableTools(detail)}</div><div id="executive-customer-table">${executiveSummaryTable()}</div></div></div>`;
  }
  const cardsHtml=`<div class="cards k6">
    ${kcard('₹','Total sales',inrF(detail.sales),'Taxable sales · '+esc(period),0)}
    ${kcard('🎯',yearly?'Yearly target':'Selected-month target',executiveMoney(yearly?detail.targetYear:detail.targetPeriod),yearly?'Full financial year '+esc(fyName()):esc(period),1)}
    ${kcard('📅',yearly?'Yearly target achievement':selectedPeriod+' target achievement',executivePercent(detail.targetSales,detail.targetPeriod),detail.targetPeriod===null?'No assigned target':inrF(detail.targetSales)+' / '+inrF(detail.targetPeriod)+' in assigned channels',2)}
    ${kcard('🏪','Branches / retailers / suppliers',cnt(detail.customerCount),'Unique billed names in this selection',3)}
    ${kcard('📋','Sales entries',cnt(detail.entries),'Invoice lines from Direct Sales',4)}
    ${kcard('📦','Units sold',cnt(detail.units),'Units in this selection',5)}
  </div>`;
  const monthly=`<div class="scroll"><table><thead><tr><th>Month</th><th class="n">Target (₹)</th><th class="n">Total sales (₹)</th><th class="n">Sales against target (₹)</th><th class="n">Achievement %</th></tr></thead><tbody>${detail.monthly.map(x=>`<tr><td>${esc(x.month.replace('-',' '))}</td><td class="n">${executiveMoney(x.target)}</td><td class="n">${inrF(x.sales)}</td><td class="n">${x.targetSales===null?'—':inrF(x.targetSales)}</td><td class="n">${executivePercent(x.targetSales,x.target)}</td></tr>`).join('')}
    <tr class="tot"><td>Total</td><td class="n">${executiveMoney(detail.targetPeriod)}</td><td class="n">${inrF(detail.sales)}</td><td class="n">${detail.targetSales===null?'—':inrF(detail.targetSales)}</td><td class="n">${executivePercent(detail.targetSales,detail.targetPeriod)}</td></tr></tbody></table></div>`;
  const allocations=detail.allocations.length?`<div class="scroll"><table><thead><tr><th>Target allocation</th><th class="n">Yearly target (₹)</th>${yearly?'':`<th class="n">${selectedPeriod} target (₹)</th>`}</tr></thead><tbody>${detail.allocations.map(x=>`<tr><td class="wrap">${esc(x.label)}${x.city?`<br><small>${esc(x.region)}</small>`:''}</td><td class="n">${executiveMoney(executiveAllocationTarget([x],executiveMonths()))}</td>${yearly?'':`<td class="n">${executiveMoney(executiveAllocationTarget([x],detail.months))}</td>`}</tr>`).join('')}</tbody></table></div>`:'<p class="detail-note">No target is assigned to this executive.</p>';
  const channels=[...new Set(detail.rows.map(r=>r[RG]))].map(name=>{const rs=detail.rows.filter(r=>r[RG]===name);return {name,entries:rs.length,sales:sumTX(rs),customers:executiveCustomers(rs).filter(x=>x.named).length};}).sort((a,b)=>b.sales-a.sales);
  const channelTable=`<div class="scroll"><table><thead><tr><th>Channel</th><th class="n">Unique suppliers</th><th class="n">Sales entries</th><th class="n">Sales (₹)</th></tr></thead><tbody>${channels.map(x=>`<tr><td>${esc(x.name)}</td><td class="n">${cnt(x.customers)}</td><td class="n">${cnt(x.entries)}</td><td class="n">${inrF(x.sales)}</td></tr>`).join('')||'<tr><td colspan="4">No sales for this selection.</td></tr>'}<tr class="tot"><td>Total</td><td class="n">${cnt(detail.customerCount)}</td><td class="n">${cnt(detail.entries)}</td><td class="n">${inrF(detail.sales)}</td></tr></tbody></table></div>`;
  return `<div class="canvas executive-detail">${toolbar}${cardsHtml}<div class="vgrid">
    <section class="vis v7" data-dashboard-widget="executive_monthly"><h3>Monthly sales and targets — ${esc(person)}</h3>${monthly}<p class="detail-note">Target achievement includes all filtered sales in the assigned channels. Total sales also includes any other channels.</p></section>
    <section class="vis v5" data-dashboard-widget="${dashboardCan('widgets','target')?'target':'channel_sales'}"><div data-dashboard-widget="target"><h3>Target breakdown</h3>${allocations}<p class="detail-note">${esc(detail.targetNote||'Assigned budgets are shown for reference. Individual supplier targets are not assigned in this plan.')}</p></div><div data-dashboard-widget="channel_sales"><h3>Sales by channel</h3>${channelTable}</div></section>
    <section class="vis v12" data-dashboard-widget="executive_customers"><div class="executive-toolbar"><h3>Branches / retailers / suppliers — ${esc(person)}</h3>${executiveTableTools(detail)}</div><div id="executive-customer-table">${executiveCustomerTable(detail)}</div></section>
  </div></div>`;
}

/* Channel Detail keeps the executive's assignments in this channel only.
   Preserve an explicit zero plan, and do not turn an absent month into zero. */
function channelExecutiveAllocations(person,definition=pageDef()){
  const region=definition.region;
  if(!region||typeof person!=='string'||!person.trim()||person==='All')return [];
  const own=EXT().filter(plan=>rkey(plan.person)===rkey(person)&&
    (plan.region||'D&R - S')===region);
  if(own.length){
    return own.map(plan=>({label:(plan.scopeType==='state'?plan.state:plan.city)||region,
      city:plan.scopeType==='state'?'':plan.city||'',state:plan.state||'',scopeType:plan.scopeType==='state'?'state':plan.city?'city':'channel',region,
      months:plan.full||Object.fromEntries(['Apr','May','Jun','Jul']
        .flatMap((month,index)=>plan.t&&Object.prototype.hasOwnProperty.call(plan.t,index)?[[month,plan.t[index]]]:[])),
      reportedMonths:Array.isArray(plan.reportedMonths)?plan.reportedMonths:undefined,
      people:[person],targetKey:'person:'+rkey(person)+':'+region+':'+(plan.scopeType||'city')+':'+rkey(plan.city||plan.state||'')}));
  }
  // A person's city assignment in another channel does not erase an established
  // channel assignment here. A shared alias plan is counted once when combined.
  const keys=[].concat(EXEC_CHANNEL[rkey(person)]||[]);
  const key=keys.find(targetKey=>(CHKEY.find(([,candidate])=>candidate===targetKey)||[])[0]===region),months=key&&TG()[key];
  if(!months)return [];
  return [{label:region,city:'',state:'',scopeType:'channel',region,months,people:[person],targetKey:'channel:'+key}];
}

function channelExecutiveScopeMatches(row,allocation){
  return (!allocation.people||allocation.people.some(person=>rkey(person)===rkey(row[EX])))&&
    row[RG]===allocation.region&&
    (allocation.scopeType==='state'?!!allocation.state&&rkey(row[ST])===rkey(allocation.state):
      !allocation.city||rkey(row[CT])===rkey(allocation.city));
}

function channelExecutivePeople(filters=F,definition=pageDef()){
  const region=definition.region;
  if(!region)return [];
  const yearRows=fyRows(),known=[...new Set(yearRows.map(row=>row[EX])
    .filter(name=>name&&name!=='Unknown'))];
  const names=new Set(yearRows.filter(row=>row[RG]===region).map(row=>row[EX])
    .filter(name=>name&&name!=='Unknown'));
  // Use ledger display names for the channel aliases; never create synthetic
  // Channel-named and person-named duplicates use normalized assignment keys.
  for(const name of known)if(channelExecutiveAllocations(name,definition).length)names.add(name);
  for(const plan of EXT().filter(plan=>(plan.region||'D&R - S')===region)){
    const name=plan.person;
    if(name&&name!=='Unknown'&&![...names].some(existing=>rkey(existing)===rkey(name)))names.add(name);
  }
  const totals=new Map();
  for(const row of yearRows)if(row[RG]===region&&filterMatches(filters.month,row[M])&&
      filterMatches(filters.dtt,row[DT])&&filterMatches(filters.dist,row[GP])&&filterMatches(filters.state,row[ST])&&filterMatches(filters.customer,row[12])){
    totals.set(row[EX],(totals.get(row[EX])||0)+row[TX]);
  }
  return [...names].filter(name=>filterMatches(filters.exec,name))
    .sort((a,b)=>(totals.get(b)||0)-(totals.get(a)||0)||String(a).localeCompare(String(b)));
}

function channelExecutiveMonthly(person,filters=F,definition=pageDef()){
  const chosen=typeof person==='string'&&person.trim()!==''&&person!=='All';
  if(!chosen)return {person,region:definition.region||'',months:[],allocations:[],monthly:[],rows:[],
    target:null,sales:0,achieved:0,otherSales:0,hasTargets:false,targetComplete:false,scopeLabel:''};
  const model=channelTargetSelection({...filters,exec:[person]},definition);
  const monthly=model.monthly.filter(row=>model.months.includes(row.month));
  const selection=model.selected;
  return {person,region:model.region,months:model.months,allocations:model.allocations,monthly,
    rows:selection.rows,filters:model.filters,target:selection.target,sales:selection.sales,achieved:selection.achieved,
    otherSales:selection.otherSales,hasTargets:chosen&&model.hasTargets,targetComplete:chosen&&selection.targetComplete,
    scopeLabel:chosen?model.scopeLabel:''};
}

/* One basis for Home and channel cards, charts and gauges. Targets retain
   their assignments; Home achievement includes every selected sales channel. */
function channelTargetSelection(filters=F,definition=pageDef()){
  const region=definition.region||'',executiveFiltered=!filterAll(filters.exec);
  const people=executiveFiltered?filterValues(filters.exec):[];
  const allocationMap=new Map(),assignedPeople=new Set(),relevantPeople=new Set();
  const ledgerPeople=region?new Set(fyRows().filter(row=>row[RG]===region).map(row=>rkey(row[EX]))):null;
  const stateAllowed=allocation=>filterAll(filters.state)||!allocation.state||
    filterValues(filters.state).some(state=>rkey(state)===rkey(allocation.state));
  if(executiveFiltered){
    for(const person of people){
      const assigned=region?channelExecutiveAllocations(person,definition):executiveTargetAllocations(person);
      // An executive in a different channel must not erase this channel's
      // valid targets. Sales here without an assignment still make it incomplete.
      if(!region||assigned.length||ledgerPeople.has(rkey(person)))relevantPeople.add(rkey(person));
      const own=assigned.filter(stateAllowed);
      if(own.length)assignedPeople.add(rkey(person));
      for(const allocation of own){
        const existing=allocationMap.get(allocation.targetKey);
        if(existing)existing.people=[...new Set([...existing.people,...allocation.people])];
        else allocationMap.set(allocation.targetKey,{...allocation,people:[...allocation.people]});
      }
    }
  }else{
    const key=region?definition.tkey||(CHKEY.find(([channel])=>channel===region)||[])[1]:'ALL TOTAL';
    const months=TG()[key];
    if(months)allocationMap.set('channel:'+key,{label:region,region,city:'',state:'',scopeType:'channel',months,targetKey:'channel:'+key});
  }
  const allocations=[...allocationMap.values()];
  // These plans contain no brand/distribution split. A city or channel target
  // also cannot be divided among states using sales proportions or aliases.
  const unsupportedDimensions=['dtt','dist','customer'].filter(key=>!filterAll(filters[key]));
  if(!filterAll(filters.state)&&allocations.some(allocation=>!allocation.state))unsupportedDimensions.push('state');
  const targetScopeAvailable=unsupportedDimensions.length===0;
  const hasTargets=allocations.length>0&&targetScopeAvailable;
  const completePeople=!executiveFiltered||[...relevantPeople].every(person=>assignedPeople.has(person));
  const yearRows=fyRows().filter(row=>(!region||row[RG]===region)&&filterMatches(filters.exec,row[EX])&&
    filterMatches(filters.dtt,row[DT])&&filterMatches(filters.dist,row[GP])&&filterMatches(filters.state,row[ST])&&filterMatches(filters.customer,row[12]));
  const targetFor=month=>completePeople&&targetScopeAvailable?executiveAllocationTarget(allocations,[month]):null;
  const monthly=executiveMonths().map(month=>{
    const rows=yearRows.filter(row=>row[M]===month);
    const achievedRows=rows;
    const sales=sumTX(rows),achieved=sumTX(achievedRows),target=targetFor(month);
    return {month,target,sales,achieved,otherSales:sales-achieved,rows,achievedRows,targetComplete:target!==null};
  });
  const summarize=months=>{
    const entries=monthly.filter(row=>months.includes(row.month));
    const targetComplete=entries.length>0&&entries.every(row=>row.targetComplete);
    return {months,rows:entries.flatMap(row=>row.rows),achievedRows:entries.flatMap(row=>row.achievedRows),
      target:targetComplete?entries.reduce((total,row)=>total+row.target,0):null,targetComplete,
      sales:entries.reduce((total,row)=>total+row.sales,0),achieved:entries.reduce((total,row)=>total+row.achieved,0),
      otherSales:entries.reduce((total,row)=>total+row.otherSales,0)};
  };
  const months=MONTHS.filter(month=>filterMatches(filters.month,month));
  return {region,people,months,monthly,allocations,hasTargets,executiveFiltered,filters:{...filters},
    targetNote:targetScopeAvailable?'':'No target allocation for the selected '+unsupportedDimensions.map(key=>({dtt:'product',dist:'distribution',state:'state',customer:'customer'})[key]).join(' / ')+'.',
    targetLabel:executiveFiltered?'Executive target':region?'Channel target':'Company target',
    scopeLabel:[...new Set(allocations.map(allocation=>allocation.label))].join(', '),
    selected:summarize(months),annual:summarize(executiveMonths()),quarter:summarize(executiveMonths().slice(0,4))};
}

/* Explicit month choices compare the exact selected assignments and sales.
   The All-month overview retains its annual target and current sales context. */
function dashboardTargetComparison(model){
  const yearly=filterAll(model.filters.month),targetPeriod=yearly?model.annual:model.selected;
  return {...model.selected,target:targetPeriod.target,targetComplete:targetPeriod.targetComplete,
    yearly,basis:yearly?'full year':'selected months'};
}
