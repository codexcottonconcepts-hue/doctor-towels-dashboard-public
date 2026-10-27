/* Customer profiles are saved separately; invoice locations remain invoice data. */
(()=>{
  'use strict';
  function start(){
    const panel=document.getElementById('customer-details-panel');
    if(!panel||panel.dataset.ready||panel.dataset.canViewProfile==='false')return;
    panel.dataset.ready='true';
    const form=panel.closest('form'),customer=document.getElementById('customer_id');
    if(!form||!customer)return;
    const name=document.getElementById('new_customer'),nameBox=document.getElementById('customer-new-name');
    const profileBox=document.getElementById('customer-details-profile');
    const status=document.getElementById('customer-status');
    const add=document.getElementById('customer-add'),edit=document.getElementById('customer-edit');
    const update=document.getElementById('customer-update'),cancel=document.getElementById('customer-cancel');
    const done=document.getElementById('customer-done'),close=document.getElementById('customer-close');
    const column=panel.closest('.customer-profile-column'),title=document.getElementById('customer-dialog-title');
    const fields={address:document.getElementById('customer_address'),contact_number:document.getElementById('customer_contact_number'),location:document.getElementById('customer_location'),gstin:document.getElementById('customer_gstin')};
    const geo=['state_id','district_id','city_id'].map(id=>document.getElementById(id));
    const permitted=panel.dataset.canEditProfile==='true'&&!panel.closest('fieldset')?.disabled;
    const base=(panel.dataset.profileUrl||'/entries/customers').replace(/\/$/,'');
    let mode=name?.value.trim()?'new':'view',profile=null,request=0,controller=null,busy=false,loading=false;
    let previousCustomer='',invoiceGeo=null,suppress=false;
    let expanded=false,backdrop=null,returnFocus=null;
    function message(text,kind=''){if(status){status.textContent=text;status.dataset.kind=kind;status.hidden=!text;}}
    function announceChange(control){control.dispatchEvent(new Event('change',{bubbles:true}));}
    function selected(value){suppress=true;customer.value=String(value||'');announceChange(customer);suppress=false;}
    function readGeo(){return geo.map(control=>control?.value||'');}
    function writeGeo(values){
      geo.forEach((control,index)=>{
        if(!control||control.matches(':disabled'))return;
        const value=String(values[index]??'');
        control.value=Array.from(control.options).some(option=>option.value===value)?value:'';
        announceChange(control);
      });
    }
    function profileGeo(value){return ['state_id','district_id','city_id'].map(key=>value?.[key]??'');}
    function ensureProfileCity(value){
      const city=geo[2],id=String(value?.city_id??'');
      if(!id||!city||Array.from(city.options).some(option=>option.value===id))return true;
      // Only the selected, authenticated profile may supply its saved city.
      // Build an option as text, and retain the existing state/district linkage.
      const validId=raw=>/^[1-9]\d*$/.test(raw)&&Number(raw)<=2147483647;
      const state=String(value?.state_id??''),district=String(value?.district_id??'');
      const stateOption=Array.from(geo[0]?.options||[]).find(option=>option.value===state);
      const districtOption=Array.from(geo[1]?.options||[]).find(option=>option.value===district);
      if(String(value?.customer_id)!==customer.value||!validId(id)||!validId(state)||!stateOption||
         (district&&(!validId(district)||!districtOption||districtOption.dataset.state!==state))||
         typeof value?.city!=='string'||!value.city.trim()||value.city.length>200)return false;
      const option=document.createElement('option');
      option.value=id;option.textContent=value.city+' (saved customer location)';
      option.dataset.state=state;option.dataset.district=district;
      option.dataset.customerSavedCity='true';city.append(option);
      return true;
    }
    function fill(value){for(const [key,control]of Object.entries(fields))if(control)control.value=value?.[key]||'';}
    function visible(control){return !!control&&!control.matches(':disabled')&&!control.closest('[hidden]')&&control.getClientRects().length>0;}
    function focusable(){return Array.from(column?.querySelectorAll('button,input,textarea,select,a[href],[tabindex]')||[]).filter(control=>control.tabIndex>=0&&visible(control));}
    function openMenu(){
      const trigger=column?.querySelector('.dt-select-trigger[aria-expanded="true"]');
      return document.getElementById(trigger?.getAttribute('aria-controls'))?.closest('.dt-select-menu');
    }
    function openEditor(trigger){
      if(!column||busy||expanded)return;
      returnFocus=trigger||document.activeElement;expanded=true;
      column.classList.add('is-expanded');column.setAttribute('role','dialog');
      column.setAttribute('aria-modal','true');column.setAttribute('aria-labelledby','customer-dialog-title');column.tabIndex=-1;
      document.body.classList.add('customer-editor-open');
      backdrop=document.createElement('div');backdrop.className='customer-editor-backdrop';backdrop.setAttribute('aria-hidden','true');
      backdrop.addEventListener('click',()=>{if(!busy)closeEditor();});document.body.append(backdrop);
      render();queueMicrotask(()=>{if(expanded)(mode==='new'?name:fields.address)?.focus();});
    }
    function closeEditor(restore=true){
      if(!expanded)return;
      expanded=false;column.classList.remove('is-expanded');column.removeAttribute('role');
      column.removeAttribute('aria-modal');column.removeAttribute('aria-labelledby');column.removeAttribute('tabindex');
      backdrop?.remove();backdrop=null;document.body.classList.remove('customer-editor-open');render();
      if(restore){const target=visible(returnFocus)?returnFocus:visible(edit)?edit:customer;target?.focus();}
    }
    function render(){
      const newMode=mode==='new',editing=mode==='edit';
      if(nameBox)nameBox.hidden=!newMode;
      if(name){name.disabled=!newMode||!permitted;name.required=newMode&&permitted;}
      if(profileBox)profileBox.hidden=false;
      for(const control of Object.values(fields))if(control)control.disabled=!(permitted&&(newMode||editing))||busy;
      if(add){add.hidden=!permitted||newMode||editing;add.disabled=busy;}
      if(edit){edit.hidden=!permitted||expanded||(!profile&&!newMode&&!editing);edit.disabled=busy;}
      if(update){update.hidden=!editing||!!column&&!expanded;update.disabled=busy;update.textContent=busy?'Updating…':'Update customer';}
      if(done){done.hidden=!newMode||!expanded;done.disabled=busy;}
      if(close){close.hidden=!expanded;close.disabled=busy;}
      if(cancel){cancel.hidden=!newMode&&!editing;cancel.disabled=busy;}
      if(title)title.textContent=expanded?(newMode?'Add customer':'Edit customer'):'Customer details';
    }
    function stopFetch(){request++;controller?.abort();controller=null;loading=false;}
    async function load(applyLocation=false){
      stopFetch();
      const id=customer.value,sequence=request;
      mode='view';profile=null;if(name)name.value='';fill(null);render();
      if(!id){message('');return;}
      controller=new AbortController();loading=true;message('Loading customer details…');
      try{
        const response=await fetch(base+'/'+encodeURIComponent(id),{credentials:'same-origin',headers:{Accept:'application/json'},signal:controller.signal});
        let result;try{result=await response.json();}catch{throw new Error('Customer details could not be loaded. Please sign in again or reload the page.');}
        if(sequence!==request||customer.value!==id)return;
        if(!response.ok)throw new Error(result.message||result.detail||'Customer details could not be loaded.');
        if(!result.customer||String(result.customer.customer_id)!==id)throw new Error('The customer details did not match your selection. Reload the page.');
        profile=result.customer;fill(profile);
        if(!ensureProfileCity(profile)){
          message('The saved customer location is unavailable. Reload the customer details before changing its location.','error');
        }else{
          if(applyLocation)writeGeo(profileGeo(profile));
          message('');
        }
      }catch(error){if(error.name!=='AbortError'&&sequence===request)message(error.message||'Customer details could not be loaded.','error');}
      finally{if(sequence===request){controller=null;loading=false;render();}}
    }
    customer.addEventListener('change',()=>{
      if(suppress)return;
      if(busy)return;
      closeEditor(false);invoiceGeo=null;previousCustomer='';load(true);
    });
    add?.addEventListener('click',()=>{
      if(!permitted||busy)return;
      stopFetch();previousCustomer=customer.value;invoiceGeo=readGeo();selected('');profile=null;mode='new';fill(null);
      if(name)name.value='';render();message('');openEditor(add);
    });
    edit?.addEventListener('click',()=>{
      if(!permitted||busy)return;
      if(mode==='new'||mode==='edit'){openEditor(edit);return;}
      if(!profile)return;
      if(!ensureProfileCity(profile)){message('The saved customer location is unavailable. Reload the customer details before changing its location.','error');return;}
      invoiceGeo=readGeo();mode='edit';fill(profile);writeGeo(profileGeo(profile));render();
      message('');openEditor(edit);
    });
    cancel?.addEventListener('click',()=>{
      if(busy)return;
      const wasNew=mode==='new';mode='view';if(name)name.value='';
      if(invoiceGeo)writeGeo(invoiceGeo);invoiceGeo=null;
      closeEditor();
      if(wasNew){selected(previousCustomer);previousCustomer='';load(false);}
      else{fill(profile);render();message('Customer changes cancelled.','success');}
    });
    close?.addEventListener('click',()=>{if(!busy)closeEditor();});
    done?.addEventListener('click',()=>{
      if(busy||mode!=='new')return;
      if(!name?.value.trim()){
        message('Enter the customer name.','error');name?.focus();return;
      }
      if(!name.checkValidity()){name.reportValidity();return;}
      if(!geo[0]?.value){
        message('Choose a customer state.','error');
        const trigger=geo[0]?.closest('.entry-field')?.querySelector('.dt-select-trigger');
        (trigger||geo[0])?.focus();return;
      }
      message('');closeEditor();
    });
    document.addEventListener('keydown',event=>{
      if(!expanded)return;
      // Text-input Enter must not invoke background invoice validation first.
      if(event.key==='Enter'&&event.target?.matches('input,select')&&!openMenu()){
        event.preventDefault();(mode==='new'?done:update)?.focus();return;
      }
      if(event.key==='Escape'){
        if(openMenu())return;
        event.preventDefault();event.stopPropagation();if(!busy)closeEditor();return;
      }
      if(event.key!=='Tab')return;
      const menu=openMenu();if(menu?.contains(document.activeElement))return;
      const controls=focusable(),first=controls[0],last=controls[controls.length-1];
      if(!controls.length){event.preventDefault();column.focus();}
      else if(event.shiftKey&&(document.activeElement===first||!column.contains(document.activeElement))){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&(document.activeElement===last||!column.contains(document.activeElement))){event.preventDefault();first.focus();}
    },true);
    document.addEventListener('focusin',event=>{
      if(expanded&&!column.contains(event.target)&&!openMenu()?.contains(event.target))(focusable()[0]||column).focus();
    });
    window.addEventListener('pagehide',()=>closeEditor(false));
    fields.gstin?.addEventListener('input',()=>{fields.gstin.value=fields.gstin.value.toUpperCase();});
    update?.addEventListener('click',async()=>{
      if(!permitted||!profile||mode!=='edit'||busy)return;
      const id=String(profile.customer_id),payload=new URLSearchParams();
      payload.set('csrf_token',form.querySelector('[name="csrf_token"]')?.value||'');
      payload.set('revision',profile.revision||'');
      for(const [key,control]of Object.entries(fields))payload.set('customer_'+key,control?.value||'');
      geo.forEach((control,index)=>payload.set(['state_id','district_id','city_id'][index],control?.value||''));
      let saved=false;
      busy=true;const wasDisabled=customer.disabled;customer.disabled=true;render();message('Updating customer…');
      try{
        const response=await fetch(base+'/'+encodeURIComponent(id),{method:'POST',credentials:'same-origin',headers:{Accept:'application/json','Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'},body:payload});
        let result;try{result=await response.json();}catch{throw new Error('The customer update could not be confirmed. Reload the customer details before trying again.');}
        if(!response.ok)throw new Error(result.message||result.detail||'The customer could not be updated.');
        if(!result.customer||String(result.customer.customer_id)!==id)throw new Error('The customer update could not be confirmed. Reload the page.');
        profile=result.customer;mode='view';fill(profile);
        if(invoiceGeo)writeGeo(invoiceGeo);invoiceGeo=null;
        message('Customer updated.','success');
        saved=true;
      }catch(error){message(error.message||'The customer could not be updated.','error');}
      finally{busy=false;customer.disabled=wasDisabled;render();if(saved)closeEditor();}
    });
    form.addEventListener('submit',event=>{
      if(expanded){event.preventDefault();message(mode==='new'?'Click Done to finish customer details before saving the sales entry.':'Update or cancel the customer changes before saving this sales entry.','error');(mode==='new'?done:update)?.focus();}
      else if(mode==='edit'||busy){event.preventDefault();message('Update or cancel the customer changes before saving this sales entry.','error');edit?.focus();}
      else if(loading){event.preventDefault();message('Wait for the customer details to load before saving this sales entry.','error');}
    });
    if(mode==='new'){
      selected('');render();
    }else{
      const initial=document.getElementById('customer-profile-data');
      if(initial&&customer.value){
        try{const parsed=JSON.parse(initial.textContent);const value=parsed.customer||parsed;if(String(value.customer_id)===customer.value&&ensureProfileCity(value)){profile=value;fill(profile);render();message('');return;}}catch{/* Use the authenticated endpoint instead. */}
      }
      render();if(customer.value)load(false);
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
