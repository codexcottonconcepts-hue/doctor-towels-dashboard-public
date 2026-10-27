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
<div id="customer-details-panel" class="customer-details"
     data-can-edit-profile="{'true' if can_edit_profile else 'false'}"
     data-can-view-profile="true"
     data-profile-url="/entries/customers">
  <div class="customer-details-toolbar">
    <span id="customer-dialog-title" class="customer-details-caption" hidden>Customer details</span>
    <div class="customer-details-actions">
      <button type="button" id="customer-add" class="entry-btn customer-action"{' hidden' if new or not can_edit_profile else ''}>+ Add customer</button>
      <button type="button" id="customer-edit" class="entry-btn customer-action" hidden>Edit</button>
      <button type="button" id="customer-update" class="entry-btn primary customer-action" hidden>Update customer</button>
      <button type="button" id="customer-done" class="entry-btn primary customer-action" hidden>Done</button>
      <button type="button" id="customer-cancel" class="entry-btn customer-action"{'' if new else ' hidden'}>Cancel</button>
      <button type="button" id="customer-close" class="entry-btn customer-action customer-editor-close" aria-label="Close customer editor" hidden>×</button>
    </div>
  </div>
  <div id="customer-new-name" class="entry-field customer-new-name"{hidden}>
    <label for="new_customer">New customer name<span class="entry-required">*</span></label>
    <input id="new_customer" name="new_customer" value="{_text(values.get('new_customer'))}" maxlength="200" placeholder="Enter customer name"{disabled}>
  </div>
  <div id="customer-details-profile" class="customer-profile-grid" hidden>
    <div class="entry-field customer-address-field">
      <label for="customer_address">Address</label>
      <textarea id="customer_address" name="customer_address" maxlength="2000" rows="2" placeholder="Building, street and area"{disabled}>{_text(values.get('customer_address'))}</textarea>
    </div>
    <div class="entry-field">
      <label for="customer_contact_number">Contact number</label>
      <input id="customer_contact_number" name="customer_contact_number" type="tel" value="{_text(values.get('customer_contact_number'))}" maxlength="40" autocomplete="tel" placeholder="Optional contact number"{disabled}>
    </div>
    <div class="entry-field">
      <label for="customer_gstin">GSTIN number</label>
      <input id="customer_gstin" name="customer_gstin" value="{_text(values.get('customer_gstin'))}" maxlength="15" autocapitalize="characters" spellcheck="false" placeholder="Optional GSTIN"{disabled}>
    </div>
  </div>
  <input id="customer_location" name="customer_location" type="hidden" value="{_text(values.get('customer_location'))}"{disabled}>
  <p id="customer-status" class="customer-status" role="status" aria-live="polite" hidden></p>
</div>'''


def assets():
    root = Path(__file__).resolve().parent
    return ('<style id="customer-details-styles">'
            + (root / 'customer_details.css').read_text(encoding='utf-8')
            + '</style><script id="customer-details-script">'
            + (root / 'customer_details.js').read_text(encoding='utf-8')
            + '</script>')
