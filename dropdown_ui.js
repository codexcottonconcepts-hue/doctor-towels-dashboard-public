/* Progressive select UI. Native selects own values, validation and submitted data.
   Multiple selects stage choices until Apply; first value="All" is exclusive. */
(()=>{
  'use strict';
  if(window.dtDropdownUI)return;
  const controls=new Map();
  let sequence=0,opened=null,scheduled=false,invalidFocus=null;
  const icons={
    calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18M8 15h2M14 15h2"/>',
    person:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
    location:'<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
    store:'<path d="M3 10h18l-2-6H5l-2 6ZM4 10v10h16V10M9 20v-6h6v6M8 4l-1 6M16 4l1 6"/>',
    list:'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h1M12 8h4M8 12h1M12 12h4M8 16h1M12 16h4"/>'
  };
  function svg(path,cls){
    const el=document.createElementNS('http://www.w3.org/2000/svg','svg');
    el.setAttribute('viewBox','0 0 24 24');el.setAttribute('fill','none');
    el.setAttribute('stroke','currentColor');el.setAttribute('stroke-width','1.7');
    el.setAttribute('stroke-linecap','round');el.setAttribute('stroke-linejoin','round');
    el.setAttribute('class',cls);el.setAttribute('aria-hidden','true');el.innerHTML=path;
    return el;
  }
  function labelFor(select){
    if(select.getAttribute('aria-label'))return select.getAttribute('aria-label');
    const refs=(select.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean);
    const referenced=refs.map(id=>document.getElementById(id)?.textContent||'').join(' ').trim();
    if(referenced)return referenced;
    const labels=Array.from(select.labels||[]).map(label=>{
      const clone=label.cloneNode(true);
      clone.querySelectorAll('select,.dt-select').forEach(el=>el.remove());
      return clone.textContent.trim();
    }).filter(Boolean);
    return labels.join(' ')||select.name?.replace(/_/g,' ')||'Choose an option';
  }
  function allOption(c){
    const first=c.select.options[0];return first?.value==='All'?first:null;
  }
  function selectionText(c,options){
    const all=allOption(c),selected=Array.from(options);
    if(all&&selected.includes(all))return {text:all.label,title:all.label};
    return {text:selected.length>1?selected.length+' selected':selected[0]?.label||'Select…',
      title:selected.map(option=>option.label).join(', ')};
  }
  function initialDraft(c){
    const draft=new Set(c.select.selectedOptions),all=allOption(c);
    if(all&&(draft.has(all)||!draft.size))return new Set([all]);
    return draft;
  }
  function eligible(select){
    return !select.hidden&&(select.size<=1||select.hasAttribute('data-multi-filter'));
  }
  function sync(c){
    const s=c.select,option=s.selectedOptions[0];
    const summary=c.multiple?selectionText(c,s.selectedOptions):{text:option?.label||'Select…',title:option?.label||''};
    c.value.textContent=summary.text;
    c.button.title=summary.title;
    c.button.disabled=s.matches(':disabled');
    c.button.setAttribute('aria-label',labelFor(s)+(c.multiple?': '+summary.text:''));
    c.button.setAttribute('aria-required',String(s.required));
    if(s.getAttribute('aria-describedby'))c.description=s.getAttribute('aria-describedby');
    if(c.invalid&&s.validity.valid){c.invalid=false;c.error.textContent='';}
    c.button.setAttribute('aria-invalid',String(!!c.invalid));
    c.button.setAttribute('aria-describedby',[c.description,c.invalid?c.error.id:''].filter(Boolean).join(' '));
    if(opened===c&&c.button.disabled)close();
  }
  function close(){
    if(!opened)return;
    opened.button.setAttribute('aria-expanded','false');
    opened.button.removeAttribute('aria-activedescendant');
    opened.menu.remove();opened.menu=null;opened.draft=null;opened=null;
  }
  function position(c){
    if(opened!==c)return;
    const rect=c.button.getBoundingClientRect();
    if(!c.button.isConnected||!rect.width||!rect.height){close();return;}
    const vw=document.documentElement.clientWidth,vh=window.innerHeight,gap=6,edge=8;
    const width=Math.min(Math.max(rect.width,240),vw-edge*2);
    c.menu.style.width=width+'px';
    const below=vh-rect.bottom-gap-edge,above=rect.top-gap-edge;
    const chrome=c.menu.getBoundingClientRect().height-c.list.getBoundingClientRect().height;
    const flip=below<Math.min(c.list.scrollHeight,240)+chrome&&above>below;
    const available=Math.max(44,Math.min(300,(flip?above:below)-chrome));
    c.list.style.maxHeight=available+'px';
    const height=c.menu.getBoundingClientRect().height;
    c.menu.style.left=Math.max(edge,Math.min(rect.left,vw-width-edge))+'px';
    c.menu.style.top=Math.max(edge,Math.min(flip?rect.top-gap-height:rect.bottom+gap,vh-height-edge))+'px';
  }
  function activate(c,index,scroll=true){
    c.active=index;
    c.items.forEach(item=>item.node.classList.toggle('is-active',item.index===index));
    const item=c.items.find(item=>item.index===index);
    if(!item){c.button.removeAttribute('aria-activedescendant');return;}
    c.button.setAttribute('aria-activedescendant',item.node.id);
    if(scroll){
      const top=item.node.offsetTop-c.list.offsetTop,bottom=top+item.node.offsetHeight;
      if(top<c.list.scrollTop)c.list.scrollTop=top;
      else if(bottom>c.list.scrollTop+c.list.clientHeight)c.list.scrollTop=bottom-c.list.clientHeight;
    }
  }
  function visibleOptions(c){
    const query=(c.search?.value||'').trim().toLocaleLowerCase();
    return Array.from(c.select.options).filter(o=>!o.hidden&&!o.closest('optgroup[hidden]')&&
      (!query||o.value==='All'||o.label.toLocaleLowerCase().includes(query)));
  }
  function buildList(c){
    c.list.replaceChildren();c.items=[];
    let lastGroup=null;
    visibleOptions(c).forEach(option=>{
      const group=option.closest('optgroup');
      if(group&&group!==lastGroup){
        const heading=document.createElement('div');heading.className='dt-select-group';
        heading.textContent=group.label;heading.setAttribute('role','presentation');c.list.append(heading);
      }
      lastGroup=group;
      const index=option.index,node=document.createElement('div');
      const disabled=option.disabled||!!group?.disabled;
      node.id=c.uid+'-option-'+index;node.className='dt-select-option';
      node.setAttribute('role','option');node.setAttribute('aria-selected',String(c.multiple?c.draft.has(option):option.selected));
      node.setAttribute('aria-disabled',String(disabled));
      if(c.multiple){
        const check=document.createElement('span');check.className='dt-select-check';check.setAttribute('aria-hidden','true');
        check.append(svg('<path d="m5 12 4 4L19 6"/>','dt-select-checkmark'));node.append(check);
      }
      const text=document.createElement('span');text.className='dt-select-option-label';text.textContent=option.label;node.append(text);
      node.addEventListener('pointerdown',event=>event.preventDefault());
      node.addEventListener('pointermove',()=>{if(!disabled)activate(c,index,false);});
      node.addEventListener('click',()=>{if(!disabled)choose(c,index);});
      c.list.append(node);c.items.push({index,node,disabled,option});
    });
    if(!c.items.length){
      const empty=document.createElement('div');empty.className='dt-select-empty';
      empty.textContent='No options available';c.list.append(empty);
    }
  }
  function open(c){
    sync(c);if(c.button.disabled)return;
    close();opened=c;
    c.menu=document.createElement('div');c.menu.className='dt-select-menu'+(c.multiple?' is-multiple':'');
    c.list=document.createElement('div');c.list.className='dt-select-options';
    c.list.id=c.uid+'-list';c.list.setAttribute('role','listbox');
    c.list.setAttribute('aria-label',labelFor(c.select));
    if(c.multiple){
      c.draft=initialDraft(c);c.list.setAttribute('aria-multiselectable','true');
      const hint=document.createElement('div');hint.className='dt-select-multi-hint';
      hint.textContent='Choose options, then Apply';c.menu.append(hint);
    }
    c.search=null;
    if(c.select.hasAttribute('data-searchable')){
      c.search=document.createElement('input');c.search.type='search';c.search.className='dt-select-search';
      c.search.placeholder='Search '+labelFor(c.select).toLowerCase();
      c.search.setAttribute('aria-label','Search '+labelFor(c.select));
      c.search.addEventListener('input',()=>{buildList(c);position(c);activate(c,-1,false);});
      c.search.addEventListener('keydown',event=>{
        if(event.key==='ArrowDown'){
          event.preventDefault();c.button.focus({preventScroll:true});
          activate(c,c.items.find(item=>!item.disabled&&item.option.value!=='All')?.index??0);
        }else if(event.key==='Enter'){
          event.preventDefault();
          const match=c.items.find(item=>!item.disabled&&item.option.value!=='All');
          if(match)choose(c,match.index);
        }
      });
      c.menu.append(c.search);
    }
    c.menu.append(c.list);
    if(c.multiple){
      const actions=document.createElement('div');actions.className='dt-select-actions';
      c.reset=document.createElement('button');c.reset.type='button';c.reset.className='dt-select-reset';
      c.reset.textContent=allOption(c)?'Reset to All':'Clear';
      c.reset.addEventListener('click',()=>{
        const all=allOption(c);
        if(all&&(all.disabled||all.hidden||all.closest('optgroup[disabled],optgroup[hidden]')))return;
        c.draft=all?new Set([all]):new Set();refreshDraft(c);
      });
      c.apply=document.createElement('button');c.apply.type='button';c.apply.className='dt-select-apply';c.apply.textContent='Apply';
      c.apply.addEventListener('click',()=>apply(c));actions.append(c.reset,c.apply);c.menu.append(actions);
      c.menu.addEventListener('keydown',event=>{
        if(event.key==='Escape'){
          event.preventDefault();event.stopPropagation();close();c.button.focus({preventScroll:true});
        }else if(event.key==='Tab'&&((event.target===c.reset&&event.shiftKey)||(event.target===c.apply&&!event.shiftKey))){
          event.preventDefault();c.button.focus({preventScroll:true});
        }
      });
    }
    document.body.append(c.menu);
    c.button.setAttribute('aria-expanded','true');
    buildList(c);position(c);
    const selected=c.items.find(item=>item.index===c.select.selectedIndex&&!item.disabled);
    activate(c,selected?.index??c.items.find(item=>!item.disabled)?.index??-1);
    c.typeahead='';c.typedAt=0;
    if(c.search)c.search.focus({preventScroll:true});
  }
  function refreshDraft(c){
    const available=new Set(c.select.options),all=allOption(c);
    c.draft=new Set(Array.from(c.draft).filter(option=>available.has(option)));
    if(!c.draft.size&&all)c.draft.add(all);
    c.items.forEach(item=>item.node.setAttribute('aria-selected',String(c.draft.has(item.option))));
  }
  function afterChoose(c,changed){
    const id=c.select.id;
    if(changed){
      c.select.dispatchEvent(new Event('input',{bubbles:true}));
      c.select.dispatchEvent(new Event('change',{bubbles:true}));
    }
    // Dashboard filter handlers replace their entire DOM during change.
    queueMicrotask(()=>{
      scan();
      const next=c.select.isConnected?c:controls.get(document.getElementById(id));
      if(next&&!next.button.disabled)next.button.focus({preventScroll:true});
    });
  }
  function apply(c){
    if(opened!==c||c.select.matches(':disabled'))return;
    refreshDraft(c);
    const draft=new Set(c.draft),changed=Array.from(c.select.options).some(option=>option.selected!==draft.has(option));
    close();
    Array.from(c.select.options).forEach(option=>{option.selected=draft.has(option);});
    sync(c);afterChoose(c,changed);
  }
  function choose(c,index){
    const option=c.select.options[index];
    if(c.select.matches(':disabled')||!option||option.disabled||option.hidden||
        option.closest('optgroup[disabled],optgroup[hidden]'))return;
    if(c.multiple){
      const all=allOption(c);
      if(option===all)c.draft=new Set([all]);
      else{
        c.draft.delete(all);
        if(c.draft.has(option))c.draft.delete(option);else c.draft.add(option);
      }
      refreshDraft(c);activate(c,index,false);c.button.focus({preventScroll:true});return;
    }
    const changed=c.select.selectedIndex!==index;
    close();c.select.selectedIndex=index;sync(c);
    afterChoose(c,changed);
  }
  function keydown(c,event){
    if(event.altKey||event.ctrlKey||event.metaKey||c.button.disabled)return;
    const key=event.key;
    if(key==='Tab'){
      if(opened===c&&c.multiple&&!event.shiftKey){event.preventDefault();c.reset.focus();}
      else if(opened===c)close();
      return;
    }
    if(key==='Escape'){if(opened===c){event.preventDefault();event.stopPropagation();close();}return;}
    if(key==='Enter'||key===' '){
      event.preventDefault();
      if(key==='Enter'&&opened===c&&c.multiple){apply(c);return;}
      if(key===' '&&!c.multiple&&opened===c&&c.typeahead&&Date.now()-c.typedAt<700){typeahead(c,' ');return;}
      if(opened===c){if(c.active>=0)choose(c,c.active);}else open(c);
      return;
    }
    if(['ArrowDown','ArrowUp','Home','End'].includes(key)){
      event.preventDefault();const wasOpen=opened===c;if(!wasOpen)open(c);
      if(opened!==c)return;
      const items=c.items.filter(item=>!item.disabled);
      if(!items.length)return;
      const at=items.findIndex(item=>item.index===c.active);
      const next=key==='Home'?0:key==='End'?items.length-1:
        !wasOpen?Math.max(0,at):Math.max(0,Math.min(items.length-1,at+(key==='ArrowDown'?1:-1)));
      activate(c,items[next].index);return;
    }
    if(key.length===1){event.preventDefault();if(opened!==c)open(c);if(opened===c)typeahead(c,key);}
  }
  function typeahead(c,key){
    const now=Date.now();c.typeahead=(now-c.typedAt<700?c.typeahead:'')+key.toLocaleLowerCase();c.typedAt=now;
    // Repeated letters cycle through names with the same initial.
    const repeated=Array.from(c.typeahead).every(ch=>ch===c.typeahead[0]);
    const query=repeated?c.typeahead[0]:c.typeahead,items=c.items.filter(item=>!item.disabled);
    const at=items.findIndex(item=>item.index===c.active),start=query.length===1?at+1:Math.max(0,at);
    const ordered=items.slice(start).concat(items.slice(0,start));
    const found=ordered.find(item=>item.node.textContent.trim().toLocaleLowerCase().startsWith(query));
    if(found)activate(c,found.index);
  }
  function enhance(select){
    if(controls.has(select)||!eligible(select))return;
    const uid='dt-select-'+(++sequence),wrapper=document.createElement('div');wrapper.className='dt-select';
    const button=document.createElement('button');button.type='button';button.id=uid+'-trigger';
    button.className='dt-select-trigger';button.setAttribute('role','combobox');
    button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-controls',uid+'-list');
    button.setAttribute('aria-expanded','false');button.tabIndex=select.tabIndex;
    const value=document.createElement('span');value.className='dt-select-value';
    const label=labelFor(select).toLowerCase();
    const kind=/month|year|date/.test(label)?'calendar':/executive|person|customer|username/.test(label)?'person':
      /state|district|city|location/.test(label)?'location':/distribut|store|retailer|branch|channel/.test(label)?'store':'list';
    button.append(svg(icons[kind],'dt-select-icon'),value,svg('<path d="m6 9 6 6 6-6"/>','dt-select-chevron'));
    const error=document.createElement('span');error.className='dt-select-error';error.id=uid+'-error';error.setAttribute('aria-live','polite');
    select.before(wrapper);wrapper.append(select,button,error);
    const c={select,wrapper,button,value,error,uid,multiple:select.multiple,description:select.getAttribute('aria-describedby')||'',
      originalTabIndex:select.getAttribute('tabindex'),originalAriaHidden:select.getAttribute('aria-hidden')};
    controls.set(select,c);select.classList.add('dt-select-native');select.tabIndex=-1;select.setAttribute('aria-hidden','true');
    button.addEventListener('click',()=>{if(opened===c)close();else open(c);});
    button.addEventListener('keydown',event=>keydown(c,event));
    c.onFocus=()=>button.focus();select.addEventListener('focus',c.onFocus);
    c.onInvalid=event=>{
      event.preventDefault();c.invalid=true;c.error.textContent=select.validationMessage;sync(c);
      if(!invalidFocus){invalidFocus=c;queueMicrotask(()=>{invalidFocus?.button.focus();invalidFocus=null;});}
    };
    select.addEventListener('invalid',c.onInvalid);sync(c);
  }
  function remove(c){
    if(opened===c)close();controls.delete(c.select);
    c.select.removeEventListener('focus',c.onFocus);c.select.removeEventListener('invalid',c.onInvalid);
    c.select.classList.remove('dt-select-native');
    for(const [name,value] of [['tabindex',c.originalTabIndex],['aria-hidden',c.originalAriaHidden]]){
      if(value===null)c.select.removeAttribute(name);else c.select.setAttribute(name,value);
    }
    if(c.wrapper.parentNode){c.wrapper.before(c.select);c.wrapper.remove();}
  }
  function scan(){
    scheduled=false;
    controls.forEach(c=>{if(!c.select.isConnected||c.multiple!==c.select.multiple||!eligible(c.select))remove(c);});
    document.querySelectorAll('select').forEach(enhance);controls.forEach(sync);
    if(opened){
      const active=opened.active;if(opened.multiple)refreshDraft(opened);
      buildList(opened);position(opened);if(opened)activate(opened,active);
    }
  }
  function schedule(){if(!scheduled){scheduled=true;queueMicrotask(scan);}}
  function start(){
    scan();
    new MutationObserver(records=>{
      const relevant=records.some(record=>{
        const target=record.target.nodeType===1?record.target:record.target.parentElement;
        if(target?.matches('select,option,optgroup')||
          (record.type==='attributes'&&target?.matches('fieldset')))return true;
        return record.type==='childList'&&[...record.addedNodes,...record.removedNodes].some(node=>
          node.nodeType===1&&(node.matches('select')||node.querySelector('select')));
      });
      if(relevant)schedule();
    }).observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,
      attributeFilter:['disabled','hidden','selected','label','value','multiple','size','required','data-multi-filter']});
    document.addEventListener('change',event=>{
      if(event.target.matches('select')){
        if(opened?.select===event.target&&opened.multiple)opened.draft=initialDraft(opened);
        schedule();
      }
    });
    document.addEventListener('reset',()=>{if(opened)close();setTimeout(schedule,0);});
    document.addEventListener('pointerdown',event=>{
      if(opened&&!opened.wrapper.contains(event.target)&&!opened.menu.contains(event.target))close();
    });
    document.addEventListener('focusin',event=>{
      if(opened&&!opened.wrapper.contains(event.target)&&!opened.menu.contains(event.target))close();
    });
    document.addEventListener('click',event=>{
      const label=event.target.closest('label');if(!label||event.target.closest('.dt-select'))return;
      const c=controls.get(label.control);
      if(c&&!c.button.disabled){event.preventDefault();c.button.focus();}
    });
    document.addEventListener('scroll',event=>{if(opened&&!opened.menu.contains(event.target))position(opened);},true);
    document.addEventListener('toggle',()=>{if(opened)position(opened);},true);
    window.addEventListener('resize',()=>{if(opened)position(opened);});
  }
  window.dtDropdownUI={refresh:schedule};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
