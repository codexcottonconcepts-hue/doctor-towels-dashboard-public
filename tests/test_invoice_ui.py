"""Invoice widget escaping, arithmetic, and server/client calculation parity."""
import json
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import unittest

import invoice_ui


ROOT = Path(__file__).resolve().parents[1]


class ScriptData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_data = False
        self.data = ""
        self.selects = []
        self.options = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'script':
            self.in_data = attributes.get('id') == 'invoice-widget-data'
        if tag == 'select':
            self.selects.append(attributes)
        if tag == 'option':
            self.options.append(attributes)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_data = False

    def handle_data(self, data):
        if self.in_data:
            self.data += data


class InvoiceWidgetTests(unittest.TestCase):
    def test_catalog_names_and_posted_values_cannot_escape_embedded_data(self):
        value = '</script><img src=x onerror="alert(1)">'
        catalog = [{"product_id": 1, "catalog_key": "test", "product_name": value, "mrp": "118.00", "prices": {"common_mrp": "118.00"}}]
        posted = json.dumps([{"product_id": value, "quantity": "1", "unit_rate": "118", "discount_percent": "0"}])
        html = invoice_ui.render(catalog, {"entry_mode": "invoice", "invoice_items": posted})
        self.assertNotIn('<img', html)
        parser = ScriptData()
        parser.feed(html)
        data = json.loads(parser.data)
        self.assertEqual(data["catalog"][0]["product_name"], value)
        self.assertEqual(data["rawItems"], posted)

    def test_new_and_saved_invoices_open_directly_without_entry_method_choice(self):
        for values in ({}, {"_invoice_saved": True, "entry_mode": "summary"}):
            parser = ScriptData()
            html = invoice_ui.render([], values)
            parser.feed(html)
            self.assertEqual(json.loads(parser.data)["mode"], "invoice")
            self.assertFalse(any(select.get('name') == 'entry_mode' for select in parser.selects))
            self.assertNotIn('Summary entry', html)
            self.assertIn('name="entry_mode" value="invoice"', html)
            self.assertNotIn('Discount applies before GST.', html)

    def test_readonly_controls_and_original_item_payload_are_preserved(self):
        original = '[{"product_id":1,"quantity":"x","unit_rate":"118.00","discount_percent":"0"}]'
        parser = ScriptData()
        parser.feed(invoice_ui.render([], {"entry_mode": "invoice", "invoice_items": original}, editable=False))
        self.assertFalse(json.loads(parser.data)["editable"])
        self.assertEqual(json.loads(parser.data)["rawItems"], original)
        self.assertTrue(all('disabled' in select for select in parser.selects))

    def test_synced_summary_does_not_offer_product_conversion(self):
        parser = ScriptData()
        html = invoice_ui.render([], {"_invoice_summary_only": True, "entry_mode": "invoice"})
        parser.feed(html)
        self.assertEqual(parser.data, '')
        self.assertEqual(parser.selects, [])
        self.assertIn('name="entry_mode" value="summary"', html)
        self.assertNotIn('Summary entry', html)

    def test_error_render_receives_saved_display_snapshots_separately(self):
        snapshots = [{"product_id": 1, "product_name": "Original saved name", "mrp": Decimal('118.00'), "unit": None}]
        posted = '[{"product_id":1,"quantity":"bad","unit_rate":"99","discount_percent":"0"}]'
        parser = ScriptData()
        parser.feed(invoice_ui.render([], {"_invoice_saved": True, "_invoice_snapshots": snapshots, "invoice_items": posted}))
        data = json.loads(parser.data)
        self.assertEqual(data['snapshots'][0]['product_name'], 'Original saved name')
        self.assertEqual(data['snapshots'][0]['mrp'], '118.00')
        self.assertEqual(data['rawItems'], posted)

    def test_null_catalog_rates_are_not_filled_or_converted_to_zero(self):
        parser = ScriptData()
        parser.feed(invoice_ui.render([{"product_id": 1, "mrp": None, "prices": {"common_mrp": None}}], {}))
        self.assertIsNone(json.loads(parser.data)["catalog"][0]["prices"]["common_mrp"])

    def test_product_entry_has_no_help_text_or_add_button(self):
        html = invoice_ui.render([], {})
        self.assertNotIn('id="invoice-add-item"', html)
        self.assertNotIn('+ Add product', html)
        self.assertIn('id="invoice-next-section" type="button"', html)
        self.assertIn('invoice-product-options', html)
        for text in ('Rates include 18% GST', 'Price suggestions:', '<kbd>', 'Up to 100 per entry',
                     'Discount reduces the taxable value', 'Type a product, or press Enter'):
            self.assertNotIn(text, html)

    def test_colour_column_and_choices_appear_only_when_colours_exist(self):
        colours = [{"name": "Navy Blue", "hex": "#1f2a52"}, {"name": "Assorted", "hex": None}]
        parser = ScriptData()
        html = invoice_ui.render([], {}, colours=colours)
        parser.feed(html)
        self.assertEqual(json.loads(parser.data)["colours"], colours)
        self.assertIn('<th scope="col" class="invoice-col-colour">Colour</th>', html)
        self.assertIn('invoice-items-table has-colour"', html)
        parser = ScriptData()
        html = invoice_ui.render([], {})
        parser.feed(html)
        self.assertEqual(json.loads(parser.data)["colours"], [])
        self.assertNotIn('<th scope="col" class="invoice-col-colour">', html)

    def test_javascript_arithmetic_matches_independent_decimal_cases(self):
        cases = [
            {"quantity": "2", "unit_rate": "118", "discount_percent": "10"},
            {"quantity": "0.5", "unit_rate": "0.01", "discount_percent": "0"},
            {"quantity": "1", "unit_rate": "0.01", "discount_percent": "50"},
            {"quantity": "1", "unit_rate": "118", "discount_percent": "12.50"},
            {"quantity": "1.37", "unit_rate": "29.99", "discount_percent": "7.33"},
            {"quantity": "100.05", "unit_rate": "0", "discount_percent": "0"},
            {"quantity": "2", "unit_rate": "118", "discount_percent": "100"},
            {"quantity": "1", "unit_rate": "999999999999.99", "discount_percent": "0"},
        ]
        invalid = [
            {"quantity": "0", "unit_rate": "10", "discount_percent": "0"},
            {"quantity": "1", "unit_rate": "", "discount_percent": "0"},
            {"quantity": "1", "unit_rate": "10", "discount_percent": "100.01"},
            {"quantity": "1.001", "unit_rate": "10", "discount_percent": "0"},
            {"quantity": "1", "unit_rate": "-1", "discount_percent": "0"},
            {"quantity": "1", "unit_rate": "10", "discount_percent": ""},
            {"quantity": "2", "unit_rate": "999999999999.99", "discount_percent": "0"},
        ]
        script = "const fs=require('fs'),calc=require('./invoice_entry.js');const data=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(data.map(row=>calc.calculate(row)),(key,value)=>typeof value==='bigint'?calc.decimal(value):value));"
        result = subprocess.run(['node', '-e', script], cwd=ROOT, input=json.dumps(cases + invalid), capture_output=True, text=True, check=True)
        values = json.loads(result.stdout)
        cent = Decimal('0.01')
        rounded = lambda value: value.quantize(cent, rounding=ROUND_HALF_UP)
        for case, value in zip(cases, values):
            gross = rounded(Decimal(case['quantity']) * Decimal(case['unit_rate']))
            discount = rounded(gross * Decimal(case['discount_percent']) / 100)
            total = gross - discount
            taxable = rounded(total / Decimal('1.18'))
            for key, expected in [('gross', gross), ('discount', discount), ('total', total), ('taxable', taxable), ('gst', total - taxable)]:
                self.assertEqual(value[key], format(expected, '.2f'), (case, key))
        self.assertTrue(all('error' in value for value in values[len(cases):]))


if __name__ == '__main__':
    unittest.main()
