/* Invoice arithmetic uses integer hundredths and HALF_UP rounding throughout. */
(()=>{
  'use strict';
  const MAX_LINES=100, MAX_CENTS=99999999999999n;
  function scaled(value){
    const raw=String(value??'').trim(), match=/^(\d+)(?:\.(\d{1,2}))?$/.exec(raw);
    if(!match||raw.length>18)return null;
    return BigInt(match[1])*100n+BigInt((match[2]||'').padEnd(2,'0'));
  }
  function rounded(numerator,denominator){return (numerator+denominator/2n)/denominator;}
  function decimal(cents){return (cents/100n).toString()+'.'+(cents%100n).toString().padStart(2,'0');}
  function money(cents){
    const [whole,part]=decimal(cents).split('.'),tail=whole.slice(-3),head=whole.slice(0,-3);
    return (head?head.replace(/\B(?=(\d{2})+(?!\d))/g,',')+',':'')+tail+'.'+part;
  }
  function calculate(item){
    const quantity=scaled(item.quantity), rate=scaled(item.unit_rate), percent=scaled(item.discount_percent);
    if(quantity===null||quantity<=0n||quantity>MAX_CENTS)return {error:'Enter a quantity above zero, with up to 2 decimal places.',field:'quantity'};
    if(rate===null||rate<0n||rate>MAX_CENTS)return {error:'Enter a selling rate, with up to 2 decimal places.',field:'unit_rate'};
    if(percent===null||percent>10000n)return {error:'Enter a discount from 0 to 100%, with up to 2 decimal places.',field:'discount_percent'};
    const gross=rounded(quantity*rate,100n),discount=rounded(gross*percent,10000n),total=gross-discount;
    if(gross>MAX_CENTS||total>MAX_CENTS)return {error:'This line amount is too large. Reduce its quantity or rate.',field:'quantity'};
    const taxable=rounded(total*100n,118n),gst=total-taxable;
    return {quantity,rate,percent,gross,discount,total,taxable,gst};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={scaled,rounded,decimal,money,calculate,MAX_LINES};
  if(typeof document==='undefined')return;
  function boot(){
    const widget=document.getElementById('product-invoice-widget'),data=document.getElementById('invoice-widget-data');
    if(!widget||!data||widget.dataset.ready)return;
    widget.dataset.ready='1';
    const config=JSON.parse(data.textContent),catalog=new Map((config.catalog||[]).map(p=>[String(p.product_id),p]));
    const snapshots=new Map((config.snapshots||[]).map(p=>[String(p.product_id),p]));
    const colours=(config.colours||[]).map(c=>String(c.name));
    const mode=widget.querySelector('#entry_mode'),price=widget.querySelector('#price_list'),panel=widget.querySelector('#invoice-product-panel');
    const priceField=widget.querySelector('#invoice-price-list-field'),body=widget.querySelector('#invoice-item-rows'),payload=widget.querySelector('#invoice_items');
    const status=widget.querySelector('#invoice-item-status');
    const form=widget.closest('form'),summary=document.getElementById('summary-sales-values');
    const primary=['primary_qty','primary_taxable','primary_with_tax'].map(id=>document.getElementById(id));
    let rows=[],sequence=0,loadProblem='',unparsed=false,opened=null,attempted=false;
    try{
      const input=JSON.parse(config.rawItems||'[]');
      if(!Array.isArray(input)||input.length>MAX_LINES||input.some(r=>!r||typeof r!=='object'||Array.isArray(r)))throw new Error('invalid');
      rows=input.map(r=>{
        const snapshot=config.saved?(snapshots.get(String(r.product_id))||(r.product_name?r:null)):null;
        const product=catalog.get(String(r.product_id));
        return {key:++sequence,product_id:String(r.product_id??''),quantity:String(r.quantity??''),unit_rate:String(r.unit_rate??''),discount_percent:String(r.discount_percent??''),colour:String(r.colour??''),snapshot,
          query:String(snapshot?.product_name||product?.product_name||r.product_query||''),draft:false,touched:true};
      });
    }catch(error){loadProblem='The previous item list could not be read. Add the products again before saving.';unparsed=true;}
    const writable=()=>config.editable&&!widget.closest('fieldset')?.disabled;
    function elem(tag,attributes={},text=''){
      const node=document.createElement(tag);
      for(const [key,value]of Object.entries(attributes))node.setAttribute(key,String(value));
      if(text!=='')node.textContent=text;
      return node;
    }
    function pristine(row){return row.draft&&!row.touched&&!row.product_id&&!row.query&&!row.colour&&row.quantity==='1'&&row.unit_rate===''&&row.discount_percent==='0';}
    function enteredRows(){return rows.filter(row=>!pristine(row));}
    function rowItem(row){return {product_id:row.product_id,quantity:row.quantity,unit_rate:row.unit_rate,discount_percent:row.discount_percent,...(row.colour?{colour:row.colour}:{}),...(!row.product_id&&row.query?{product_query:row.query}:{})};}
    function writePayload(){if(!unparsed)payload.value=JSON.stringify(enteredRows().map(rowItem));}
    function focusControl(control){if(control){control.focus();if(typeof control.select==='function')control.select();}}
    function closeOptions(){
      if(!opened)return;
      opened.row.input.setAttribute('aria-expanded','false');
      opened.row.input.removeAttribute('aria-activedescendant');
      opened.popup.remove();opened=null;
    }
    function positionOptions(){
      if(!opened)return;
      const rect=opened.row.input.getBoundingClientRect(),edge=8,gap=4,vw=document.documentElement.clientWidth,vh=window.innerHeight;
      if(!opened.row.input.isConnected||!rect.width||!rect.height){closeOptions();return;}
      const width=Math.min(Math.max(rect.width,300),vw-edge*2);
      const below=vh-rect.bottom-gap-edge,above=rect.top-gap-edge,up=below<180&&above>below;
      opened.popup.style.width=width+'px';
      opened.popup.style.maxHeight=Math.max(50,Math.min(300,up?above:below))+'px';
      opened.popup.style.left=Math.max(edge,Math.min(rect.left,vw-width-edge))+'px';
      const height=opened.popup.getBoundingClientRect().height;
      opened.popup.style.top=Math.max(edge,Math.min(up?rect.top-gap-height:rect.bottom+gap,vh-height-edge))+'px';
    }
    function activateOption(index){
      if(!opened||!opened.matches.length)return;
      opened.active=Math.max(0,Math.min(index,opened.matches.length-1));
      const options=Array.from(opened.popup.querySelectorAll('[role="option"]'));
      options.forEach((option,i)=>{option.classList.toggle('is-active',i===opened.active);option.setAttribute('aria-selected',String(i===opened.active));});
      const option=options[opened.active];opened.row.input.setAttribute('aria-activedescendant',option.id);
      const top=option.offsetTop,bottom=top+option.offsetHeight;
      if(top<opened.popup.scrollTop)opened.popup.scrollTop=top;
      else if(bottom>opened.popup.scrollTop+opened.popup.clientHeight)opened.popup.scrollTop=bottom-opened.popup.clientHeight;
    }
    function chooseProduct(row,product){
      if(!writable()||!product||product.is_active===false&&String(product.product_id)!==row.product_id)return;
      const changed=String(product.product_id)!==row.product_id;
      if(changed)row.snapshot=null;
      row.product_id=String(product.product_id);row.query=row.snapshot?.product_name||product.product_name;row.input.value=row.query;row.touched=true;
      if(changed||row.unit_rate===''){
        const suggestion=product.prices?.[price.value];row.unit_rate=suggestion===null||suggestion===undefined?'':String(suggestion);
        row.node.querySelector('[data-invoice-input="unit_rate"]').value=row.unit_rate;
      }
      unparsed=false;loadProblem='';closeOptions();noteFor(row);ensureDraft();update();
      focusAfterProduct(row);
    }
    function focusAfterProduct(row){
      const quantity=row.node.querySelector('[data-invoice-input="quantity"]');
      if(!row.colourSelect){focusControl(quantity);return;}
      // Wait for the dropdown styling to enable the colour control first.
      setTimeout(()=>focusControl(row.colourSelect.disabled?quantity:row.colourSelect),0);
    }
    function openOptions(row){
      if(!writable())return;
      closeOptions();
      const term=row.input.value.trim().toLocaleLowerCase();
      const tokens=term.split(/\s+/).filter(Boolean);
      const displayName=product=>String(product.product_id)===row.product_id&&row.snapshot?.product_name?row.snapshot.product_name:product.product_name;
      const matches=Array.from(catalog.values()).filter(product=>(product.is_active!==false||String(product.product_id)===row.product_id)&&tokens.every(token=>displayName(product).toLocaleLowerCase().includes(token)));
      const popup=elem('div',{class:'invoice-product-options',id:'invoice-products-'+row.key,role:'listbox','aria-label':'Product suggestions, row '+row.key});
      popup.style.position='fixed';
      opened={row,popup,matches,active:-1};
      matches.forEach((product,index)=>{
        const option=elem('div',{class:'invoice-product-option',id:'invoice-product-'+row.key+'-'+index,role:'option','aria-selected':'false'});
        option.append(elem('span',{class:'invoice-option-name'},displayName(product)));
        const rate=scaled(product.prices?.[price.value]);
        option.append(elem('span',{class:'invoice-option-price'},rate===null?'Enter rate':'₹'+money(rate)));
        option.addEventListener('pointerdown',event=>event.preventDefault());
        option.addEventListener('pointermove',()=>{if(opened?.row===row)activateOption(index);});
        option.addEventListener('click',()=>chooseProduct(row,product));popup.append(option);
      });
      if(!matches.length)popup.append(elem('div',{class:'invoice-product-empty',role:'status'},'No matching products. Change the name to search again.'));
      document.body.append(popup);row.input.setAttribute('aria-expanded','true');positionOptions();
      if(matches.length)activateOption(Math.max(0,matches.findIndex(product=>String(product.product_id)===row.product_id)));
    }
    function focusNextSection(){
      closeOptions();
      const controls=Array.from((form||document).querySelectorAll('input,select,textarea,button'));
      const next=controls.find(control=>!widget.contains(control)&&!!(widget.compareDocumentPosition(control)&Node.DOCUMENT_POSITION_FOLLOWING)&&
        !control.matches(':disabled')&&!control.readOnly&&control.type!=='hidden'&&control.tabIndex>=0&&!!control.getClientRects().length&&!control.closest('[hidden]'));
      focusControl(next);
    }
    function ensureDraft(){
      if(!writable())return;
      const drafts=rows.filter(pristine),real=enteredRows();
      for(const extra of drafts.slice(real.length<MAX_LINES?1:0)){
        if(opened?.row===extra)closeOptions();extra.node.remove();rows=rows.filter(row=>row!==extra);
      }
      let draft=rows.find(pristine);
      if(real.length<MAX_LINES&&!draft){
        draft={key:++sequence,product_id:'',quantity:'1',unit_rate:'',discount_percent:'0',colour:'',query:'',snapshot:null,draft:true,touched:false};
        rows.push(draft);makeRow(draft);
      }
      if(draft&&real.length<MAX_LINES){rows=rows.filter(row=>row!==draft).concat(draft);body.append(draft.node);}
    }
    function noteFor(row){
      const product=catalog.get(row.product_id),meta=row.node.querySelector('.invoice-product-meta');
      meta.replaceChildren();
      if(!product)return;
      const source=row.snapshot||product,mrp=scaled(source.mrp),bits=[];
      if(mrp!==null)bits.push('MRP ₹'+money(mrp));
      else bits.push('MRP not listed');
      if(source.unit)bits.push(String(source.unit));
      meta.append(document.createTextNode(bits.join(' · ')));
      const notes=product.price_notes||product.raw_prices||{},raw=notes[price.value];
      if(raw&&scaled(String(raw).replace(/^₹\s*/, '').replaceAll(',',''))===null){
        meta.append(elem('div',{class:'invoice-product-note'},String(raw)));
      }
      if(row.unit_rate==='')meta.append(elem('div',{class:'invoice-product-note'},'No numeric price for this list. Enter the selling rate.'));
    }
    function makeRow(row){
      const tr=elem('tr',{'data-invoice-line':row.key}),productCell=elem('td',{'data-label':'Product'});
      const productInput=elem('input',{type:'text',class:'invoice-product-input',role:'combobox','aria-label':'Product, row '+row.key,'aria-autocomplete':'list','aria-expanded':'false',
        'aria-controls':'invoice-products-'+row.key,'data-invoice-input':'product_id',placeholder:'Type a product name…',autocomplete:'off',spellcheck:'false'});
      productInput.value=row.query;row.input=productInput;
      const meta=elem('div',{class:'invoice-product-meta'}),error=elem('div',{class:'invoice-line-error',id:'invoice-line-error-'+row.key});
      productInput.setAttribute('aria-describedby',error.id);
      productCell.append(productInput,meta,error);tr.append(productCell);
      if(colours.length)tr.append(colourCell(row));
      for(const [key,label,min,max,placeholder]of [['quantity','Qty','0.01','999999999999.99','Qty'],['unit_rate','Rate incl. GST (₹)','0','999999999999.99','Rate'],['discount_percent','Discount % (before GST)','0','100','0']]){
        const td=elem('td',{'data-label':label}),input=elem('input',{type:'number',min,max,step:'0.01',inputmode:'decimal',placeholder,'aria-label':label+', row '+row.key,'data-invoice-input':key,'aria-describedby':error.id});
        input.value=row[key];
        input.addEventListener('input',()=>{if(!writable())return;row[key]=input.value;row.touched=true;unparsed=false;loadProblem='';ensureDraft();noteFor(row);update();});
        input.addEventListener('keydown',event=>{
          if(event.key!=='Enter'||event.isComposing)return;
          event.preventDefault();if(!writable())return;
          closeOptions();update();
          if(!catalog.has(row.product_id)){row.input.focus();row.input.reportValidity();return;}
          if(!input.checkValidity()){input.reportValidity();return;}
          if(key==='quantity'){focusControl(row.node.querySelector('[data-invoice-input="unit_rate"]'));return;}
          if(key==='unit_rate'){focusControl(row.node.querySelector('[data-invoice-input="discount_percent"]'));return;}
          const result=calculate(row);
          if(result.error){const invalid=row.node.querySelector('[data-invoice-input="'+result.field+'"]');invalid.focus();invalid.reportValidity();return;}
          ensureDraft();update();
          const next=rows[rows.indexOf(row)+1];if(next)focusControl(next.input);else focusNextSection();
        });
        td.append(input);tr.append(td);
      }
      for(const [key,label]of [['taxable','Taxable (₹)'],['gst','GST 18% (₹)'],['total','Total (₹)']])tr.append(elem('td',{class:'invoice-money','data-label':label,'data-invoice-output':key},'—'));
      const removeCell=elem('td',{class:'invoice-remove-cell'}),remove=elem('button',{type:'button',class:'invoice-remove','aria-label':'Remove product, row '+row.key},'×');
      remove.addEventListener('click',()=>{
        if(!writable())return;
        const index=rows.indexOf(row);if(opened?.row===row)closeOptions();
        rows=rows.filter(r=>r!==row);tr.remove();unparsed=false;loadProblem='';ensureDraft();update();
        (rows[Math.min(index,rows.length-1)]?.input)?.focus();
      });
      removeCell.append(remove);tr.append(removeCell);row.node=tr;
      productInput.addEventListener('input',()=>{
        if(!writable())return;
        row.query=productInput.value;row.touched=true;
        const product=catalog.get(row.product_id),selectedName=row.snapshot?.product_name||product?.product_name;
        if(row.query!==selectedName){row.product_id='';row.snapshot=null;row.unit_rate='';tr.querySelector('[data-invoice-input="unit_rate"]').value='';}
        unparsed=false;loadProblem='';ensureDraft();noteFor(row);update();openOptions(row);
      });
      productInput.addEventListener('focus',()=>{if(!pristine(row))openOptions(row);});
      productInput.addEventListener('click',()=>openOptions(row));
      productInput.addEventListener('blur',()=>{setTimeout(()=>{if(opened?.row===row&&document.activeElement!==productInput)closeOptions();},0);});
      productInput.addEventListener('keydown',event=>{
        if(event.isComposing)return;
        if(event.key==='Escape'){event.preventDefault();closeOptions();return;}
        if(event.key==='Tab'){closeOptions();return;}
        if(!['Enter','ArrowDown','ArrowUp'].includes(event.key))return;
        event.preventDefault();if(!writable())return;
        if(event.key==='Enter'&&pristine(row)){focusNextSection();return;}
        if(event.key==='Enter'&&row.product_id&&(!opened||opened.row!==row)){
          focusAfterProduct(row);return;
        }
        const wasOpen=opened?.row===row;
        if(!wasOpen)openOptions(row);
        if(event.key==='ArrowDown'){activateOption(wasOpen?opened.active+1:0);return;}
        if(event.key==='ArrowUp'){activateOption(wasOpen?opened.active-1:opened.matches.length-1);return;}
        if(opened?.matches.length){chooseProduct(row,opened.matches[Math.max(opened.active,0)]);return;}
        if(row.product_id&&catalog.has(row.product_id)){closeOptions();focusAfterProduct(row);return;}
        closeOptions();update();productInput.reportValidity();
      });
      body.append(tr);noteFor(row);
    }
    function colourCell(row){
      const td=elem('td',{'data-label':'Colour'});
      const select=elem('select',{class:'invoice-colour-select','aria-label':'Colour, row '+row.key,'data-invoice-colour':''});
      select.append(elem('option',{value:''},'No colour'));
      // A colour later removed from the list still shows on the line that saved it.
      for(const name of row.colour&&!colours.includes(row.colour)?[...colours,row.colour]:colours)select.append(elem('option',{value:name},name));
      select.value=row.colour;row.colourSelect=select;
      select.addEventListener('change',()=>{
        if(!writable())return;
        row.colour=select.value;row.touched=true;unparsed=false;loadProblem='';ensureDraft();update();
        // After the dropdown has returned focus to itself, continue to quantity.
        setTimeout(()=>focusControl(row.node.querySelector('[data-invoice-input="quantity"]')),0);
      });
      // Enter on a closed colour list moves on, so the colour can be skipped.
      td.addEventListener('keydown',event=>{
        if(event.key!=='Enter'||event.isComposing||event.target.getAttribute('aria-expanded')==='true')return;
        event.preventDefault();event.stopPropagation();
        focusControl(row.node.querySelector('[data-invoice-input="quantity"]'));
      },true);
      td.append(select);return td;
    }
    function update(){
      const active=mode.value==='invoice',canEdit=active&&writable();
      let quantity=0n,taxable=0n,gst=0n,total=0n,invalid=0;
      for(const row of rows){
        const draft=pristine(row);row.node.classList.toggle('invoice-draft',draft);
        const result=calculate(row),product=catalog.get(row.product_id);
        const error=draft?'':!product?'Choose a product from the suggestions.':result.error||'';
        row.node.querySelector('.invoice-line-error').textContent=active?error:'';
        for(const control of row.node.querySelectorAll('input,select,button:not(.dt-select-trigger)')){
          control.disabled=!canEdit||(draft&&(control.type==='number'||control.tagName==='SELECT'));
          if(control.hasAttribute('data-invoice-input'))control.required=canEdit&&!draft;
        }
        for(const control of row.node.querySelectorAll('input[data-invoice-input]'))control.setCustomValidity('');
        row.input.setCustomValidity(active&&!draft&&!product?'Choose a product from the suggestions.':'');
        if(active&&!draft&&result.error){
          row.node.querySelector('[data-invoice-input="'+result.field+'"]').setCustomValidity(result.error);
        }
        for(const key of ['taxable','gst','total'])row.node.querySelector('[data-invoice-output="'+key+'"]').textContent=error||draft?'—':money(result[key]);
        row.node.querySelector('.invoice-remove').hidden=draft;
        if(draft)continue;
        if(error)invalid++;
        else{quantity+=result.quantity;taxable+=result.taxable;gst+=result.gst;total+=result.total;}
      }
      let message=loadProblem;
      const entered=enteredRows();
      if(!entered.length&&!message&&active&&attempted)message='Choose a product to begin the invoice.';
      if(invalid&&active)message='Complete '+invalid+' product '+(invalid===1?'row':'rows')+' to calculate the invoice total.';
      if(quantity>MAX_CENTS||taxable>MAX_CENTS||total>MAX_CENTS){message='Invoice totals are too large. Reduce the quantities or rates.';invalid++;}
      const ready=entered.length>0&&entered.length<=MAX_LINES&&!invalid&&!unparsed;
      status.textContent=active?message:'';
      widget.querySelector('#invoice-total-quantity').textContent=ready?money(quantity):'—';
      widget.querySelector('#invoice-total-taxable').textContent=ready?'₹'+money(taxable):'—';
      widget.querySelector('#invoice-total-gst').textContent=ready?'₹'+money(gst):'—';
      widget.querySelector('#invoice-total-amount').textContent=ready?'₹'+money(total):'—';
      widget.querySelector('#invoice-item-count').textContent=entered.length+' '+(entered.length===1?'item':'items');
      payload.disabled=!writable();
      if(active&&writable())primary.forEach((el,index)=>{if(el)el.value=ready?decimal([quantity,taxable,total][index]):'';});
      writePayload();
      return ready;
    }
    function setMode(){
      mode.value='invoice';
      const active=true;
      panel.hidden=!active;priceField.hidden=!active;
      price.disabled=!active||!writable();
      if(summary)summary.hidden=active;
      const legacyTotals=document.getElementById('invoice-primary-totals');
      if(legacyTotals)legacyTotals.hidden=true;
      primary.forEach(el=>{if(el)el.readOnly=true;});
      ensureDraft();
      update();
    }
    for(const row of rows)makeRow(row);
    price.addEventListener('change',()=>{for(const row of rows)noteFor(row);if(opened)openOptions(opened.row);});
    widget.querySelector('#invoice-next-section')?.addEventListener('click',focusNextSection);
    document.addEventListener('pointerdown',event=>{if(opened&&!opened.popup.contains(event.target)&&event.target!==opened.row.input)closeOptions();});
    document.addEventListener('scroll',event=>{if(opened&&!opened.popup.contains(event.target))positionOptions();},true);
    window.addEventListener('resize',positionOptions);
    form?.addEventListener('submit',event=>{
      if(mode.value!=='invoice'||!writable())return;
      attempted=true;
      if(!update()){
        event.preventDefault();status.scrollIntoView({behavior:'smooth',block:'center'});
        const input=rows.flatMap(r=>Array.from(r.node.querySelectorAll('input,select'))).find(control=>!control.checkValidity());
        if(input)input.reportValidity();
        else rows[0]?.input.focus();
      }
    });
    setMode();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
