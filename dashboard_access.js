/* Display permissions complement the server's scoped dashboard data. An absent
   policy supports older saved reports; an explicit empty policy grants nothing. */
function dashboardPolicy(){return typeof window==='undefined'?null:window.DASHBOARD_ACCESS;}
function dashboardCan(group,key){
  const policy=dashboardPolicy();
  return policy==null||Array.isArray(policy[group])&&policy[group].includes(key);
}
function dashboardPageAllowed(id){
  return dashboardCan('pages',id)&&(id!=='team'||dashboardCan('drilldowns','executive'));
}
function dashboardViews(id=page){
  if(id!=='home'&&!PAGES.find(p=>p.id===id)?.region)return [];
  return ['overview','detail',...(SECONDARY[id]?['secondary']:[])].filter(view=>dashboardCan('views',view));
}
function dashboardUsablePage(id){
  const definition=PAGES.find(p=>p.id===id);
  return !!definition&&dashboardPageAllowed(id)&&(!(id==='home'||definition.region)||dashboardViews(id).length>0)&&
    (id!=='online'||['online_summary','online_platforms','online_trend'].some(key=>dashboardCan('widgets',key)));
}
function dashboardNormalizeLocation(){
  if(!dashboardUsablePage(page))page=PAGES.find(p=>!p.hidden&&dashboardUsablePage(p.id))?.id||PAGES.find(p=>dashboardUsablePage(p.id))?.id||null;
  if(!page)return false;
  const views=dashboardViews();
  if(views.length&&!views.includes(dashboardView())){
    if(page==='home')homeView=views[0];else channelViews[page]=views[0];
  }
  for(const [key,permission] of Object.entries({month:'month',dtt:'brand',exec:'exec',customer:'customer',dist:'sub',state:'state'})){
    if(!dashboardCan('filters',permission))F[key]=['All'];
  }
  for(const [key,permission] of Object.entries({month:'secondaryMonth',distributor:'distributor',store:'branch'})){
    if(!dashboardCan('filters',permission))LF[key]=['All'];
  }
  if(!dashboardCan('filters','fy')&&String(FY)!==String(DATA.fy))setFY(DATA.fy);
  const scopes=dashboardPolicy()?.scopes||{};
  for(const [key,scope] of Object.entries({exec:'executive',customer:'customer',dtt:'brand',dist:'distribution',state:'state'})){
    if(!Array.isArray(scopes[scope]))continue;
    const allowed=scopes[scope].map(String),chosen=filterValues(F[key]).filter(value=>allowed.includes(value));
    F[key]=filterAll(F[key])||!chosen.length?[...allowed]:chosen;
  }
  const allowedMonths=scopes.month;
  if(Array.isArray(allowedMonths)){
    const calendar=lfoMonths();
    MONTHS=[...new Set([...MONTHS,...calendar.filter(month=>allowedMonths.includes(month.split('-')[0]))])].sort((a,b)=>calendar.indexOf(a)-calendar.indexOf(b));
    SHORT=Object.fromEntries(MONTHS.map(month=>[month,month.split('-')[0]]));
    MS=MONTHS.map(month=>SHORT[month]);YOF=Object.fromEntries(MONTHS.map(month=>[SHORT[month],month.split('-')[1]]));
    const allowed=MONTHS.filter(month=>allowedMonths.includes(month.split('-')[0]));
    const chosen=filterValues(F.month).filter(month=>allowed.includes(month));
    F.month=filterAll(F.month)||!chosen.length?allowed:chosen;
    const secondaryAllowed=lfoMonths().filter(month=>allowedMonths.includes(month.split('-')[0]));
    const secondaryChosen=filterValues(LF.month).filter(month=>secondaryAllowed.includes(month));
    LF.month=filterAll(LF.month)||!secondaryChosen.length?secondaryAllowed:secondaryChosen;
  }
  return true;
}
function dashboardWidget(key,markup){return dashboardCan('widgets',key)?markup:'';}
function dashboardScopeOptions(key){
  const scope=({exec:'executive',customer:'customer',dtt:'brand',dist:'distribution',state:'state'})[key];
  const values=dashboardPolicy()?.scopes?.[scope];return Array.isArray(values)?values:[];
}
function dashboardReturnPage(){
  if(dashboardPolicy()==null||dashboardUsablePage('home'))return 'home';
  return PAGES.find(p=>!p.hidden&&dashboardUsablePage(p.id))?.id||PAGES.find(p=>dashboardUsablePage(p.id))?.id||'home';
}
function dashboardCardKey(label){
  const text=String(label).toLowerCase();
  if(text.includes('achievement')&&(text.includes('%')||text.includes('target')))return 'achievement_pct';
  if(text.includes('difference')||text.includes('balance'))return 'difference';
  if(text.includes('target'))return 'target';
  if(text.includes('unit'))return 'units';
  if(text.includes('sales entries')||text.includes('orders')||text.includes('branches')||text.includes('store months')||text.includes('months short'))return 'orders';
  return 'achievement';
}
function dashboardExecutiveFilterAllowed(key){return dashboardCan('filters',({channel:'sub',city:'state',state:'state'})[key]||key);}
function dashboardHrefAllowed(href){
  if(dashboardPolicy()==null||!href||href.startsWith('#'))return true;
  let url;try{url=new URL(href,window.location.href);}catch(e){return false;}
  if(url.origin!==window.location.origin)return true;
  if(url.pathname==='/reports/sales-breakdown')return dashboardCan('drilldowns','sales')||(dashboardCan('drilldowns','state')&&url.searchParams.get('focus')==='customer'&&url.searchParams.has('state'));
  if(url.pathname==='/'){
    const id=url.searchParams.get('page')||'home';
    return dashboardUsablePage(id)&&(!url.searchParams.has('view')||dashboardCan('views',url.searchParams.get('view')));
  }
  return true;
}
function dashboardEmpty(){return '<div class="canvas"><section class="vis dashboard-empty"><h3>No dashboard sections enabled</h3><p>Your account has no dashboard view selected. Ask your administrator to update Users &amp; access.</p></section></div>';}
function dashboardNonLink(anchor){
  const node=anchor.namespaceURI==='http://www.w3.org/2000/svg'
    ?document.createElementNS('http://www.w3.org/2000/svg','g'):document.createElement('span');
  for(const attr of anchor.attributes){
    if(['href','xlink:href','target','rel','role','tabindex','aria-label'].includes(attr.name))continue;
    node.setAttribute(attr.name,attr.value);
  }
  node.classList.remove('achieved-drilldown','state-drilldown','detail-link');
  node.style.cursor='default';
  node.append(...anchor.childNodes);anchor.replaceWith(node);return node;
}
/* Remove denied sections before inserting HTML into the live dashboard. These
   attributes also keep each UI block tied to one readable permission key. */
