"""Whole-screen TV push animation, including stale-picture cleanup."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is required")
class SalesviewerFlowTests(unittest.TestCase):
    def run_js(self, checks):
        prelude = r"""
const assert=require('node:assert/strict');
let identity='salesviewer',page='home',reduced=false,allowed=true,rendered=0;
let timeoutId=0;const timers=new Map(),animated=[],copies=[],stylesRead=[];
class Element{
 constructor(kind,id,children=[]){this.kind=kind;this.id=id;this.children=children;this.attributes={id};this.attrs={};this.styles={};this.dataset={};this.removed=false;
 this.style={setProperty:(k,v,p)=>this.styles[k]=[v,p]};}
 matches(){return ['app','report','body','vis','brand'].includes(this.kind)}
 cloneNode(){const clone=new Element(this.kind,this.id,this.children.map(c=>c.cloneNode(true)));copies.push(clone);return clone}
 querySelectorAll(){return this.children.flatMap(c=>[c,...c.querySelectorAll('*')])}
 removeAttribute(k){delete this.attributes[k];if(k==='id')this.id=''}
 setAttribute(k,v){this.attrs[k]=v}
 append(c){this.children.push(c)}
 remove(){this.removed=true}
 replaceChildren(){this.children=[];this.cleared=true}
 animate(frames,options){let resolve;const finished=new Promise(r=>resolve=r);const record={element:this,frames,options,finished,resolve,cancelled:false,cancel(){this.cancelled=true}};animated.push(record);return record}
}
const svg=new Element('svg','chart'),report=new Element('report','report',[new Element('body','page',[svg])]);
const app=new Element('app','root',[report]);
const body=new Element('document','body');body.classList={toggle(){},remove(){}};
const document={hidden:false,body,querySelector:s=>s==='.app'?app:{textContent:identity},
 getElementById:id=>id==='page'?report.children[0]:null,createElement:()=>new Element('frame','')};
const window={DASHBOARD_ACCESS:null,matchMedia:()=>({matches:reduced}),getComputedStyle:e=>{
 stylesRead.push(e.kind);const style=['width','height','display'];style.getPropertyValue=k=>({width:'1536px',height:'730px',display:'flex'})[k];style.backgroundColor='rgb(0, 0, 0)';return style}};
const setTimeout=(fn,delay)=>{const id=++timeoutId;timers.set(id,{fn,delay});return id},clearTimeout=id=>timers.delete(id);
function render(){dashboardTvBeforeRender();rendered++;}
const TV_BRAND_IMAGE='data:image/png;base64,UE5H';
"""
        source = (ROOT / "dashboard_access.js").read_text(encoding="utf-8")
        setup = "dashboardUsablePage=id=>allowed&&['home','online'].includes(id);dashboardLiveActive=true;"
        result = subprocess.run([NODE, "-"], input=prelude + source + setup + checks,
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_two_full_panels_push_right_to_left_without_fade(self):
        self.run_js("""
dashboardTvAdvance({page:'home',variant:null});
assert.equal(rendered,1);assert.equal(animated.length,2);
assert.deepEqual(animated[0].frames,[{transform:'translate3d(0,0,0)'},{transform:'translate3d(-100%,0,0)'}]);
assert.deepEqual(animated[1].frames,[{transform:'translate3d(100%,0,0)'},{transform:'translate3d(0,0,0)'}]);
for(const a of animated){assert.equal(a.options.duration,1000);assert.equal(a.options.easing,'cubic-bezier(.65,0,.35,1)');assert.equal(JSON.stringify(a.frames).includes('opacity'),false)}
assert.equal(DASHBOARD_SLIDE_INTERVAL,10000);assert.equal(DASHBOARD_LIVE_INTERVAL,20000);
const frame=dashboardSlideOutgoing;assert.equal(frame.inert,true);assert.equal(frame.attrs['aria-hidden'],'true');
assert.equal(frame.style.backgroundColor,'rgb(0, 0, 0)');assert.ok(copies.every(c=>!c.id));
assert.equal(stylesRead.includes('svg'),false);assert.deepEqual(copies.find(c=>c.kind==='app').styles.width,['1536px','important']);
""")

    def test_access_failure_clears_old_picture_and_cancels_both_animations(self):
        self.run_js("""
