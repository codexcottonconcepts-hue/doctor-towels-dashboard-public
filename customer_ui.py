"""Customer details controls used inside the sales-entry invoice section."""
from html import escape
from pathlib import Path


def _text(value):
    return escape('' if value is None else str(value), quote=True)


def render(values, *, can_edit_profile=False, can_view_profile=True):
    """Render profile controls; the caller owns customer and location selects."""
    if not can_view_profile:
        return ''
    new = bool(str(values.get('new_customer') or '').strip())
    enabled = new and can_edit_profile
    disabled = '' if enabled else ' disabled'
    hidden = '' if new else ' hidden'
    return f'''
<div id="customer-details-panel" class="customer-details entry-full"
     data-can-edit-profile="{'true' if can_edit_profile else 'false'}"
     data-can-view-profile="true"
     data-profile-url="/entries/customers">
  <div class="customer-details-toolbar">
    <span class="customer-details-caption">Customer details</span>
    <div class="customer-details-actions">
      <button type="button" id="customer-add" class="entry-btn customer-action"{' hidden' if new or not can_edit_profile else ''}>+ Add customer</button>
      <button type="button" id="customer-edit" class="entry-btn customer-action" hidden>Edit customer details</button>
      <button type="button" id="customer-update" class="entry-btn primary customer-action" hidden>Update customer</button>
      <button type="button" id="customer-cancel" class="entry-btn customer-action"{'' if new else ' hidden'}>Cancel</button>
    </div>
  </div>
  <div id="customer-new-name" class="entry-field customer-new-name"{hidden}>
    <label for="new_customer">New customer name<span class="entry-required">*</span></label>
    <input id="new_customer" name="new_customer" value="{_text(values.get('new_customer'))}" maxlength="200" placeholder="Enter customer name"{' required' if enabled else ''}{disabled}>
  </div>
  <div id="customer-details-profile" class="customer-profile-grid"{hidden}>
    <div class="entry-field customer-address-field">
      <label for="customer_address">Address</label>
      <textarea id="customer_address" name="customer_address" maxlength="2000" rows="2" placeholder="Building, street and area"{disabled}>{_text(values.get('customer_address'))}</textarea>
    </div>
    <div class="entry-field">
      <label for="customer_location">Location / locality</label>
      <input id="customer_location" name="customer_location" value="{_text(values.get('customer_location'))}" maxlength="200" placeholder="Area or locality"{disabled}>
    </div>
    <div class="entry-field">
      <label for="customer_gstin">GSTIN number</label>
      <input id="customer_gstin" name="customer_gstin" value="{_text(values.get('customer_gstin'))}" maxlength="15" autocapitalize="characters" spellcheck="false" placeholder="Optional GSTIN"{disabled}>
    </div>
  </div>
  <p id="customer-profile-geography" class="customer-profile-geography" hidden></p>
  <p id="customer-status" class="customer-status" role="status" aria-live="polite">{'Choose the new customer’s state, district and city below. It will be added when this sales entry is saved.' if new else 'Select an existing customer or add a customer.'}</p>
</div>'''


def assets():
    root = Path(__file__).resolve().parent
    return ('<style id="customer-details-styles">'
            + (root / 'customer_details.css').read_text(encoding='utf-8')
            + '</style><script id="customer-details-script">'
            + (root / 'customer_details.js').read_text(encoding='utf-8')
            + '</script>')
