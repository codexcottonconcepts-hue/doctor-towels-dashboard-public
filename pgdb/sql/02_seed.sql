-- =====================================================================
-- 02_seed.sql   Reference data.
--
-- Safe to re-run: every insert is ON CONFLICT DO NOTHING / DO UPDATE.
-- Public reference categories and explicitly fictional demo retailers.
-- No customer records, staff assignments, sales or business targets are seeded.
-- =====================================================================

begin;

-- ---------------------------------------------------------------- years
insert into master.financial_year (fy, label, start_date, end_date, is_current) values
    (2024, '2024-25', '2024-04-01', '2025-03-31', false),
    (2025, '2025-26', '2025-04-01', '2026-03-31', false),
    (2026, '2026-27', '2026-04-01', '2027-03-31', true ),
    (2027, '2027-28', '2027-04-01', '2028-03-31', false),
    (2028, '2028-29', '2028-04-01', '2029-03-31', false)
on conflict (fy) do nothing;


-- ------------------------------------------------------------- channels
insert into master.channel (code, name, sort_order) values
    ('LFO',     'Large Format Outlets',            10),
    ('L-LFO',   'Local Large Format Outlets',      20),
    ('D&R-S',   'Distributor & Retail - South',    30),
    ('D&R-N',   'Distributor & Retail - North',    40),
    ('Saloon',  'Saloon',                          50),
    ('Hospital','Hospital',                        60)
on conflict do nothing;


-- --------------------------------------------------------- sub-channels
-- channel_id below is the usual channel only; see the note on the table.
insert into master.sub_channel (code, name, channel_id)
select v.code, v.name, c.channel_id
from (values
    ('Demo Retailer A', 'Fictional Demo Retailer A', 'LFO'),
    ('Demo Retailer B', 'Fictional Demo Retailer B', 'L-LFO'),
    ('Distribution','Distribution',            'D&R-S'),
    ('Direct',      'Direct',                  'D&R-S'),
    ('Demo Salon',  'Fictional Demo Salon',    'Saloon')
) as v(code, name, chan)
left join master.channel c on lower(c.code) = lower(v.chan)
on conflict do nothing;


-- ---------------------------------------------------------------- brands
insert into master.brand (code, name) values
    ('DT', 'Doctor Towel'),
    ('TT', 'Thirsty Towel')
on conflict do nothing;


-- -------------------------------------------------------------- couriers
-- Add a row here when a new courier starts being used; nothing else changes.
insert into master.courier (code, name, tracking_url_template, sort_order) values
    ('DTDC', 'DTDC Express', 'https://www.dtdc.in/tracking/shipment-tracking?awb={docket}', 10),
    ('Bluedart', 'Bluedart', null, 20),
    ('Pionexxco', 'Pionexxco', null, 30),
    ('RK', 'RK', null, 40)
on conflict (lower(code)) do update set is_active = true;

-- Preserve historical carriers separately; RK is not an RKB alias.
insert into master.courier (code, name, tracking_url_template, sort_order) values
    ('OWN', 'Own Delivery', null, 50),
    ('RKB', 'RKB Logistics', null, 60)
on conflict do nothing;


-- ---------------------------------------------------------------- states
-- Canonical spellings. The three misspellings live in the sheet today
-- (Telengana, Odissa, Rajastan) and are mapped away by the loader, not
-- stored here. 'Aurangabad' in the state column is a city and is corrected
-- to Maharashtra on load.
insert into master.state (name, code, zone) values
    ('Tamil Nadu',      'TN', 'South'),
    ('Kerala',          'KL', 'South'),
    ('Karnataka',       'KA', 'South'),
    ('Andhra Pradesh',  'AP', 'South'),
    ('Telangana',       'TG', 'South'),
    ('Puducherry',      'PY', 'South'),
    ('Maharashtra',     'MH', 'West'),
    ('Gujarat',         'GJ', 'West'),
    ('Goa',             'GA', 'West'),
    ('Rajasthan',       'RJ', 'North'),
    ('Delhi',           'DL', 'North'),
    ('Uttar Pradesh',   'UP', 'North'),
    ('Uttarakhand',     'UK', 'North'),
    ('Punjab',          'PB', 'North'),
    ('Haryana',         'HR', 'North'),
    ('Himachal Pradesh','HP', 'North'),
    ('Chandigarh',      'CH', 'North'),
    ('Jammu & Kashmir', 'JK', 'North'),
    ('Madhya Pradesh',  'MP', 'Central'),
    ('Chhattisgarh',    'CG', 'Central'),
    ('West Bengal',     'WB', 'East'),
    ('Bihar',           'BR', 'East'),
    ('Jharkhand',       'JH', 'East'),
    ('Odisha',          'OD', 'East'),
    ('Assam',           'AS', 'North East'),
    ('Meghalaya',       'ML', 'North East'),
    ('Tripura',         'TR', 'North East'),
    ('Manipur',         'MN', 'North East'),
    ('Nagaland',        'NL', 'North East'),
    ('Mizoram',         'MZ', 'North East'),
    ('Arunachal Pradesh','AR','North East'),
    ('Sikkim',          'SK', 'North East'),
    ('Dadra & Nagar Haveli and Daman & Diu', 'DH', 'West')
