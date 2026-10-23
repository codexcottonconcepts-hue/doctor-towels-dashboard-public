/* Keep a clicked achievement in the same channel, year and filter context. */
function salesBreakdownUrl(overrides={}){
  const params=new URLSearchParams({fy:String(FY),return_page:pageDef().id||'home'});
  const values={channel:pageDef().region?[pageDef().region]:[],month:F.month,
    executive:F.exec,brand:F.dtt,distribution:F.dist,state:F.state,customer:F.customer,...overrides};
  for(const [key,value] of Object.entries(values)){
    if(!filterAll(value))for(const item of filterValues(value))params.append(key,item);
  }
  return '/reports/sales-breakdown?'+params.toString();
}

function dashboardStateName(name){
  const aliases={'Dadra & Nagar Haveli and Daman & Diu':'Dadra and Nagar Haveli and Daman and Diu',
    'Daman & Diu':'Dadra and Nagar Haveli and Daman and Diu'};
  return ((DATA.map&&DATA.map.nameFix)||{})[name]||aliases[name]||name;
}
function stateBreakdownUrl(name,{exact=false}={}){
  // A raw table row is one ledger label; a map region can combine aliases.
  // Resolve only rows in the current selection, never broaden an empty region.
  const states=[...new Set(baseRows().map(row=>row[ST]).filter(state=>
    exact?state===name:dashboardStateName(state)===name))];
  if(!states.length)return null;
  return salesBreakdownUrl({state:states,focus:'customer',
    return_view:dashboardView()==='detail'?'detail':'overview'});
}

function initAchievementLocation(){
  const params=new URLSearchParams(window.location.search);
  if(params.get('drilldown')!=='1')return;
  const requestedPage=params.get('page');
  if(PAGES.some(p=>p.id===requestedPage))page=requestedPage;
  if(params.get('view')==='detail'){
    if(page==='home')homeView='detail';
    else if(pageDef().region)channelViews[page]='detail';
  }
  const fy=params.get('fy');
  if((DATA.fys||[DATA.fy]).map(String).includes(fy))setFY(fy);
  for(const [key,param] of Object.entries({month:'month',dtt:'dtt',exec:'exec',dist:'dist',state:'state',customer:'customer'})){
    const selected=filterValues(params.getAll(param));
    F[key]=selected.length?selected:['All'];
  }
}
