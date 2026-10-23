"""Styles scoped to the sales-entry screen."""

STYLE = """<style>
body{background:#f5f6f8}.wrap{max-width:1440px;padding:32px 32px 56px}.bar{padding:15px 32px}.bar>a{text-decoration:none;color:#e7e7eb}.bar>a:hover{color:white;text-decoration:underline}.bar>a[href="/entries"]{color:#fff;font-weight:700}
.entries-page{--accent:#b5122d;--ink:#202331;--muted:#6c7181;--line:#e6e8ed;color:var(--ink)}
.entries-page [hidden]{display:none!important}.entries-page a{text-decoration:none}.entries-page a:hover{text-decoration:underline}
.entry-breadcrumb{display:flex;gap:9px;align-items:center;color:var(--muted);font-size:12px;margin-bottom:16px}.entry-breadcrumb a{color:var(--muted)}
.entry-heading{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:26px}.entry-heading h1{font-size:29px;letter-spacing:-.8px;line-height:1.2;font-weight:700;margin:0 0 8px}.entry-heading p{margin:0;color:var(--muted);font-size:14px}
.entry-heading-actions{display:flex;gap:10px;align-items:center;flex-shrink:0}.entry-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid #d9dce3;background:white;color:#343848;font:600 13px/1.4 "Segoe UI",sans-serif;padding:10px 15px;border-radius:7px;margin:0;min-height:40px;cursor:pointer;text-decoration:none!important;white-space:nowrap}.entry-btn:hover{background:#f4f5f7;color:#202331}.entry-btn.primary{background:var(--accent);border-color:var(--accent);color:white;box-shadow:0 2px 3px #84102012}.entry-btn.primary:hover{background:#941025}.entry-btn svg{width:16px;height:16px;flex:none}.entry-btn:focus-visible,.entry-tab:focus-visible,.entry-edit:focus-visible{outline:3px solid #c8102e55;outline-offset:3px}
.entry-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:white;border:1px solid var(--line);border-radius:10px;margin-bottom:26px;box-shadow:0 2px 5px #20233103;overflow:hidden}.entry-stat{padding:20px 24px;border-right:1px solid var(--line)}.entry-stat:last-child{border:0}.entry-stat-label{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;margin-bottom:10px}.entry-stat-label svg{width:15px;height:15px;color:#9398a5}.entry-stat strong{display:block;font-size:24px;letter-spacing:-.5px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.25}.entry-stat small{display:block;margin-top:5px;color:#858a98;font-size:11px}
.entry-tabs{display:flex;align-items:center;border-bottom:1px solid #dddfe6;gap:26px;margin-bottom:22px}.entry-tab{display:inline-flex;gap:9px;align-items:center;padding:0 1px 14px;color:var(--muted);font-weight:600;font-size:13px;border-bottom:2px solid transparent;margin-bottom:-1px;text-decoration:none!important}.entry-tab.active{color:var(--accent);border-color:var(--accent)}.entry-count{padding:2px 7px;background:#eceef2;color:#696e7c;border-radius:5px;font-size:11px;line-height:1.35}.entry-tab.active .entry-count{color:var(--accent);background:#f9e8ec}.entry-format{margin-left:auto;color:#808593;font-size:11px;padding-bottom:13px;white-space:nowrap}
.entry-panel{background:white;border:1px solid var(--line);border-radius:10px;box-shadow:0 2px 5px #20233103;overflow:hidden}.entry-panel-heading{padding:22px 24px 18px;display:flex;align-items:center;justify-content:space-between;gap:20px}.entry-panel h2{font-size:16px;letter-spacing:-.2px;margin:0 0 5px}.entry-panel-heading p{margin:0;color:var(--muted);font-size:12px}.entry-panel-heading .entry-note{margin:0;font-size:11px;white-space:nowrap}
.entries-page label{font-size:12px;color:#4c5161;font-weight:600;margin:0 0 7px}.entry-required{color:var(--accent);margin-left:3px}.entries-page input,.entries-page select,.entries-page textarea{width:100%;min-width:0;max-width:100%;padding:10px 11px;border:1px solid #dcdfe6;border-radius:6px;font:13px/1.4 "Segoe UI",sans-serif;background:#fff;color:var(--ink);min-height:40px;box-shadow:0 1px 2px #20233103}.entries-page input::placeholder,.entries-page textarea::placeholder{color:#9a9eab;opacity:1}.entries-page input:focus,.entries-page select:focus,.entries-page textarea:focus{outline:3px solid #b5122d12;border-color:#b5122d;box-shadow:none}.entry-field{min-width:0}.entry-field small{display:block;font-size:11px;color:#9499a5;margin-top:5px}.entry-date{font-variant-numeric:tabular-nums;letter-spacing:.2px}
.entry-date-control{position:relative;display:block}.entries-page .entry-date-control>.entry-date{padding-right:44px}.entry-calendar{position:absolute;right:2px;top:2px;bottom:2px;width:36px;display:flex;align-items:center;justify-content:center;border-radius:4px;color:#747d8e;background:#f6f7fa}.entry-calendar:hover{color:var(--accent);background:#f9e8ec}.entry-calendar:focus-within{outline:2px solid var(--accent);outline-offset:1px}.entry-calendar svg{height:18px;width:18px;pointer-events:none}.entries-page .entry-calendar input{position:absolute;inset:0;width:100%;height:100%;min-height:0;padding:0;border:0;opacity:0;cursor:pointer}.entry-calendar input::-webkit-calendar-picker-indicator{position:absolute;inset:0;width:100%;height:100%;margin:0;padding:0;cursor:pointer}
.ledger-filter{display:grid;grid-template-columns:minmax(240px,1fr) 160px 160px auto;gap:14px;align-items:end;padding:18px 24px 22px;background:#fcfcfd;border-top:1px solid #f1f2f5;border-bottom:1px solid var(--line)}.ledger-filter .entry-field small{display:none}.ledger-filter-actions{display:flex;gap:9px;align-items:center}.entry-clear{font-size:12px;color:var(--muted);padding:10px 2px}.entry-filter-note{padding:11px 24px;color:#777c8b;font-size:12px;background:#fcfcfd;border-bottom:1px solid var(--line)}
/* Use the available screen width for the register; entry forms retain their reading width. */
.wrap:has(#ledger:not([hidden])){max-width:none}
.entry-scroll{overflow:visible;max-height:none}
.entry-table{width:100%;min-width:0;table-layout:fixed;border-collapse:collapse;margin:0;font-size:12px}
.entry-table th{position:sticky;top:0;background:#f8f9fb;color:#737888;font-size:10px;text-transform:uppercase;letter-spacing:.3px;font-weight:650;padding:12px 8px;border-bottom:1px solid var(--line);white-space:normal;overflow-wrap:normal;z-index:1}
.entry-table td{padding:12px 8px;vertical-align:middle;border-bottom:1px solid #eef0f4;color:#505666;line-height:1.45;overflow-wrap:anywhere;min-width:0}
.entry-table th:first-child,.entry-table td:first-child{padding-left:14px}
.entry-table th:last-child,.entry-table td:last-child{padding-right:14px}
.entry-table tr:last-child td{border-bottom:0}
.entry-table tbody tr:hover{background:#fafbfd}
.entry-table tbody tr.saved-row{background:#f0faf5}
.entry-table .entry-col-invoice_date{font-variant-numeric:tabular-nums;color:#606677}
.entry-invoice{font-weight:650;color:#272e3d;white-space:normal;overflow-wrap:anywhere}
.entry-line{display:block;color:#969ba6;font-size:10px;margin-top:3px}
.entry-table .entry-col-customer{font-weight:500;color:#343c4d}
.entry-table .entry-money{text-align:right;font-weight:650;color:#293244;white-space:normal;font-variant-numeric:tabular-nums}
.entry-table .entry-col-courier{font-size:11px;font-weight:600}
.entry-empty-value{color:#a0a5af;font-size:11px}
.entry-table .entry-col-courier_docket_no{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.entry-action-list{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.entry-edit{display:inline-flex;align-items:center;justify-content:center;gap:4px;max-width:100%;color:#606a7c;font-size:11px;font-weight:600;line-height:1.35;white-space:normal;padding:6px;border:1px solid #e2e5eb;border-radius:5px}
.entry-edit svg{height:12px;width:12px;flex:0 0 12px}
.entry-edit:hover{border-color:#b5122d;color:#b5122d;text-decoration:none!important}
/* Narrow screens show each record as labeled cells, keeping its action alongside it. */
@media(max-width:1100px){
  .entry-table,.entry-table tbody{display:block;width:100%}
  .entry-table colgroup{display:none}
  .entry-table thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
  .entry-table tbody tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));padding:14px 16px;gap:12px 18px;border-bottom:1px solid var(--line)}
  .entry-table tbody tr:last-child{border-bottom:0}
  .entry-table td,.entry-table td:first-child,.entry-table td:last-child{display:block;border:0;padding:0;text-align:left;min-width:0}
  .entry-table td[data-label]::before{content:attr(data-label);display:block;margin-bottom:4px;font-size:10px;font-weight:600;color:#7b8190;letter-spacing:.3px;text-transform:uppercase}
  .entry-table .entry-actions{grid-column:1/-1;border-top:1px solid #eef0f4;padding-top:12px}
  .entry-table .entry-actions::before{display:none}
  .entry-action-list{justify-content:flex-end}
  .entry-edit{min-height:36px;padding:7px 12px}
  .entry-table .entry-empty-state{grid-column:1/-1}
}

.entry-pagination{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:16px 24px;border-top:1px solid var(--line);color:#818695;font-size:12px}.entry-pagination strong{color:#474f60;font-weight:600}.entry-page-controls{display:flex;align-items:center;gap:12px}.entry-page-controls .entry-btn{padding:6px 11px;font-size:11px;min-height:32px}.entry-btn[aria-disabled=true]{background:#fafbfc;color:#b6bbc5;cursor:default}.entry-empty-state{text-align:center;padding:58px 20px!important;color:#7a8191!important}.entry-empty-state strong{display:block;font-size:15px;color:#343c4d;margin-bottom:8px}.entry-empty-state a{display:inline-block;margin-top:14px}
.entry-form-heading{border-bottom:1px solid var(--line)}.entry-form-grid{display:grid;grid-template-columns:minmax(0,1fr)}.entry-form-main{min-width:0}.entry-section{border:0;padding:25px 28px;margin:0;border-bottom:1px solid #edf0f4;min-width:0}.entry-section legend{float:left;display:flex;align-items:center;gap:10px;width:100%;padding:0;margin:0 0 20px;font-size:14px;font-weight:650;color:#32394a}.entry-section legend .step{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;background:#f2f3f6;color:#898e9c;border-radius:6px;font-size:11px;font-weight:650}.entry-grid{clear:both;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px 18px}.entry-grid .entry-full{grid-column:1/-1}.entry-section-note{clear:both;margin:0 0 16px;color:#8a8f9b;font-size:12px}.entry-form-aside{padding:26px 23px;border-left:1px solid var(--line);background:#fcfcfd}.entry-aside-title{display:flex;align-items:center;gap:8px;margin-bottom:15px;font-size:12px;font-weight:650;color:#4d5668}.entry-aside-title svg{height:16px;width:16px;color:#a9adba}.entry-form-aside p{font-size:12px;line-height:1.7;color:#858b99;margin:0 0 16px}.entry-form-aside strong{color:#596274;font-weight:600}.entry-aside-divider{border:0;border-top:1px solid #e9ebf0;margin:22px 0}.entry-form-actions{display:flex;justify-content:flex-end;align-items:center;gap:20px;padding:20px 28px;background:#fcfcfd;border-top:1px solid var(--line)}.entry-form-actions p{margin:0;color:#8c919f;font-size:11px}.entry-form-buttons{display:flex;align-items:center;gap:12px}.entry-note{color:var(--muted);font-size:12px}.entries-page .err,.entries-page .ok{margin:0 0 20px;padding:13px 16px;font-size:13px}.entry-footer-note{font-size:11px;color:#9297a3;margin:16px 2px 0}.entries-page textarea{resize:vertical;min-height:75px}
@media(min-width:1600px){.wrap{padding-top:38px}}
@media(max-width:1100px){.entry-form-grid{grid-template-columns:1fr}.entry-form-aside{display:none}.entry-stat{padding:18px}.entry-stat strong{font-size:21px}.ledger-filter{grid-template-columns:minmax(180px,1fr) 145px 145px auto}}
@media(max-width:800px){.wrap{padding:24px 18px 40px}.bar{padding:12px 18px;flex-wrap:wrap;gap:9px}.bar .sp{width:100%;margin:3px 0 0;font-size:11px;line-height:2}.entry-heading{align-items:flex-start;flex-direction:column;gap:16px}.entry-heading h1{font-size:26px}.entry-stats{grid-template-columns:repeat(2,minmax(0,1fr));margin-bottom:22px}.entry-stat:nth-child(2){border-right:0}.entry-stat:nth-child(-n+2){border-bottom:1px solid var(--line)}.entry-stat strong{font-size:22px}.ledger-filter{grid-template-columns:1fr 1fr;padding:18px}.ledger-filter .entry-field:first-child{grid-column:1/-1}.ledger-filter-actions{grid-column:1/-1;justify-content:flex-end}.entry-panel-heading{padding:20px 18px}.entry-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.entry-section{padding:22px}.entry-form-actions{padding:18px 22px}.entry-pagination{padding:16px 18px}.entry-format{font-size:10px}.entry-tabs{gap:20px}.entry-tab{font-size:12px}}
@media(max-width:480px){.wrap{padding:20px 12px 36px}.entry-heading p{font-size:12px;max-width:310px}.entry-heading-actions{width:100%}.entry-heading-actions .entry-btn{flex:1;padding:10px;font-size:12px}.entry-stat{padding:16px 14px}.entry-stat-label{font-size:11px;gap:5px}.entry-stat-label svg{display:none}.entry-stat strong{font-size:clamp(15px,4.5vw,20px)}.entry-stat small{font-size:10px}.entry-format{display:none}.entry-panel-heading{align-items:flex-start;gap:10px}.entry-panel-heading .entry-note{font-size:10px}.entry-grid{grid-template-columns:1fr}.entry-section{padding:22px 18px}.entry-form-actions{align-items:stretch;flex-direction:column-reverse;gap:12px;padding:18px}.entry-form-buttons{justify-content:space-between}.entry-form-buttons .primary{flex:1}.entry-pagination{align-items:flex-start;flex-direction:column;gap:13px}.entry-page-controls{width:100%;justify-content:space-between}.entry-tab{gap:6px}.entry-filter-note{padding:10px 18px}}
</style>"""

