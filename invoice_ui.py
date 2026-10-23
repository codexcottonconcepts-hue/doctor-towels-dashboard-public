"""Product invoice entry widget, embedded only for users who may view sales values."""
import json
from html import escape
from pathlib import Path


PRICE_LABELS = (
    ("common_mrp", "Common MRP"),
    ("north_retail", "North retail offer"),
    ("gifting", "Trruu / Other gifting"),
    ("lulu_retail", "Retail offer"),
)

STYLE = """<style>
.invoice-widget{grid-column:1/-1;min-width:0}.invoice-widget [hidden]{display:none!important}
.invoice-toolbar{display:grid;grid-template-columns:minmax(180px,1fr);gap:16px;max-width:340px;margin-bottom:14px}.invoice-toolbar label{display:block}
.invoice-items-box{border:1px solid #e2e6ee;border-radius:9px;overflow:hidden;background:#fff}.invoice-items-table{border-collapse:collapse;width:100%;table-layout:fixed;margin:0;font-size:12px}
.invoice-items-table thead th{background:#f6f7fa;color:#647086;font-size:10px;font-weight:650;text-align:right;padding:11px 7px;border-bottom:1px solid #e2e6ee;white-space:normal;line-height:1.5}
.invoice-items-table thead th:first-child{text-align:left;padding-left:12px;width:31%}.invoice-items-table.has-colour thead th:first-child{width:24%}.invoice-items-table th.invoice-col-colour{text-align:left;width:13%}.invoice-items-table th.invoice-col-qty{width:8%}.invoice-items-table th.invoice-col-rate{width:12%}.invoice-items-table th.invoice-col-discount{width:9%}.invoice-items-table th:last-child{width:34px}
.invoice-items-table td{padding:12px 6px;vertical-align:top;border-bottom:1px solid #edf0f4;min-width:0}.invoice-items-table td:first-child{padding-left:12px}.invoice-items-table tbody tr:last-child td{border-bottom:0}
.invoice-items-table .invoice-money{text-align:right;font-variant-numeric:tabular-nums;font-weight:600;padding-top:24px;overflow-wrap:anywhere;color:#303a4e}
.invoice-items-table input{font-size:12px;padding:8px 7px;min-height:36px}.invoice-items-table input[type=number]{text-align:right}.invoice-items-table .invoice-product-input{width:100%;margin:0}
.invoice-product-options{position:fixed;z-index:10020;overflow:auto;overscroll-behavior:contain;background:#fff;border:1px solid #d9dfeb;border-radius:8px;box-shadow:0 8px 24px #16243d26;padding:4px;font:12px/1.5 system-ui,sans-serif;color:#263245;box-sizing:border-box}.invoice-product-options[hidden]{display:none!important}
.invoice-product-option{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 9px;cursor:pointer;border-radius:5px}.invoice-product-option:hover,.invoice-product-option.is-active{background:#f9e9ee;color:#a2102b}.invoice-option-name{min-width:0;overflow-wrap:anywhere}.invoice-option-price{flex-shrink:0;font-size:11px;font-variant-numeric:tabular-nums;color:#707d91}.invoice-product-empty{padding:12px;color:#747f91}.invoice-draft{background:#fafbfd}
.invoice-product-meta{color:#7d8593;font-size:10px;line-height:1.5;margin:6px 1px 0;overflow-wrap:anywhere}.invoice-product-note{color:#916319}.invoice-line-error{color:#b5122d;font-size:11px;line-height:1.5;margin-top:5px}
.invoice-remove{display:flex;align-items:center;justify-content:center;width:26px;height:32px;min-height:32px;padding:0;border:1px solid #e4e7ed;border-radius:5px;background:white;color:#9a4656;font-size:19px;cursor:pointer;margin-top:3px}.invoice-remove:hover{background:#fff2f4;border-color:#d498a3}.invoice-remove:focus-visible{outline:2px solid #b5122d;outline-offset:2px}
.invoice-row-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;background:#fafbfd;border-top:1px solid #edf0f4}.invoice-row-actions span{font-size:11px;color:#7b8493}.invoice-row-actions .entry-btn{min-height:34px;padding:7px 12px;font-size:12px}.invoice-items-table .invoice-colour-select{width:100%;margin:0;font-size:12px;min-height:36px}.invoice-items-table .dt-select-trigger{min-height:36px;font-size:12px}
.invoice-totals{position:sticky;bottom:0;z-index:2;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:#f6f8fb;border:1px solid #e2e6ee;border-radius:8px;margin-top:12px;box-shadow:0 -3px 8px #ffffffaa}
.invoice-total{padding:13px 14px;border-right:1px solid #e2e6ee;min-width:0}.invoice-total:last-child{border:0;background:#f9eff2;border-radius:0 8px 8px 0}.invoice-total span{display:block;font-size:10px;color:#717d8e;margin-bottom:5px}.invoice-total strong{display:block;font-variant-numeric:tabular-nums;font-size:16px;overflow-wrap:anywhere;color:#263245}.invoice-total:last-child strong{color:#a2102b}
.invoice-status{color:#b5122d;font-size:12px;line-height:1.5;margin:10px 0 0}.invoice-sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.invoice-widget button:disabled{opacity:.5;cursor:default}.invoice-empty{padding:18px!important;color:#747f91;text-align:center}
@media(max-width:1100px){.invoice-items-table thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}.invoice-items-table,.invoice-items-table tbody{display:block}.invoice-items-table tbody tr{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));position:relative;gap:12px;padding:14px;border-bottom:1px solid #e2e6ee}.invoice-items-table tbody tr:last-child{border-bottom:0}.invoice-items-table td,.invoice-items-table td:first-child{display:block;padding:0;border:0}.invoice-items-table td:first-child{grid-column:1/-1;padding-right:32px}.invoice-items-table td[data-label]::before{content:attr(data-label);display:block;font-size:10px;color:#6e7a8e;margin-bottom:6px;text-align:left;font-weight:500}.invoice-items-table .invoice-money{padding-top:0;text-align:left}.invoice-items-table .invoice-remove-cell{position:absolute;top:14px;right:12px}.invoice-items-table .invoice-empty{grid-column:1/-1;padding-right:0!important}.invoice-items-table input[type=number]{text-align:left}.invoice-items-table .dt-select-value{white-space:normal}.invoice-total strong{font-size:14px}}
@media(max-width:560px){.invoice-toolbar{grid-template-columns:1fr;gap:12px}.invoice-totals{grid-template-columns:repeat(2,minmax(0,1fr));position:static}.invoice-total:nth-child(2){border-right:0}.invoice-total:nth-child(-n+2){border-bottom:1px solid #e2e6ee}.invoice-total:last-child{border-radius:0 0 8px 0}.invoice-row-actions{align-items:flex-start;flex-direction:column}.invoice-items-table tbody tr{gap:10px}.invoice-total strong{font-size:16px}}
</style>"""


