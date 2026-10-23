/* Primary retailer/brand targets are independent from secondary store plans. */
const RETAILER_BRANDS=[{key:'dt',name:'Doctor Towel',label:'DT'},{key:'tt',name:'Thirsty Towel',label:'TT'}];
const retailerNameKey=value=>String(value??'').trim().replace(/\s+/g,' ').toLocaleLowerCase();
const retailerBrandKey=value=>({dt:'dt','doctor towel':'dt',tt:'tt','thirsty towel':'tt'})[retailerNameKey(value)]||null;
const retailerChannel=value=>({'D&R-S':'D&R - S','D&R-N':'D&R - N','L-LFO':'L - LFO'})[value]||value;
function retailerTargetMonths(){
  return ['Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec','Jan','Feb','Mar']
    .map((month,index)=>`${month}-${Number(FY)+(index>8?1:0)}`);
}
function retailerTargetsModel(rows,filters=F,definition=pageDef()){
  const channel=retailerChannel(definition.region||'LFO'),yearMonths=retailerTargetMonths();
  const months=yearMonths.filter(month=>filterMatches(filters.month,month));
  const selectedBrands=RETAILER_BRANDS.filter(brand=>filterAll(filters.dtt)||
    filterValues(filters.dtt).some(value=>retailerBrandKey(value)===brand.key));
  const unsupported=['exec','state','customer'].filter(key=>!filterAll(filters[key]));
  const targetNote=unsupported.length?'No primary retailer/brand target allocation for the selected '+
    unsupported.map(key=>({exec:'executive',state:'state',customer:'customer'})[key]).join(' / ')+'.':'';
  const targets=(DATA.primaryRetailerTargets||[]).filter(target=>String(target.fy)===String(FY)&&retailerChannel(target.channel)===channel);
  const sales=(rows||[]).filter(row=>String(row[11])===String(FY)&&retailerChannel(row[1])===channel&&
    filterMatches(filters.month,row[0])&&filterMatches(filters.exec,row[2])&&filterMatches(filters.state,row[3])&&
    filterMatches(filters.dist,row[6])&&filterMatches(filters.customer,row[12])&&
    selectedBrands.some(brand=>retailerBrandKey(row[5])===brand.key));
  const names=new Map();
  const addName=name=>{const text=String(name??'').trim();if(text&&!names.has(retailerNameKey(text)))names.set(retailerNameKey(text),text);};
  for(const row of sales)addName(row[6]);
  for(const target of targets)addName(target.retailer);
  const selectedRetailer=name=>filterAll(filters.dist)||filterValues(filters.dist).some(value=>retailerNameKey(value)===retailerNameKey(name));
  const knownAmount=value=>value!==null&&value!==undefined&&value!==''&&
    (typeof value==='number'||typeof value==='string'&&value.trim()!=='')&&Number.isFinite(Number(value))&&Number(value)>=0;
  const sumTargets=values=>values.reduce((total,value)=>total+Math.round(Number(value)*100),0)/100;
  const retailers=[...names.values()].filter(selectedRetailer).map(name=>{
    const own=sales.filter(row=>retailerNameKey(row[6])===retailerNameKey(name));
    const result={name,rows:own};
    for(const brand of RETAILER_BRANDS){
      const selected=selectedBrands.some(value=>value.key===brand.key);
      const achievedRows=own.filter(row=>retailerBrandKey(row[5])===brand.key);
      const assigned=targets.filter(target=>retailerNameKey(target.retailer)===retailerNameKey(name)&&retailerBrandKey(target.brand)===brand.key);
      const slots=months.map(month=>assigned.filter(target=>target.month===month));
      const complete=selected&&!unsupported.length&&slots.length>0&&slots.every(slot=>slot.length===1&&knownAmount(slot[0].amount));
      const target=complete?sumTargets(slots.map(slot=>slot[0].amount)):null;
      const reason=!selected?'Brand not selected':targetNote||(!complete?'No complete primary target for this retailer, brand and selected months.':target===0?'The assigned primary target is zero.':'');
      result[brand.key]={selected,target,targetComplete:complete,targetReason:reason,
        achieved:achievedRows.reduce((total,row)=>total+row[9],0),rows:achievedRows};
    }
    const chosen=selectedBrands.map(brand=>result[brand.key]);
    result.achieved=chosen.reduce((total,brand)=>total+brand.achieved,0);
    result.target=chosen.length&&chosen.every(brand=>brand.targetComplete)?sumTargets(chosen.map(brand=>brand.target)):null;
    result.percent=result.target>0?result.achieved/result.target*100:null;
    return result;
  }).sort((a,b)=>b.achieved-a.achieved||a.name.localeCompare(b.name));
  return {channel,months,yearly:filterAll(filters.month),filters:{...filters},retailers,targetNote,
    targetScopeAvailable:unsupported.length===0};
}
function retailerTargetSalesUrl(model,retailer){
  // Exact ledger labels also preserve alternate display casing without a fuzzy match.
  return salesBreakdownUrl({channel:[model.channel],month:model.filters.month,executive:model.filters.exec,
    state:model.filters.state,customer:model.filters.customer,
    distribution:[...new Set(retailer.rows.map(row=>row[6]))],brand:[...new Set(retailer.rows.map(row=>row[5]))]});
}
function retailerTargetsPanel(rows,filters=F,definition=pageDef()){
  const model=retailerTargetsModel(rows,filters,definition);
  if(!model.retailers.length)return '<p class="note">No retailers match this selection.</p>';
  const brands=RETAILER_BRANDS.filter(brand=>model.retailers.some(retailer=>retailer[brand.key].selected));
  if(!brands.length)return '<p class="note">No DT / TT brands match this selection.</p>';
  const colours={target:'#9aa8bd',achieved:'#12356e'};
  const exact=value=>'₹'+Number(value).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const compact=value=>{const n=Math.abs(value),div=n>=1e7?1e7:n>=1e5?1e5:n>=1e3?1e3:1;
    return (value/div).toLocaleString('en-IN',{maximumFractionDigits:div===1?2:1})+(div===1e7?'Cr':div===1e5?'L':div===1e3?'K':'');};
  const max=Math.max(1,...model.retailers.flatMap(retailer=>[retailer.target||0,Math.max(0,retailer.achieved)]));
  const legend=['Target','Achieved'].map(label=>
    `<span><i class="sw" style="background:${colours[label.toLowerCase()]}"></i>${label}</span>`).join('');
  const columns=model.retailers.map(retailer=>{
    const tooltip=`<b>${esc(retailer.name)}</b><br>Target: ${retailer.target===null?'Not set':exact(retailer.target)}<br>Achieved: ${exact(retailer.achieved)}`;
    const target=retailer.target===null?'<span class="retailer-target-unset" aria-hidden="true"></span>':
      `<span class="comparison-column-bar target" data-value="${retailer.target}" title="${esc(retailer.name+' target '+exact(retailer.target))}" data-tip="${esc(tooltip)}" style="height:${retailer.target/max*100}%;background:${colours.target}"><span class="comparison-column-value">${compact(retailer.target)}</span></span>`;
    const tag=retailer.rows.length?'a':'span',description=retailer.name+' achieved '+exact(retailer.achieved)+(retailer.rows.length?'. View sales entries':'');
    const achieved=`<${tag} class="comparison-column-bar achieved${retailer.rows.length?' achieved-drilldown':''}" data-value="${retailer.achieved}"${retailer.rows.length?` href="${esc(retailerTargetSalesUrl(model,retailer))}"`:''} aria-label="${esc(description)}" title="${esc(description)}" data-tip="${esc(tooltip)}" style="height:${Math.max(0,retailer.achieved)/max*100}%;min-height:${retailer.achieved>0?3:0}px;background:${colours.achieved}"><span class="comparison-column-value">${compact(retailer.achieved)}</span></${tag}>`;
    const percent=retailer.percent===null?(retailer.target===null?'Not set':'N/A'):retailer.percent.toFixed(1).replace(/\.0$/,'')+'%';
    const reason=model.targetNote||(retailer.target===null?'No complete primary target for this retailer and selected brands and months.':retailer.target===0?'The assigned primary target is zero.':`Achieved / target: ${exact(retailer.achieved)} / ${exact(retailer.target)}`);
    return `<div class="comparison-column" role="group" aria-label="${esc(retailer.name)}"><div class="comparison-pair" style="grid-template-columns:repeat(2,minmax(0,24px))">${target}${achieved}</div><span class="comparison-name" title="${esc(retailer.name)}">${esc(retailer.name)}</span><div class="comparison-attainment"><span title="${esc(reason)}">${percent}</span></div></div>`;
  }).join('');
  return `<style>
.comparison-retailers .comparison-columns{padding:26px 0 0}
.comparison-retailers .comparison-column{flex:1 0 82px;min-width:82px;grid-template-rows:minmax(0,1fr) 18px 16px;padding:0 5px}
.comparison-retailers .comparison-pair{gap:6px}
.comparison-retailers .comparison-column-value{bottom:calc(100% + 2px);padding:0;transform:translateX(-50%);font-size:9px;line-height:1}
.comparison-retailers .target .comparison-column-value{bottom:calc(100% + 13px)}
.comparison-retailers .comparison-attainment{display:flex;flex-direction:row;align-items:center;justify-content:center;gap:8px;font-size:9px;font-weight:700}
.comparison-retailers .comparison-name{display:block;font-size:11px;line-height:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-top:2px}
.comparison-retailers .leg{gap:10px;flex-wrap:wrap;font-size:10px}
</style><div class="comparison-chart comparison-retailers"><div class="leg">${legend}</div><div class="comparison-list" tabindex="0" role="region" aria-label="Retailer targets and achieved sales"><div class="comparison-columns">${columns}</div></div></div><p class="note retailer-target-note">${esc(model.targetNote||'Primary taxable sales · '+(model.yearly?'full-year':'selected-month')+' targets. Not set = no complete assigned target.')}</p>`;
}
