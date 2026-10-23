"""The TV brand is a presentation variant, never an extra authorized page."""
import base64
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is required")
class SalesviewerBrandTests(unittest.TestCase):
    def run_js(self, checks):
        source = (ROOT / "dashboard_access.js").read_text(encoding="utf-8")
        prelude = """
const assert=require('node:assert/strict');
let identity='salesviewer',page='online',rendered=[],allowed=true,nextId=0;
const timers=new Map(),classes=new Set(),nodes={page:{dataset:{},replaceChildren(){this.cleared=true}},tip:null};
const document={hidden:false,body:{classList:{toggle(key,on){if(on)classes.add(key);else classes.delete(key)},remove(key){classes.delete(key)}}},
 querySelector(){return {textContent:identity}},getElementById(id){return nodes[id]||null}};
const window={DASHBOARD_ACCESS:null};
const TV_BRAND_IMAGE='data:image/png;base64,UE5H';
const setTimeout=(fn,delay)=>{const id=++nextId;timers.set(id,{fn,delay});return id};
const clearTimeout=id=>timers.delete(id);
function render(){rendered.push({page,brand:dashboardTvBrandVisible()});}
function tick(){const timer=timers.get(dashboardSlideTimer);timers.delete(dashboardSlideTimer);assert.equal(timer.delay,10000);timer.fn();}
"""
        setup = """
dashboardUsablePage=id=>allowed&&['home','online'].includes(id);
dashboardLiveActive=true;
"""
        result = subprocess.run([NODE, "-"], input=prelude + source + setup + checks,
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_initial_company_then_dashboard_online_and_company_again(self):
        self.run_js("""
dashboardTvPrepareSlide();assert.equal(page,'home');assert.equal(dashboardTvBrandVisible(),true);
assert.deepEqual(dashboardTvSlideOptions().map(s=>[s.page,s.variant]),[['home','brand'],['home',null],['online','combined']]);
dashboardTvSlideshow(true);tick();assert.equal(page,'home');assert.equal(dashboardTvBrandVisible(),false);
tick();assert.equal(page,'online');tick();assert.equal(page,'home');assert.equal(dashboardTvBrandVisible(),true);
assert.equal(DASHBOARD_LIVE_INTERVAL,20000);
assert.match(dashboardTvBrandMarkup(),/alt="Doctor Towels/);
assert.equal(dashboardTvShowSlide('home','brand'),true);
assert.equal(dashboardTvShowSlide('map'),false);
""")

    def test_data_refresh_does_not_reset_first_slide_or_timer(self):
        self.run_js("""
dashboardTvPrepareSlide();dashboardTvSlideshow(true);tick();
const existing=dashboardSlideTimer;dashboardTvPrepareSlide();dashboardTvSlideshow(true);
assert.equal(dashboardTvBrandVisible(),false);assert.equal(dashboardSlideTimer,existing);
tick();assert.equal(page,'online');
""")

    def test_regular_accounts_never_enter_brand_or_slideshow(self):
        self.run_js("""
identity='operator';page='home';dashboardLiveActive=false;dashboardTvPrepareSlide();
assert.equal(dashboardSlideStarted,false);assert.equal(dashboardTvBrandVisible(),false);
dashboardTvSlideshow(false);assert.equal(dashboardSlideTimer,null);
assert.equal(dashboardTvShowSlide('home','brand'),false);
""")

    def test_unconfirmed_access_hides_content_and_pauses_advance(self):
        self.run_js("""
dashboardTvPrepareSlide();dashboardTvSlideshow(true);classes.add('salesviewer-brand');
dashboardLiveClear();assert.equal(nodes.page.cleared,true);assert.equal(classes.has('salesviewer-brand'),false);
assert.equal(dashboardLiveNeedsRender,true);tick();assert.equal(rendered.length,0);
allowed=false;dashboardTvSlideshow(true);assert.equal(dashboardSlideTimer,null);
assert.equal(dashboardTvBrandVisible(),false);assert.equal(dashboardTvShowSlide('home','brand'),false);
""")

    def test_empty_sales_payload_still_allows_company_slide(self):
        self.run_js("""
const DATA={rows:[],months:[]};dashboardTvPrepareSlide();assert.equal(dashboardTvBrandVisible(),true);
dashboardTvSlideshow(true);tick();assert.equal(dashboardTvBrandVisible(),false);
""")

    def test_template_inline_image_and_viewport_styles(self):
        template = (ROOT / "template.html").read_text(encoding="utf-8")
        self.assertIn('const TV_BRAND_IMAGE = "/*__SALESVIEWER_BRAND__*/";', template)
        self.assertIn("if(dashboardTvBrandVisible())el.innerHTML=dashboardTvBrandMarkup();", template)
        self.assertIn("else el.innerHTML = dashboardMarkup(", template)
        self.assertIn(".salesviewer-brand .rhead{display:none!important}", template)
        self.assertIn(".salesviewer-brand .app{padding:0}", template)
        self.assertIn("object-fit:contain", template)
        self.assertIn(".tv-slide-outgoing{position:fixed", template)
        self.assertNotIn("translate3d(-6%,0,0)", template)

    def test_supplied_png_is_embedded_without_changes_or_asset_requests(self):
        import refresh
        expected = (ROOT / "salesviewer-brand.png").read_bytes()
        html = refresh.render_html({"rows": [], "months": [], "fy": 2026})
        prefix = 'const TV_BRAND_IMAGE = "data:image/png;base64,'
        encoded = html.split(prefix, 1)[1].split('";', 1)[0]
        self.assertEqual(base64.b64decode(encoded), expected)
        self.assertNotIn("/*__SALESVIEWER_BRAND__*/", html)


if __name__ == "__main__":
    unittest.main()