DATE_SCRIPT = """<script>
(()=>{document.querySelectorAll('input[data-entry-date]').forEach(input=>{
  // Disabled fieldsets also protect invoice dates for dispatch-only users.
  if(input.matches(':disabled')||input.readOnly)return;
  const picker=document.createElement('input');picker.type='date';
  // Keep the text field usable in browsers without native calendar support.
  if(picker.type!=='date')return;
  picker.min='0001-01-01';picker.max='9999-12-31';
  picker.id=input.id+'-calendar';picker.dataset.entryCalendar=input.id;
  const label=input.labels[0]?.textContent.replace('*','').trim()||'date';
  picker.setAttribute('aria-label','Choose '+label);
  picker.title='Choose '+label;
  const control=document.createElement('span');control.className='entry-date-control';
  const calendar=document.createElement('span');calendar.className='entry-calendar';
  calendar.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18M8 15h2M14 15h2M8 18h2"/></svg>';
  input.before(control);control.append(input,calendar);calendar.append(picker);
  function syncPicker(){
    const match=/^([0-9]{2})-([0-9]{2})-([0-9]{4})$/.exec(input.value.trim());
    // Native validation rejects impossible dates without changing the typed text.
    picker.value=match?match[3]+'-'+match[2]+'-'+match[1]:'';
  }
  input.addEventListener('input',syncPicker);
  input.addEventListener('change',syncPicker);
  input.addEventListener('blur',()=>{
    const raw=input.value.trim();
    if(/^[0-9]{8}$/.test(raw))input.value=raw.slice(0,2)+'-'+raw.slice(2,4)+'-'+raw.slice(4);
    syncPicker();
  });
  picker.addEventListener('change',()=>{
    const match=/^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(picker.value);
    input.value=match?match[3]+'-'+match[2]+'-'+match[1]:'';
    input.dispatchEvent(new Event('input',{bubbles:true}));
    input.dispatchEvent(new Event('change',{bubbles:true}));
  });
  function openPicker(event){
    syncPicker();
    if(typeof picker.showPicker==='function'){
      try{picker.showPicker();event.preventDefault();}catch(error){/* Native indicator remains available. */}
    }
  }
  picker.addEventListener('click',openPicker);
  picker.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' ')openPicker(event);});
  // A form reset restores the text field first; then update its calendar.
  input.form?.addEventListener('reset',()=>setTimeout(syncPicker,0));
  syncPicker();
});})();
</script>"""
