/* Online sales have their own source grain and filters. Missing cells stay
   missing; an explicit numeric zero is a reported sale. Amounts are rupees. */
const ONLINE_PLATFORMS=['Shopify','Amazon India','Flipkart','Myntra','Meesho','JioMart','Ajio','Tata CLiQ','FirstCry','Pepperfry','Nykaa Beauty','Nykaa Fashion'];
const ONLINE_PLATFORM_COLORS=['#315b96','#007c78','#a36516','#a83d4d','#7251a4','#326f93','#944c7b','#8a7423','#33785c','#52647e','#855b3e','#2e7681'];
const OF={platform:'All',date:null,mode:'monthly',month:null};
let onlineChartObserver=null,onlineChartSpecs=[];
function resetOnlineCharts(){onlineChartObserver?.disconnect();onlineChartObserver=null;onlineChartSpecs=[];}
function onlineChartBlock(models,options={}){
  const id=onlineChartSpecs.push({models,options,width:0,height:0})-1;
  return `<div class="online-trend-chart" data-online-chart="${id}">${onlineTrendChart(models,options)}</div>`;
}
function fitOnlineCharts(){
  const charts=[...document.querySelectorAll('#page [data-online-chart]')];if(!charts.length)return;
  const draw=node=>{
    if(!node.isConnected)return;
    const spec=onlineChartSpecs[Number(node.dataset.onlineChart)];if(!spec)return;
    const width=Math.round(node.clientWidth),height=Math.round(node.clientHeight);
    if(width<100||height<80||spec.width===width&&spec.height===height)return;
    spec.width=width;spec.height=height;
    node.innerHTML=spec.kind==='platforms'?onlinePlatformColumns(spec.model,{width,height}):onlineTrendChart(spec.models,{...spec.options,width,height});
  };
  charts.forEach(draw);
  if(typeof ResizeObserver!=='undefined'){
    onlineChartObserver=new ResizeObserver(entries=>entries.forEach(entry=>draw(entry.target)));
    charts.forEach(node=>onlineChartObserver.observe(node));
  }
}
function initOnlineLocation(){
  if(new URLSearchParams(window.location.search).get('page')==='online'&&dashboardUsablePage('online'))page='online';
}
function onlinePlatforms(data=DATA.onlineSales||{}){
  const names=[...(Array.isArray(data.platforms)?data.platforms:[]),...(Array.isArray(data.daily)?data.daily:[]).map(row=>row.platform),...(Array.isArray(data.monthly)?data.monthly:[]).map(row=>row.platform)];
  return [...new Set([...ONLINE_PLATFORMS,...names.filter(name=>typeof name==='string'&&name.trim())])];
}
const onlineMoney=value=>value===null||!Number.isFinite(value)?'—':new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',minimumFractionDigits:2,maximumFractionDigits:2}).format(value);
const onlineNumber=value=>typeof value==='number'&&Number.isFinite(value);
function onlineIsoDate(value){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(String(value)))return null;
  const date=new Date(value+'T00:00:00Z');
  return Number.isFinite(date.valueOf())&&date.toISOString().slice(0,10)===value?value:null;
}
function onlineAddDays(value,days){const date=new Date(value+'T00:00:00Z');date.setUTCDate(date.getUTCDate()+days);return date.toISOString().slice(0,10);}
function onlineDateLabel(value,short=false){
  return onlineIsoDate(value)?new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',day:'numeric',month:'short',...(short?{}:{year:'numeric'})}).format(new Date(value+'T00:00:00Z')):'No reporting date';
}
function onlinePeriod(mode,anchor){
  if(!onlineIsoDate(anchor))return null;
  if(mode==='weekly'){
    const weekday=new Date(anchor+'T00:00:00Z').getUTCDay(),start=onlineAddDays(anchor,-((weekday+6)%7)),end=onlineAddDays(start,6);
    return {mode,start,end,key:start,label:onlineDateLabel(start)+' – '+onlineDateLabel(end),short:onlineDateLabel(start,true)+' – '+onlineDateLabel(end,true)};
  }
  if(mode==='monthly'){
    const start=anchor.slice(0,7)+'-01',date=new Date(start+'T00:00:00Z');date.setUTCMonth(date.getUTCMonth()+1);
    return {mode,start,end:onlineAddDays(date.toISOString().slice(0,10),-1),key:start.slice(0,7),label:new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',month:'long',year:'numeric'}).format(new Date(start+'T00:00:00Z')),short:new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',month:'short',year:'numeric'}).format(new Date(start+'T00:00:00Z'))};
  }
  return {mode:'daily',start:anchor,end:anchor,key:anchor,label:onlineDateLabel(anchor),short:onlineDateLabel(anchor,true)};
}
function onlinePayload(){
  const source=DATA.onlineSales;
  if(!source||typeof source!=='object')return {available:false,daily:[],monthly:[],coverage:{},warnings:[]};
  const monthly=new Map();
  for(const row of Array.isArray(source.monthly)?source.monthly:[]){
    if(!onlineIsoDate(row.month+'-01')||typeof row.platform!=='string'||!row.platform.trim()||!onlineNumber(row.sales))continue;
    const key=row.month+'\0'+row.platform,previous=monthly.get(key);
    if(!previous||previous.source!=='monthly-entered'&&row.source==='monthly-entered')monthly.set(key,row);
  }
  return {...source,daily:(Array.isArray(source.daily)?source.daily:[]).filter(row=>onlineIsoDate(row.date)&&typeof row.platform==='string'&&row.platform.trim()&&onlineNumber(row.sales)),
    monthly:[...monthly.values()],coverage:source.coverage||{},warnings:Array.isArray(source.warnings)?source.warnings:[]};
}
function onlineAvailableMonths(data=onlinePayload()){
  return [...new Set([...data.monthly.map(row=>row.month),...data.daily.map(row=>row.date.slice(0,7))])].sort();
}
function onlineSelectedMonth(data=onlinePayload()){
  return onlineIsoDate(OF.month+'-01')?OF.month:OF.date?.slice(0,7)||onlineAvailableMonths(data).slice(-1)[0]||null;
}
function onlineLatest(data=onlinePayload()){
  return [...data.daily.map(row=>row.date),...data.monthly.map(row=>row.month+'-01')].sort().slice(-1)[0]||null;
}
function onlineNormalize(){
  if(!dashboardCan('filters','onlinePlatform')||!['All',...onlinePlatforms()].includes(OF.platform))OF.platform='All';
  if(!dashboardCan('filters','onlinePeriod')){OF.date=null;OF.month=null;OF.mode='monthly';}
  if(!['daily','weekly','monthly'].includes(OF.mode))OF.mode='monthly';
  if(OF.date!==null&&!onlineIsoDate(OF.date))OF.date=null;
  if(OF.month!==null&&!onlineIsoDate(OF.month+'-01'))OF.month=null;
}
const onlineSum=rows=>rows.length?rows.reduce((sum,row)=>sum+Math.round(row.sales*100),0)/100:null;
function onlinePeriodData(mode,anchor,platform='All',data=onlinePayload()){
  const period=onlinePeriod(mode,anchor),names=platform==='All'?onlinePlatforms(data):[platform];
  const daily=period?data.daily.filter(row=>row.date>=period.start&&row.date<=period.end&&(platform==='All'||row.platform===platform)):[];
  const records=!period?[]:mode==='monthly'?data.monthly.filter(row=>row.month===period.key&&(platform==='All'||row.platform===platform)):daily;
  const rows=names.map(name=>{
    const cells=records.filter(row=>row.platform===name),days=new Set(daily.filter(row=>row.platform===name).map(row=>row.date)).size;
    const entered=cells.some(row=>row.source==='monthly-entered'),derived=cells.some(row=>row.source==='daily-derived');
    return {platform:name,sales:onlineSum(cells),days:days||Math.max(0,...cells.map(row=>Number(row.daysReported)||0)),source:mode!=='monthly'?'Daily Sales':entered&&derived?'Monthly Sales + daily rollup':entered?'Monthly Sales':derived?'Daily rollup':'No value entered'};
  });
  return {period,rows,total:onlineSum(records),reported:rows.filter(row=>row.sales!==null).length,days:new Set(daily.map(row=>row.date)).size,
    derived:records.some(row=>row.source==='daily-derived'),entered:records.some(row=>row.source==='monthly-entered')};
}
function onlineTrend(mode,anchor,platform='All',data=onlinePayload()){
  if(!anchor)return [];
  const anchors=mode==='monthly'?data.monthly.map(row=>row.month+'-01'):data.daily.map(row=>row.date);
  const keys=[...new Set(anchors.filter(date=>date<=anchor).map(date=>onlinePeriod(mode,date).start))].sort();
  if(!keys.length)return [];
  const end=onlinePeriod(mode,anchor).start;let current=keys[0],all=[];
  // The import spans a bounded sheet, but cap the display work for saved reports.
  for(let count=0;current<=end&&count<3660;count++){
    all.push(current);
    if(mode==='monthly'){const date=new Date(current+'T00:00:00Z');date.setUTCMonth(date.getUTCMonth()+1);current=date.toISOString().slice(0,10);}
    else current=onlineAddDays(current,mode==='weekly'?7:1);
  }
  return all.slice(-(mode==='daily'?14:12)).map(date=>onlinePeriodData(mode,date,platform,data));
}
function onlineDailyRange(start,end,platform='All',data=onlinePayload()){
  if(!onlineIsoDate(start)||!onlineIsoDate(end))return [];
  const models=[];
  for(let date=start;date<=end&&models.length<366;date=onlineAddDays(date,1))models.push(onlinePeriodData('daily',date,platform,data));
  return models;
}
function onlineMonthTimeline(month,platform='All',data=onlinePayload()){
  const period=onlinePeriod('monthly',month+'-01');
  return period?onlineDailyRange(period.start,period.end,platform,data):[];
}
function onlineWeekGroups(models){
  const groups=[];
  models.forEach((model,index)=>{
    const period=onlinePeriod('weekly',model.period.start);let group=groups.slice(-1)[0];
    if(!group||group.period.key!==period.key){group={period,start:index,end:index,models:[]};groups.push(group);}
    group.end=index;group.models.push(model);
  });
  return groups.map(group=>({...group,total:onlineSum(group.models.filter(model=>model.total!==null).map(model=>({sales:model.total})))}));
}
function onlineWeeklyLabels(models){
  return `<div class="online-weeks" aria-label="Daily subtotals by Monday–Sunday week">${onlineWeekGroups(models).map(group=>`<span title="${esc('Monday–Sunday: '+group.period.label)}"><b>Week ${esc(group.period.short)}</b><small>${esc(onlineDateLabel(group.models[0].period.start,true)+' – '+onlineDateLabel(group.models.slice(-1)[0].period.end,true))} · ${onlineMoney(group.total)}</small></span>`).join('')}</div>`;
}
function onlineOverallData(data=onlinePayload(),platform='All'){
  const names=platform==='All'?onlinePlatforms(data):[platform],records=data.monthly.filter(row=>platform==='All'||row.platform===platform);
  return {rows:names.map(name=>({platform:name,sales:onlineSum(records.filter(row=>row.platform===name))})),total:onlineSum(records),months:onlineAvailableMonths(data)};
}
function onlineTvData(data=onlinePayload()){
  if(!data.available)return null;
  const dates=[...new Set(data.daily.map(row=>row.date))].sort(),latestDaily=dates.slice(-1)[0]||null;
  const month=onlineAvailableMonths(data).slice(-1)[0];if(!month)return null;
  const anchor=latestDaily||month+'-01',dailyMonth=latestDaily?.slice(0,7)||null;
  const reportedDates=dates.filter(date=>date.slice(0,7)===dailyMonth);
  const trend=reportedDates.length?onlineDailyRange(reportedDates[0],latestDaily,'All',data):[];
  return {day:onlinePeriodData('daily',anchor,'All',data),week:onlinePeriodData('weekly',anchor,'All',data),
    month:onlinePeriodData('monthly',month+'-01','All',data),overall:onlineOverallData(data),
    dailyMonth,dailyStart:reportedDates[0]||null,dailyEnd:latestDaily,trend};
}
function onlineHistoryTable(data=onlinePayload(),platform='All'){
  const months=onlineAvailableMonths(data),names=platform==='All'?onlinePlatforms(data):[platform];
  const models=months.map(month=>onlinePeriodData('monthly',month+'-01',platform,data));
  return `<div class="scroll online-history" tabindex="0" role="region" aria-label="Reported online sales for all available months"><table class="online-table"><thead><tr><th>Platform</th>${months.map(month=>`<th class="n">${esc(onlinePeriod('monthly',month+'-01').short)}</th>`).join('')}<th class="n">All months · INR</th></tr></thead><tbody>${names.map((name,index)=>`<tr><td>${esc(name)}</td>${models.map(model=>`<td class="n">${onlineMoney(model.rows[index].sales)}</td>`).join('')}<td class="n"><b>${onlineMoney(onlineSum(models.map(model=>({sales:model.rows[index].sales})).filter(row=>row.sales!==null)))}</b></td></tr>`).join('')}<tr class="tot"><td>Total reported</td>${models.map(model=>`<td class="n">${onlineMoney(model.total)}</td>`).join('')}<td class="n">${onlineMoney(onlineSum(models.filter(model=>model.total!==null).map(model=>({sales:model.total}))))}</td></tr></tbody></table></div>`;
}
function onlineSourceNote(data=onlinePayload()){
  const derived=data.monthly.some(row=>row.source==='daily-derived'),entered=data.monthly.some(row=>row.source==='monthly-entered');
  return `${derived?(entered?'Monthly Sales entries take priority; blank months use Daily Sales rollups.':'Monthly totals calculated from Daily Sales; Monthly Sales has no entered values.'):'Source: Daily Sales and Monthly Sales.'} — means no reported value; 0.00 is an entered zero.`;
}
function onlineDataNotice(data){
  return data.stale?'<p class="online-notice online-stale" role="status">Online Sales update delayed · showing the last saved report. Updates retry automatically.</p>':'';
}
function onlineEmpty(data=onlinePayload()){
  const imported=!!data.available;
  return `<div class="canvas online-sales"><section class="vis online-empty"><h3>Online sales</h3><p>${imported?'No online sales values have been entered yet.':'Online sales are temporarily unavailable.'}</p><p class="note">${imported?'Daily Sales and Monthly Sales will appear after values are recorded.':'The last import could not supply this report. The dashboard will retry its normal data refresh.'}</p></section></div>`;
}
function onlineSlicers(){
  onlineNormalize();const data=onlinePayload(),month=onlineSelectedMonth(data),anchor=OF.date||month+'-01',months=onlineAvailableMonths(data);
  if(month&&!months.includes(month))months.push(month);months.sort();
  const platform=dashboardCan('filters','onlinePlatform')?`<div class="slicer" data-dashboard-filter="onlinePlatform"><label for="sl-online-platform">Platform</label><select id="sl-online-platform">${['All',...onlinePlatforms()].map(name=>`<option value="${esc(name)}"${OF.platform===name?' selected':''}>${name==='All'?'All platforms':esc(name)}</option>`).join('')}</select></div>`:'';
  const period=dashboardCan('filters','onlinePeriod')?`<div class="slicer" data-dashboard-filter="onlinePeriod"><label for="sl-online-month">Report month</label><select id="sl-online-month">${months.map(value=>`<option value="${value}"${month===value?' selected':''}>${esc(onlinePeriod('monthly',value+'-01').label)}</option>`).join('')}</select></div><div class="slicer" data-dashboard-filter="onlinePeriod"><label for="sl-online-mode">Summary period</label><select id="sl-online-mode">${[['daily','Daily'],['weekly','Weekly'],['monthly','Monthly']].map(([value,label])=>`<option value="${value}"${OF.mode===value?' selected':''}>${label}</option>`).join('')}</select></div><div class="slicer" data-dashboard-filter="onlinePeriod"><label for="sl-online-date">Date in summary period</label><input type="date" id="sl-online-date" value="${onlineIsoDate(anchor)||''}" aria-label="Date in online sales summary period"></div>`:'';
  document.getElementById('slicers').innerHTML=platform+period;
  const select=document.getElementById('sl-online-platform');if(select)select.onchange=()=>{OF.platform=select.value;render();};
  const modes=document.getElementById('sl-online-mode');if(modes)modes.onchange=()=>{OF.mode=modes.value;render();};
  const monthsSelect=document.getElementById('sl-online-month');if(monthsSelect)monthsSelect.onchange=()=>{OF.month=monthsSelect.value;OF.date=OF.month+'-01';OF.mode='monthly';render();};
  const date=document.getElementById('sl-online-date');if(date)date.onchange=()=>{OF.date=onlineIsoDate(date.value);OF.month=OF.date?.slice(0,7)||null;render();};
}
function onlinePlatformColor(platform){
  const known=ONLINE_PLATFORMS.indexOf(platform),index=known>=0?known:[...platform].reduce((value,letter)=>value+letter.charCodeAt(0),0);
  return ONLINE_PLATFORM_COLORS[index%ONLINE_PLATFORM_COLORS.length];
}
function onlinePlatformRanking(model){
  return {...model,rows:[...model.rows].sort((a,b)=>{
    if(a.sales===null)return b.sales===null?a.platform.localeCompare(b.platform):1;
    if(b.sales===null)return -1;
    return b.sales-a.sales||a.platform.localeCompare(b.platform);
  })};
}
function onlinePlatformColumns(model,size={}){
  model=onlinePlatformRanking(model);
  const {width=600,height=220}=size,W=Math.max(180,width),H=Math.max(120,height),L=48,R=10,T=18,B=28,pw=W-L-R,ph=H-T-B;
  const observedMax=Math.max(0,...model.rows.map(row=>row.sales||0)),min=Math.min(0,...model.rows.map(row=>row.sales||0));
  const max=observedMax===min?min+1:observedMax,span=max-min,slot=pw/model.rows.length,bw=Math.min(36,slot*.58),y=value=>T+(max-value)/span*ph;
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Online sales by platform in rupees; numbered coloured columns match the platform legend"><title>Reported sales by platform. Exact amounts are in the legend. A dash means no reported value; zero is reported.</title>`;
  for(let index=0;index<4;index++){
    const amount=min+span*index/3,yy=y(amount);
    svg+=`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="#e8edf3"/><text x="${L-7}" y="${yy+4}" text-anchor="end" fill="#768296" font-size="10">${esc(tick(amount))}</text>`;
  }
  svg+=`<line x1="${L}" x2="${W-R}" y1="${y(0)}" y2="${y(0)}" stroke="#bdc8d6"/>`;
  model.rows.forEach((row,index)=>{
    const x=L+slot*(index+.5),color=onlinePlatformColor(row.platform),description=esc(row.platform+': '+onlineMoney(row.sales));
    if(row.sales===null)svg+=`<text x="${x}" y="${y(0)-6}" text-anchor="middle" fill="#a6afbc" font-size="14">—<title>${description}</title></text>`;
    else if(row.sales===0)svg+=`<circle cx="${x}" cy="${y(0)}" r="3" fill="${color}"><title>${description}</title></circle>`;
    else{
      const height=Math.max(2,Math.abs(y(row.sales)-y(0))),top=row.sales>0?y(0)-height:y(0);
      svg+=`<rect x="${x-bw/2}" y="${top}" width="${bw}" height="${height}" rx="4" fill="${color}"><title>${description}</title></rect>`;
      svg+=`<line x1="${x-bw/2+3}" x2="${x+bw/2-3}" y1="${top+Math.min(4,height/2)}" y2="${top+Math.min(4,height/2)}" stroke="#fff" stroke-opacity=".35"/>`;
    }
    svg+=`<text x="${x}" y="${H-8}" text-anchor="middle" fill="#526176" font-size="10" font-weight="600">${index+1}</text>`;
  });
  return svg+'</svg>';
}
function onlinePlatformChart(model){
  if(model.total===null)return '<p class="online-state">No reported values in this period.</p>';
  model=onlinePlatformRanking(model);
  const id=onlineChartSpecs.push({kind:'platforms',model,width:0,height:0})-1;
  const legend=model.rows.map((row,index)=>`<div class="online-platform-item"><span class="online-platform-key" style="--platform-color:${onlinePlatformColor(row.platform)}">${index+1}</span><span class="online-legend-name" title="${esc(row.platform)}">${esc(row.platform)}</span><strong>${onlineMoney(row.sales)}</strong></div>`).join('');
  return `<div class="online-platform-viz"><div class="online-platform-chart" data-online-chart="${id}">${onlinePlatformColumns(model)}</div><div class="online-platform-legend" aria-label="Platform names and reported amounts">${legend}</div></div>`;
}
function onlineTrendChart(models,size={}){
  const present=models.filter(model=>model.total!==null);
  if(!present.length)return '<p class="online-state">No reported values for this trend.</p>';
  const {width=480,height=242,bars=false,weeks=false,compactWeeks=false}=size;
  const W=Math.max(180,width),H=Math.max(100,height),L=56,R=14,T=weeks?38:20,B=weeks?38:32,pw=W-L-R,ph=H-T-B;
  const observedMax=Math.max(0,...present.map(model=>model.total)),min=Math.min(0,...present.map(model=>model.total));
  const max=observedMax===min?min+1:observedMax,span=max-min;
  const slot=pw/models.length,x=index=>L+(index+.5)*slot,y=value=>T+(max-value)/span*ph;
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Online sales ${bars?'daily bars and line':'trend'} in rupees${weeks?' with dates and Monday–Sunday weeks':''}"><title>Online sales trend. Gaps indicate missing reporting.</title>`;
  if(weeks)for(const [index,group] of onlineWeekGroups(models).entries()){
    const left=L+group.start*slot,gw=(group.end-group.start+1)*slot;
    svg+=`<rect x="${left}" y="${T}" width="${gw}" height="${ph}" fill="${index%2?'#fafafa':'#f1f5f9'}"/><line x1="${left}" x2="${left}" y1="${T-4}" y2="${H-B}" stroke="#cbd5e1" stroke-dasharray="3 4"/>`;
    if(gw>55)svg+=`<text x="${left+gw/2}" y="17" text-anchor="middle" fill="#52525b" font-size="${W<600?9:11}">Week ${compactWeeks?index+1:esc(group.period.short)}<title>Monday–Sunday: ${esc(group.period.label)}</title></text>`;
  }
  for(let i=0;i<4;i++){const amount=min+span*i/3,yy=y(amount);svg+=`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="#e4e4e7"/><text x="${L-8}" y="${yy+4}" text-anchor="end" fill="#71717a" font-size="11">${esc(tick(amount))}</text>`;}
  svg+=`<line x1="${L}" x2="${W-R}" y1="${y(0)}" y2="${y(0)}" stroke="#a1a1aa"/>`;
  if(bars)models.forEach((model,index)=>{if(model.total!==null)svg+=`<rect x="${x(index)-slot*.3}" y="${Math.min(y(0),y(model.total))}" width="${slot*.6}" height="${Math.max(model.total===0?1:0,Math.abs(y(model.total)-y(0)))}" fill="#97aecb"><title>${esc(model.period.label)}: ${onlineMoney(model.total)}</title></rect>`;});
  let path='';models.forEach((model,index)=>{if(model.total===null){path='';return;}const point=`${x(index)},${y(model.total)}`;if(path)svg+=`<path d="M${path} L${point}" fill="none" stroke="#12356e" stroke-width="2.5"/>`;path=point;});
  models.forEach((model,index)=>{
    if(model.total!==null)svg+=`<circle cx="${x(index)}" cy="${y(model.total)}" r="${models.length>20?2.5:4}" fill="#12356e"><title>${esc(model.period.label)}: ${onlineMoney(model.total)}</title></circle>`;
    const stride=Math.max(1,Math.ceil(models.length/Math.max(2,pw/(weeks?36:95))));
    const last=models.length-1,spaced=index%stride===0&&x(last)-x(index)>=50&&x(index)-x(0)>=50;
    if(index===0||index===last||spaced)svg+=`<text x="${x(index)}" y="${H-17}" text-anchor="${index===0?'start':index===last?'end':'middle'}" fill="#52525b" font-size="${weeks?10:11}">${esc(model.period.mode==='monthly'?model.period.short:onlineDateLabel(model.period.start,true))}</text>`;
  });return svg+'</svg>';
}
function onlineDetails(model){
  return `<div class="scroll"><table class="online-table"><thead><tr><th>Platform</th><th class="n">Sales (INR)</th><th class="n">Reporting days</th><th>Source</th></tr></thead><tbody>${model.rows.map(row=>`<tr><td>${esc(row.platform)}</td><td class="n">${onlineMoney(row.sales)}</td><td class="n">${row.sales===null?'—':row.days||'—'}</td><td>${esc(row.source)}</td></tr>`).join('')}<tr class="tot"><td>Total reported</td><td class="n">${onlineMoney(model.total)}</td><td class="n">${model.days||'—'}</td><td>${model.reported} / ${model.rows.length} platforms</td></tr></tbody></table></div>`;
}
function pgOnline(){
  onlineNormalize();const data=onlinePayload(),selectedMonth=onlineSelectedMonth(data),anchor=OF.date||selectedMonth&&selectedMonth+'-01';
  if(!data.available||!anchor)return onlineEmpty(data);
  if(dashboardTvEnabled())return pgOnlineTv(data);
  const model=onlinePeriodData(OF.mode,anchor,OF.platform,data),mode={daily:'Daily',weekly:'Weekly',monthly:'Monthly'}[OF.mode];
  const month=onlinePeriodData('monthly',selectedMonth+'-01',OF.platform,data),timeline=onlineMonthTimeline(selectedMonth,OF.platform,data);
  const cards=`<div class="cards online-cards" data-dashboard-widget="online_summary">${kcard('₹',mode+' online sales',onlineMoney(model.total),esc(model.period.label),0,'online_summary')}${kcard('▧','Platforms reporting',model.reported+' / '+model.rows.length,'Entered values, including zero',1,'online_summary')}${kcard('▤','Days with daily records',String(model.days),OF.mode==='weekly'?'Monday–Sunday · entered days only':OF.mode==='monthly'?'Calendar month · entered days only':'Selected reporting date',2,'online_summary')}</div>`;
  const notice=`<p class="online-notice">Summary: ${esc(model.period.label)} · ${esc(OF.platform==='All'?'All platforms':OF.platform)}${model.total===null?' · No reported value in this summary period':''}</p>`;
  return `<div class="canvas online-sales">${cards}${onlineDataNotice(data)}${notice}<div class="online-grid"><section class="vis online-platform-panel" data-dashboard-widget="online_platforms"><h3>Monthly sales by platform · ${esc(month.period.label)}</h3><p class="online-period">${esc(onlineDateLabel(month.period.start)+' – '+onlineDateLabel(month.period.end))} · INR</p>${onlinePlatformChart(month)}</section><section class="vis online-trend-panel" data-dashboard-widget="online_trend"><h3>Daily bars and line · ${esc(month.period.label)} · INR</h3>${onlineChartBlock(timeline,{bars:true,weeks:true,compactWeeks:true})}${onlineWeeklyLabels(timeline)}<p class="note">Monday–Sunday weeks · daily subtotals within this calendar month · gaps mean no reporting</p></section><section class="vis online-detail-panel" data-dashboard-widget="online_platforms"><h3>Reported sales · all available months · INR</h3>${onlineHistoryTable(data,OF.platform)}</section></div><p class="online-source">${esc(onlineSourceNote(data))}</p></div>`;
}
function pgOnlineTv(data=onlinePayload()){
  return renderOnlineTvContent(data);
}
function renderOnlineTvContent(data=onlinePayload()){
  const model=onlineTvData(data);if(!model)return onlineEmpty(data);
  const {day,week,month,overall,trend}=model;
  const cards=`<div class="cards online-cards" data-dashboard-widget="online_summary">${[['Latest reported day',day],['Week · Monday–Sunday',week],['Calendar month',month]].map(([label,periodModel],index)=>kcard('₹','Online · '+label,onlineMoney(periodModel.total),index<2&&!model.dailyEnd?'No daily records':esc(periodModel.period.label)+(index===0?' · '+periodModel.reported+'/'+periodModel.rows.length+' platforms reported':''),index,'online_summary')).join('')}</div>`;
  const first=overall.months[0],last=overall.months.slice(-1)[0];
  const platforms=`<section class="vis online-tv-platforms" data-dashboard-widget="online_platforms"><h3>Monthly sales by platform · ${esc(month.period.label)}</h3><p class="online-period">All months ${esc(onlinePeriod('monthly',first+'-01').short+' – '+onlinePeriod('monthly',last+'-01').short)} · ${onlineMoney(overall.total)}</p>${onlinePlatformChart(month)}</section>`;
  const daily=trend.length?`${onlineChartBlock(trend,{bars:true,weeks:true,compactWeeks:true})}<p class="note">${esc(onlineDateLabel(model.dailyStart)+' – '+onlineDateLabel(model.dailyEnd))} · Monday–Sunday weeks · gaps mean no reporting</p>`:'<p class="online-state">No daily records have been reported. Monthly sales remain available.</p>';
  const dailyTitle=model.dailyMonth?onlinePeriod('monthly',model.dailyMonth+'-01').label:'No daily reporting';
  const dailyPanel=`<section class="vis online-tv-trend" data-dashboard-widget="online_trend"><h3>Daily online sales · ${esc(dailyTitle)} · INR</h3>${daily}</section>`;
  return `<div class="canvas online-sales online-tv online-tv-combined">${cards}${onlineDataNotice(data)}<div class="online-tv-grid">${platforms}${dailyPanel}</div><p class="online-source">${esc(onlineSourceNote(data))}</p></div>`;
}