function dashboardMarkup(html){
  if(dashboardPolicy()==null)return html;
  const template=document.createElement('template');template.innerHTML=html;
  const root=template.content;
  for(const node of root.querySelectorAll('[data-dashboard-widget]'))if(!dashboardCan('widgets',node.dataset.dashboardWidget))node.remove();
  for(const node of root.querySelectorAll('[data-dashboard-filter]'))if(!dashboardCan('filters',node.dataset.dashboardFilter))node.remove();
  for(const node of root.querySelectorAll('[data-dashboard-drilldown]'))if(!dashboardCan('drilldowns',node.dataset.dashboardDrilldown))node.remove();
  for(const anchor of root.querySelectorAll('a[href]'))if(!dashboardHrefAllowed(anchor.getAttribute('href')))dashboardNonLink(anchor);
  for(const node of root.querySelectorAll('[data-goto]'))if(!dashboardUsablePage(node.dataset.goto)){
    const label=document.createElement('span');label.className=node.className;label.append(...node.childNodes);node.replaceWith(label);
  }
  for(const node of root.querySelectorAll('[data-secondary-breakdown]'))if(!dashboardCan('drilldowns','secondary')){
    node.removeAttribute('data-secondary-breakdown');node.removeAttribute('tabindex');node.removeAttribute('role');node.classList.remove('achieved-drilldown');
  }
  for(const node of root.querySelectorAll('[data-lfo-distributor]'))if(!dashboardCan('drilldowns','secondary')){
    const block=document.createElement('div');block.className=node.className;block.append(...node.childNodes);node.replaceWith(block);
  }
  for(const node of [...root.querySelectorAll('.cards,.overview-support,.overview-left,.overview-right,.team-breakup-actions')].reverse())if(!node.children.length)node.remove();
  const canvas=root.querySelector('.canvas');
  if(canvas&&!root.querySelector('.kcard,.vis'))return dashboardEmpty();
  return template.innerHTML;
}
let dashboardTvDateTimer=null;
function dashboardTvDate(now=new Date()){
  return new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short',year:'numeric'}).format(now);
}
function dashboardTvHeader(enabled){
  if(dashboardTvDateTimer!==null){clearInterval(dashboardTvDateTimer);dashboardTvDateTimer=null;}
  if(!enabled)return;
  const date=document.getElementById('tv-current-date');if(!date)return;
  const update=()=>{
    const now=new Date();
    date.textContent=dashboardTvDate(now);
    const parts=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',day:'2-digit',month:'2-digit',year:'numeric'}).formatToParts(now);
    const value=type=>parts.find(part=>part.type===type).value;
    date.dateTime=`${value('year')}-${value('month')}-${value('day')}`;
  };
  update();dashboardTvDateTimer=setInterval(update,60000);
}
/* Online Sales has a separate set of panels. Denying those panels must not
   change the layout of a fully granted primary-sales dashboard (or vice versa).
   Channel and data restrictions are enforced separately from presentation. */