def _json(value):
    # Embedded JSON is data, not executable script. Escape HTML terminators too.
    return json.dumps(value, ensure_ascii=False, default=str).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render(catalog, values, editable=True, colours=()):
    """Render within the sales values fieldset; its enclosing form owns save/CSRF."""
    saved = bool(values.get("_invoice_saved"))
    legacy = bool(values.get("entry_id") or values.get("_invoice_summary_only")) and not saved
    if legacy:
        # Imported and historical entries have no product rows. Keep their
        # existing values editable without offering an entry-method selector.
        return '<input type="hidden" id="entry_mode" name="entry_mode" value="summary">'
    mode = "invoice"
    price_list = str(values.get("price_list", "common_mrp"))
    raw_items = values.get("invoice_items", "[]")
    if not isinstance(raw_items, str):
        raw_items = json.dumps(raw_items, ensure_ascii=False, default=str)
    payload = {
        "catalog": list(catalog), "editable": bool(editable), "saved": saved,
        "mode": mode, "priceList": price_list, "rawItems": raw_items,
        "snapshots": values.get("_invoice_snapshots", []) if saved else [],
        "colours": list(colours),
    }
    colour_head = '<th scope="col" class="invoice-col-colour">Colour</th>' if colours else ''
    disabled = " disabled" if not editable else ""
    price_options = ''.join(f'<option value="{key}"{" selected" if price_list == key else ""}>{label}</option>' for key, label in PRICE_LABELS)
    script = Path(__file__).with_name("invoice_entry.js").read_text(encoding="utf-8")
    return STYLE + f'''<div id="product-invoice-widget" class="invoice-widget{" invoice-readonly" if not editable else ""}">
<input type="hidden" id="entry_mode" name="entry_mode" value="invoice"{disabled}><div class="invoice-toolbar">
<div id="invoice-price-list-field"{" hidden" if mode != "invoice" else ""}><label for="price_list">Price list for new items</label><select id="price_list" name="price_list"{disabled}>{price_options}</select></div></div>
<div id="invoice-product-panel">
<div class="invoice-items-box"><table class="invoice-items-table{" has-colour" if colours else ""}" aria-label="Invoice product items"><thead><tr><th scope="col">Product</th>{colour_head}<th scope="col" class="invoice-col-qty">Qty</th><th scope="col" class="invoice-col-rate">Rate incl. GST (₹)</th><th scope="col" class="invoice-col-discount">Discount %<br>(before GST)</th><th scope="col">Taxable (₹)</th><th scope="col">GST 18% (₹)</th><th scope="col">Total (₹)</th><th scope="col"><span class="invoice-sr-only">Remove</span></th></tr></thead><tbody id="invoice-item-rows"></tbody></table>
<div class="invoice-row-actions"><span id="invoice-item-count">0 items</span><button id="invoice-next-section" type="button" class="entry-btn"{disabled}>Continue →</button></div></div>
<div class="invoice-totals" aria-label="Invoice totals"><div class="invoice-total"><span>Total quantity</span><strong id="invoice-total-quantity">0.00</strong></div><div class="invoice-total"><span>Taxable value</span><strong id="invoice-total-taxable">₹0.00</strong></div><div class="invoice-total"><span>GST 18%</span><strong id="invoice-total-gst">₹0.00</strong></div><div class="invoice-total"><span>Invoice total</span><strong id="invoice-total-amount">₹0.00</strong></div></div>
<p id="invoice-item-status" class="invoice-status" role="status" aria-live="polite"></p>
<noscript><p class="invoice-status">Enable JavaScript to view and edit product invoice items.</p></noscript>
</div><input type="hidden" id="invoice_items" name="invoice_items" value="{escape(raw_items, quote=True)}"{disabled}>
<script type="application/json" id="invoice-widget-data">{_json(payload)}</script><script>{script}</script></div>'''