dashboardTvAdvance({page:'online',variant:'combined'});const frame=dashboardSlideOutgoing;
dashboardLiveClear();assert.equal(frame.removed,true);assert.equal(dashboardSlideOutgoing,null);
assert.equal(dashboardSlideAnimations.length,0);assert.ok(animated.every(a=>a.cancelled));assert.equal(dashboardLiveNeedsRender,true);
allowed=false;assert.equal(dashboardTvShowSlide('home','brand'),false);
""")

    def test_poll_render_reentry_resize_and_stop_release_old_dom(self):
        self.run_js("""
dashboardTvAdvance({page:'home',variant:null});const first=dashboardSlideOutgoing;
dashboardTvBeforeRender();assert.equal(first.removed,true);assert.ok(animated.every(a=>a.cancelled));
dashboardTvAdvance({page:'online',variant:'combined'});const second=dashboardSlideOutgoing;
dashboardTvAdvance({page:'home',variant:'brand'});assert.equal(second.removed,true);
const third=dashboardSlideOutgoing;dashboardTvSlideshowStop();assert.equal(third.removed,true);assert.equal(dashboardSlideOutgoing,null);
""")

    def test_reduced_motion_switches_instantly_without_snapshot(self):
        self.run_js("""
reduced=true;dashboardTvAdvance({page:'online',variant:'combined'});
assert.equal(rendered,1);assert.equal(page,'online');assert.equal(animated.length,0);assert.equal(copies.length,0);
assert.equal(dashboardSlideOutgoing,null);
""")

    def test_non_tv_and_denied_access_never_create_transition(self):
        self.run_js("""
identity='operator';assert.equal(dashboardTvShowSlide('home'),false);dashboardTvCaptureSlide();assert.equal(copies.length,0);
identity='salesviewer';allowed=false;assert.equal(dashboardTvShowSlide('home','brand'),false);assert.equal(animated.length,0);
""")

    def test_late_animation_completion_cannot_remove_new_transition(self):
        self.run_js("""
(async()=>{
 dashboardTvAdvance({page:'home',variant:null});const previous=animated.slice();
 dashboardTvAdvance({page:'online',variant:'combined'});const active=dashboardSlideOutgoing;
 previous.forEach(a=>a.resolve());await Promise.resolve();await Promise.resolve();
 assert.equal(dashboardSlideOutgoing,active);assert.equal(active.removed,false);
 animated.slice(2).forEach(a=>a.resolve());await Promise.resolve();await Promise.resolve();
 assert.equal(active.removed,true);assert.equal(dashboardSlideOutgoing,null);
})().catch(error=>{console.error(error);process.exitCode=1});
""")

    def test_unchanged_twenty_second_poll_does_not_interrupt_flow(self):
        self.run_js("""
(async()=>{
 dashboardTvAdvance({page:'online',variant:'combined'});const active=dashboardSlideOutgoing;
 dashboardTvSlideshow(true);const slideTimer=dashboardSlideTimer;
 dashboardTvSlideshow(true);assert.equal(dashboardSlideTimer,slideTimer);
 window.location={origin:'http://localhost'};
 const snapshot={identity:'salesviewer',access:'same-policy',json:'same-data',data:{rows:[]}};
 dashboardLiveSource=snapshot;dashboardLiveSnapshot=()=>snapshot;
 globalThis.DOMParser=class{parseFromString(){return {}}};
 globalThis.fetch=async()=>({url:'http://localhost/',status:200,ok:true,
   headers:{get:()=> 'text/html'},text:async()=>'<html>same authorized payload</html>'});
 await dashboardLivePoll();
 assert.equal(dashboardSlideOutgoing,active);assert.equal(active.removed,false);
 assert.ok(animated.every(a=>!a.cancelled));assert.equal(rendered,1);
 assert.equal(timers.get(dashboardLiveTimer).delay,20000);
})().catch(error=>{console.error(error);process.exitCode=1});
""")

    def test_viewport_wrapper_is_inert_and_render_cleanup_is_wired(self):
        template = (ROOT / "template.html").read_text(encoding="utf-8")
        self.assertIn(".tv-slide-outgoing{position:fixed;inset:0;width:100vw;height:100vh", template)
        self.assertIn("pointer-events:none", template)
        self.assertIn("function render(){\n  dashboardTvBeforeRender();", template)
        self.assertNotIn("tv-slide-in-a", template)
        self.assertNotIn("data-tv-transition", template)


if __name__ == "__main__":
    unittest.main()