function dashboardPartialLayout(){
  if(dashboardPolicy()==null)return false;
  const online=page==='online';
  const keys=DASHBOARD_WIDGET_KEYS.filter(key=>key.startsWith('online_')===online);
  return keys.some(key=>!dashboardCan('widgets',key));
}
function dashboardFitAccess(){
  // Presentation only: the normal policy still decides which data and widgets render.
  const tv=dashboardTvEnabled();
  document.body.classList.toggle('salesviewer-tv',tv);
  document.body.classList.toggle('salesviewer-brand',tv&&dashboardTvBrandVisible());
  dashboardTvHeader(tv);
  dashboardTvLive(tv);
  dashboardTvSlideshow(tv);
  const partial=dashboardPartialLayout();
  document.body.classList.toggle('dashboard-custom-layout',partial);
  if(!partial)return;
  for(const grid of document.querySelectorAll('#page .home-overview')){
    for(const wrapper of [...grid.querySelectorAll('.overview-support,.overview-left,.overview-right')].reverse())wrapper.replaceWith(...wrapper.childNodes);
    const visible=[...grid.children].map(node=>node.dataset.dashboardWidget);
    grid.classList.toggle('dashboard-two-charts',visible.length===2&&visible.includes('channel_sales')&&visible.includes('monthly_sales'));
  }
  for(const cards of document.querySelectorAll('#page .cards'))cards.style.setProperty('--visible-cards',String(Math.max(1,cards.children.length)));
}
const DASHBOARD_WIDGET_KEYS=['orders','units','target','achievement','difference','achievement_pct','channel_sales','executive_sales','retailer_sales','monthly_sales','monthly_quantity','achievement_gauge','executive_monthly','executive_customers','top_groups','alerts','yoy','sales_trend','product_sales','product_volume','fast_products','colours','orders_week','order_bands','region_sales','top_states','orders_state','orders_category','orders_month','cities','balance_monthly','balance_table','balance_channels','state_split','city_split','store_groups','store_master','store_categories','map','state_ranking','collections','secondary_groups','secondary_monthly','secondary_register','online_summary','online_platforms','online_trend'];

/* Poll the ordinary authenticated dashboard. Each request rechecks access and
   uses the server's normal SQL cache; it never forces a refresh or runs a sync. */