on conflict do nothing;


-- ------------------------------------------------------------- districts
-- Seeded for the states that carry the business. Others are added as the
-- team fills them in; district is optional on a sales row.
insert into master.district (state_id, name)
select s.state_id, d.name
from (values
    ('Tamil Nadu','Chennai'),        ('Tamil Nadu','Coimbatore'),
    ('Tamil Nadu','Madurai'),        ('Tamil Nadu','Tiruchirappalli'),
    ('Tamil Nadu','Salem'),          ('Tamil Nadu','Tirupur'),
    ('Tamil Nadu','Erode'),          ('Tamil Nadu','Vellore'),
    ('Tamil Nadu','Tirunelveli'),    ('Tamil Nadu','Dindigul'),
    ('Tamil Nadu','Sivaganga'),      ('Tamil Nadu','Thanjavur'),
    ('Tamil Nadu','Kanyakumari'),    ('Tamil Nadu','Tenkasi'),
    ('Tamil Nadu','Chengalpattu'),
    ('Kerala','Thiruvananthapuram'), ('Kerala','Ernakulam'),
    ('Kerala','Kozhikode'),          ('Kerala','Kottayam'),
    ('Kerala','Palakkad'),           ('Kerala','Kollam'),
    ('Karnataka','Bengaluru Urban'), ('Karnataka','Belagavi'),
    ('Karnataka','Mysuru'),          ('Karnataka','Shivamogga'),
    ('Telangana','Hyderabad'),
    ('Andhra Pradesh','Guntur'),     ('Andhra Pradesh','Visakhapatnam'),
    ('Andhra Pradesh','West Godavari'),
    ('Andhra Pradesh','Srikakulam'), ('Andhra Pradesh','East Godavari'),
    ('Maharashtra','Mumbai Suburban'),('Maharashtra','Pune'),
    ('Maharashtra','Thane'),         ('Maharashtra','Raigad'),
    ('Maharashtra','Chhatrapati Sambhajinagar'),
    ('Uttar Pradesh','Lucknow'),     ('Uttar Pradesh','Gautam Buddha Nagar'),
    ('Uttar Pradesh','Varanasi'),    ('Uttar Pradesh','Moradabad'),
    ('Jharkhand','Ranchi'),          ('Jharkhand','East Singhbhum'),
    ('Gujarat','Ahmedabad'),         ('Gujarat','Anand'),
    ('Madhya Pradesh','Indore'),
    ('Bihar','Begusarai'),           ('Bihar','Gaya'),
    ('Odisha','Khordha'),
    ('West Bengal','Kolkata'),
    ('Assam','Kamrup Metropolitan'),
    ('Rajasthan','Jaipur'),
    ('Delhi','New Delhi'),
    ('Chandigarh','Chandigarh'),
    ('Puducherry','Puducherry'),
    ('Dadra & Nagar Haveli and Daman & Diu','Dadra & Nagar Haveli')
) as d(state, name)
join master.state s on lower(s.name) = lower(d.state)
on conflict do nothing;


-- ----------------------------------------------------------------- roles
insert into sec.role (role_code, name, description) values
    ('admin',   'Administrator',  'Everything, including users and locking plans.'),
    ('manager', 'Sales Manager',  'Sees all data. Sets targets. Approves corrections.'),
    ('entry',   'Data Entry',     'Adds and edits sales rows. Cannot change targets.'),
    ('exec',    'Sales Executive','Sees and enters only their own rows.'),
    ('viewer',  'Viewer',         'Read-only dashboard and reports.')
on conflict (role_code) do update
    set name = excluded.name, description = excluded.description;

commit;
