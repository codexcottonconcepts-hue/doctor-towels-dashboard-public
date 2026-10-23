"""Primary dashboard geometry must not depend on an unrelated Online Sales grant."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'Node.js is needed for dashboard layout checks')
class DashboardLayoutAccessTests(unittest.TestCase):
    def run_js(self, checks):
        fixture = r'''
const assert=require('node:assert/strict');
const window={};let page='home';
const classes=new Set(),queries=[];
const document={body:{classList:{toggle(key,on){on?classes.add(key):classes.delete(key)}}},
 querySelectorAll(selector){queries.push(selector);return []}};
'''
        source=(ROOT/'dashboard_access.js').read_text(encoding='utf-8-sig')
        hooks=r'''
dashboardTvEnabled=()=>false;dashboardTvHeader=()=>{};
dashboardTvLive=()=>{};dashboardTvSlideshow=()=>{};
const primary=DASHBOARD_WIDGET_KEYS.filter(key=>!key.startsWith('online_'));
const online=DASHBOARD_WIDGET_KEYS.filter(key=>key.startsWith('online_'));
'''
        result=subprocess.run([NODE,'-'],input=fixture+source+hooks+checks,
                              text=True,capture_output=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_full_primary_channel_restricted_accounts_use_admin_layout(self):
        self.run_js(r'''
for(const channelScopes of [['D&R-N','LFO'],['D&R-S','Saloon','L-LFO']]){
 window.DASHBOARD_ACCESS={widgets:[...primary],scopes:{channel:channelScopes}};
 for(page of ['home','lfo','llfo','drn','drs','saloon','balance','map','state','store','yoy','team']){
  assert.equal(dashboardPartialLayout(),false,page);
  queries.length=0;dashboardFitAccess();
  assert.equal(classes.has('dashboard-custom-layout'),false,page);
  assert.deepEqual(queries,[],page+' must preserve its authored grid wrappers');
 }
}
''')

    def test_full_admin_and_legacy_report_layouts_are_unchanged(self):
        self.run_js(r'''
for(page of ['home','lfo','online']){
 delete window.DASHBOARD_ACCESS;assert.equal(dashboardPartialLayout(),false);
 window.DASHBOARD_ACCESS={widgets:[...DASHBOARD_WIDGET_KEYS]};
 assert.equal(dashboardPartialLayout(),false);
 dashboardFitAccess();assert.equal(classes.has('dashboard-custom-layout'),false);
}
''')

    def test_limited_two_chart_and_tv_layouts_still_reflow(self):
        self.run_js(r'''
page='home';
for(const widgets of [['channel_sales','monthly_sales'],
 ['orders','units','target','achievement','difference','achievement_pct','channel_sales','monthly_sales',...online]]){
 window.DASHBOARD_ACCESS={widgets};assert.equal(dashboardPartialLayout(),true);
 dashboardFitAccess();assert.equal(classes.has('dashboard-custom-layout'),true);
 assert.ok(queries.includes('#page .home-overview'));
}
''')

    def test_online_layout_uses_only_its_own_grants(self):
        self.run_js(r'''
page='online';window.DASHBOARD_ACCESS={widgets:[...online]};
assert.equal(dashboardPartialLayout(),false,'denied primary widgets must not reflow Online Sales');
window.DASHBOARD_ACCESS.widgets=online.slice(1);
assert.equal(dashboardPartialLayout(),true,'a denied Online Sales panel remains a partial layout');
page='home';assert.equal(dashboardPartialLayout(),true);
''')

    def test_missing_primary_widget_still_uses_permission_layout(self):
        self.run_js(r'''
page='home';window.DASHBOARD_ACCESS={widgets:primary.filter(key=>key!=='executive_sales')};
assert.equal(dashboardPartialLayout(),true);
window.DASHBOARD_ACCESS={widgets:[]};assert.equal(dashboardPartialLayout(),true);
''')


if __name__=='__main__':unittest.main()