const DASHBOARD_LIVE_INTERVAL=20000;
let dashboardLiveActive=false,dashboardLiveTimer=null,dashboardLiveRequest=null;
let dashboardLiveSource=null,dashboardLiveHooks=false,dashboardLiveNeedsRender=false;
function dashboardLiveSnapshot(doc){
  const scripts=Array.from(doc.scripts).filter(script=>!script.src).map(script=>script.textContent.trim());
  const access=scripts.map(script=>script.match(/^((?:window\.[A-Z_]+=(?:true|false);)*)window\.DASHBOARD_ACCESS=(\{[^\r\n]*\});$/)).filter(Boolean);
  const payload=scripts.flatMap(script=>Array.from(script.matchAll(/^const DATA = ([^\r\n]+);\r?$/gm)));
  const identity=doc.querySelector('#account-profile .profile-name')?.textContent.trim();
  if(access.length!==1||payload.length!==1||!identity)throw new Error('Dashboard response is incomplete');
  const policy=JSON.parse(access[0][2]),data=JSON.parse(payload[0][1]);
  if(!policy||policy.version!==1||!['pages','views','widgets','filters','drilldowns'].every(key=>Array.isArray(policy[key]))||
     !policy.scopes||typeof policy.scopes!=='object')throw new Error('Invalid dashboard access');
  if(!data||typeof data!=='object'||Array.isArray(data)||!Array.isArray(data.rows)||!data.rows.every(Array.isArray)||
     !['string','number'].includes(typeof data.fy)||!Array.isArray(data.months))throw new Error('Invalid dashboard data');
  if(Object.keys(data).some(key=>['__proto__','constructor','prototype'].includes(key)))throw new Error('Invalid data key');
  return {identity,access:access[0][0],json:payload[0][1],data};
}
function dashboardLiveStatus(message=''){
  const node=document.getElementById('tv-live-status');if(!node)return;
  node.textContent=message;node.hidden=!message;
}
function dashboardLiveStop(){
  dashboardLiveActive=false;clearTimeout(dashboardLiveTimer);dashboardLiveTimer=null;
  dashboardTvSlideshowStop();
  if(dashboardLiveRequest)dashboardLiveRequest.abort();dashboardLiveRequest=null;
}
function dashboardLiveClear(){
  if(typeof resetOnlineCharts==='function')resetOnlineCharts();
  dashboardTvPresentationStop();
  document.body.classList.remove('salesviewer-brand');
  document.getElementById('page')?.replaceChildren();
  const tooltip=document.getElementById('tip');if(tooltip){tooltip.replaceChildren();tooltip.style.opacity=0;}
  dashboardLiveNeedsRender=true;
}
function dashboardLiveLeave(path){
  dashboardLiveStop();
  dashboardLiveClear();
  for(const key of Object.keys(DATA))delete DATA[key];
  window.location.replace(path);
}
function dashboardLiveSchedule(){
  clearTimeout(dashboardLiveTimer);
  if(dashboardLiveActive)dashboardLiveTimer=setTimeout(dashboardLivePoll,DASHBOARD_LIVE_INTERVAL);
}
async function dashboardLivePoll(){
  if(!dashboardLiveActive||dashboardLiveRequest)return;
  clearTimeout(dashboardLiveTimer);
  if(document.hidden){dashboardLiveSchedule();return;}
  const controller=new AbortController();dashboardLiveRequest=controller;
  const timeout=setTimeout(()=>controller.abort(),15000);
  let received=false;
  try{
    const response=await fetch('/',{credentials:'same-origin',cache:'no-store',headers:{Accept:'text/html'},signal:controller.signal});
    if(!dashboardLiveActive||dashboardLiveRequest!==controller)return;
    received=true;
    const url=new URL(response.url,window.location.origin);
    if(url.origin!==window.location.origin)throw new Error('Unexpected dashboard origin');
    if(response.status===401||url.pathname==='/login'){dashboardLiveLeave('/login?next=%2F');return;}
    if(response.status===403){dashboardLiveLeave('/');return;}
    if(!response.ok||url.pathname!=='/'||!(response.headers.get('content-type')||'').includes('text/html'))throw new Error('Dashboard temporarily unavailable');
    const html=await response.text();
    if(!dashboardLiveActive||dashboardLiveRequest!==controller)return;
    const next=dashboardLiveSnapshot(new DOMParser().parseFromString(html,'text/html'));
    // A policy or user change requires fresh authorization and fresh chrome.
    if(next.identity!==dashboardLiveSource.identity||next.access!==dashboardLiveSource.access){dashboardLiveLeave('/');return;}
    if(next.json!==dashboardLiveSource.json||dashboardLiveNeedsRender){
      for(const key of Object.keys(DATA))delete DATA[key];
      Object.assign(DATA,next.data);
      setFY(DATA.fy);
      document.getElementById('lastup').textContent=DATA.fetched||'';
      render();
      dashboardLiveSource=next;
      dashboardLiveNeedsRender=false;
    }
    dashboardLiveStatus();
  }catch(error){
    if(dashboardLiveActive&&dashboardLiveRequest===controller){
      // A returned page that cannot confirm access must not retain old charts.
      if(received)dashboardLiveClear();
      dashboardLiveStatus('Connection interrupted — retrying…');
    }
  }finally{
    clearTimeout(timeout);
    if(dashboardLiveRequest===controller){dashboardLiveRequest=null;dashboardLiveSchedule();}
  }
}
function dashboardTvLive(enabled){
  if(!enabled){dashboardLiveStop();return;}
  if(dashboardLiveActive)return;
  try{dashboardLiveSource=dashboardLiveSnapshot(document);}catch(error){return;}
  dashboardLiveActive=true;
  if(!dashboardLiveHooks){
    const resume=()=>{if(dashboardLiveActive&&!document.hidden)dashboardLivePoll();};
    document.addEventListener('visibilitychange',resume);window.addEventListener('online',resume);
    dashboardLiveHooks=true;
  }
  dashboardLiveSchedule();
}

function dashboardTvEnabled(){
  return document.querySelector('#account-profile .profile-name')?.textContent.trim().toLowerCase()==='salesviewer';
}
/* The slides only render the already-authorized payload. Polling remains the
   sole data request, and an unconfirmed response pauses all slide changes. */
const DASHBOARD_SLIDE_INTERVAL=10000;
const DASHBOARD_SLIDE_DURATION=1000;
let dashboardSlideTimer=null,dashboardOnlineSlide='combined',dashboardHomeSlide='brand',dashboardSlideStarted=false;
let dashboardSlideOutgoing=null,dashboardSlideAnimations=[],dashboardSlideRendering=false,dashboardSlideGeneration=0;
function dashboardTvPrepareSlide(){
  if(dashboardSlideStarted||!dashboardTvEnabled()||!dashboardUsablePage('home'))return;
  dashboardSlideStarted=true;dashboardHomeSlide='brand';page='home';
}
function dashboardTvBrandVisible(){
  return dashboardTvEnabled()&&page==='home'&&dashboardHomeSlide==='brand'&&dashboardUsablePage('home');
}
function dashboardTvBrandMarkup(){
  return `<section class="tv-brand-slide" aria-label="Doctor Towels company logo">${
    TV_BRAND_IMAGE.startsWith('data:image/png;base64,')
      ?`<img src="${TV_BRAND_IMAGE}" alt="Doctor Towels — It's safe" width="1707" height="960">`
      :'<span>Doctor Towels</span>'}</section>`;
}
function dashboardTvSlideOptions(){
  const slides=[{page:'home',variant:'brand',label:'Doctor Towels',action:'Show Doctor Towels'},
                {page:'home',variant:null,label:'Dashboard',action:'Show Dashboard'}];
  if(['online_summary','online_platforms','online_trend'].some(key=>dashboardCan('widgets',key)))
    slides.push({page:'online',variant:'combined',label:'Online Sales',action:'Show Online Sales'});
  return slides;
}
function dashboardTvShowSlide(nextPage,nextVariant=''){
  if(!dashboardTvEnabled()||!dashboardLiveActive||dashboardLiveNeedsRender||!dashboardUsablePage('home')||!dashboardUsablePage('online'))return false;
  const next=dashboardTvSlideOptions().find(slide=>slide.page===nextPage&&(slide.variant||'')===nextVariant);
  if(!next||!dashboardUsablePage(next.page))return false;
  dashboardTvSlideshowStop();
  dashboardTvAdvance(next);dashboardTvSlideshow(true);
  return true;
}
function dashboardTvReducedMotion(){
  return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}
/* Freeze only layout containers. SVG chart geometry and inline colours already
   describe the picture; copying every SVG computed style would delay older TVs. */
function dashboardTvCaptureSlide(){
  dashboardTvPresentationStop();
  if(!dashboardTvEnabled()||dashboardLiveNeedsRender||dashboardTvReducedMotion())return;
  const app=document.querySelector('.app');
  if(!app||typeof app.animate!=='function'||typeof window.getComputedStyle!=='function')return;
  const clone=app.cloneNode(true);
  const sources=[app,...app.querySelectorAll('*')],copies=[clone,...clone.querySelectorAll('*')];
  for(let i=0;i<sources.length;i++){
    const copy=copies[i];copy.removeAttribute('id');
    if(!sources[i].matches('.app,.report,.rhead,.body,.canvas,.cards,.vgrid,.vis,.tv-brand-slide'))continue;
    const style=window.getComputedStyle(sources[i]);
    for(const property of style)copy.style.setProperty(property,style.getPropertyValue(property),'important');
    copy.style.setProperty('animation','none','important');
    copy.style.setProperty('transition','none','important');
  }
  const frame=document.createElement('div');
  frame.className='tv-slide-outgoing';frame.setAttribute('aria-hidden','true');frame.inert=true;
  frame.style.backgroundColor=window.getComputedStyle(document.body).backgroundColor;
  frame.append(clone);document.body.append(frame);dashboardSlideOutgoing=frame;
}
function dashboardTvAdvance(next){
  dashboardTvCaptureSlide();
  dashboardSlideRendering=true;
  try{
    page=next.page;
    if(page==='home')dashboardHomeSlide=next.variant||'';
    else if(next.variant)dashboardOnlineSlide=next.variant;
    render();
  }finally{dashboardSlideRendering=false;}
  dashboardTvPresentation(true,true);
}
function dashboardTvBeforeRender(){
  // Polls and user actions cancel an in-flight picture before updating the DOM.
  if(!dashboardSlideRendering)dashboardTvPresentationStop();
}
function dashboardTvPresentationStop(){
  dashboardSlideGeneration++;
  const animations=dashboardSlideAnimations;dashboardSlideAnimations=[];
  for(const animation of animations)animation.cancel();
  dashboardSlideOutgoing?.remove();dashboardSlideOutgoing=null;
}
function dashboardTvPresentation(enabled,advance=false){
  if(!enabled||dashboardLiveNeedsRender){dashboardTvPresentationStop();return;}
  if(!advance)return;
  const outgoing=dashboardSlideOutgoing,app=document.querySelector('.app');
  if(!outgoing||!app||dashboardTvReducedMotion()){dashboardTvPresentationStop();return;}
  const generation=dashboardSlideGeneration;
  const options={duration:DASHBOARD_SLIDE_DURATION,easing:'cubic-bezier(.65,0,.35,1)',fill:'both'};
  try{
    dashboardSlideAnimations=[
      outgoing.animate([{transform:'translate3d(0,0,0)'},{transform:'translate3d(-100%,0,0)'}],options),
      app.animate([{transform:'translate3d(100%,0,0)'},{transform:'translate3d(0,0,0)'}],options)
    ];
    Promise.all(dashboardSlideAnimations.map(animation=>animation.finished)).then(()=>{
      if(generation===dashboardSlideGeneration)dashboardTvPresentationStop();
    }).catch(()=>{if(generation===dashboardSlideGeneration)dashboardTvPresentationStop();});
  }catch(error){dashboardTvPresentationStop();}
}
function dashboardTvSlideshowStop(){
  clearTimeout(dashboardSlideTimer);dashboardSlideTimer=null;
  dashboardTvPresentationStop();
}
function dashboardTvSlideshow(enabled){
  if(!enabled||!dashboardLiveActive||!dashboardUsablePage('home')||!dashboardUsablePage('online')){
    dashboardTvSlideshowStop();return;
  }
  dashboardTvPresentation(true);
  if(dashboardSlideTimer!==null)return;
  dashboardSlideTimer=setTimeout(()=>{
    dashboardSlideTimer=null;
    if(!dashboardLiveActive)return;
    if(!document.hidden&&!dashboardLiveNeedsRender){
      const slides=dashboardTvSlideOptions();
      const variant=page==='home'?dashboardHomeSlide:dashboardOnlineSlide;
      const current=slides.findIndex(slide=>slide.page===page&&(slide.variant||'')===(variant||''));
      const next=slides[(current+1)%slides.length];
      dashboardTvAdvance(next);
    }
    dashboardTvSlideshow(dashboardTvEnabled());
  },DASHBOARD_SLIDE_INTERVAL);
}
