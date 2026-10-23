"""Shared, per-user account navigation for the dashboard and entry screens."""
from html import escape


CSS = """
.account-profile{position:relative;flex:0 0 42px;font:14px/1.45 "Segoe UI",system-ui,sans-serif;color:#24334b;text-align:left}
.bar .account-profile{margin-left:auto}
.rhead{position:relative;padding-right:78px;min-height:60px}
.rhead>.account-profile{position:absolute;top:9px;right:18px}
.account-profile--floating{position:fixed;top:10px;right:18px;z-index:110}
.account-profile .profile-trigger{display:flex;align-items:center;justify-content:center;width:42px;height:42px;margin:0;padding:0;border:0;border-radius:50%;background:linear-gradient(135deg,#db1028,#9d0015);color:#fff;font:700 16px/1 "Segoe UI",sans-serif;box-shadow:0 5px 14px #b80b2126;cursor:pointer;list-style:none;user-select:none}
.account-profile .profile-trigger::-webkit-details-marker{display:none}
.account-profile .profile-trigger::marker{content:""}
.account-profile .profile-trigger:hover{background:#a60920}
.account-profile .profile-trigger:focus-visible,.account-profile .profile-link:focus-visible{outline:3px solid #4075ef;outline-offset:3px}
.account-profile[open] .profile-trigger{box-shadow:0 0 0 4px #fce8ec}
.account-profile .profile-panel{position:fixed;top:62px;right:18px;z-index:110;width:280px;max-width:calc(100vw - 24px);max-height:calc(100dvh - 76px);overflow-y:auto;overscroll-behavior:contain;background:#fff;border:1px solid #edf0f5;border-radius:17px;box-shadow:0 16px 48px #172c4924,0 2px 6px #172c4908;padding:0;color:#24334b}
.account-profile .profile-identity{display:flex;align-items:center;gap:12px;padding:18px 18px 16px;border-bottom:1px solid #edf0f5}
.account-profile .profile-avatar{display:flex;align-items:center;justify-content:center;flex:0 0 44px;width:44px;height:44px;border-radius:50%;background:linear-gradient(140deg,#2688ff,#6756f8);font-size:17px;font-weight:700;color:#fff}
.account-profile .profile-who{min-width:0}
.account-profile .profile-name{display:block;font-size:14px;line-height:1.4;font-weight:700;color:#24334b;overflow-wrap:anywhere;letter-spacing:0}
.account-profile .profile-role{display:block;font-size:12px;color:#7385a0;margin-top:2px}
.account-profile .profile-links{display:grid;gap:2px;padding:9px}
.account-profile .profile-link{display:flex;align-items:center;gap:11px;padding:10px 12px;min-height:40px;border-radius:9px;color:#34445c;text-decoration:none;font-size:13px;font-weight:500;line-height:1.4}
.account-profile .profile-link:hover{background:#f3f6fc;color:#1e4faa}
.account-profile .profile-icon{width:18px;height:18px;flex:0 0 18px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;color:#8493aa}
.account-profile .profile-signout{padding:7px 9px 9px;border-top:1px solid #edf0f5}
.account-profile .profile-signout .profile-link,.account-profile .profile-signout .profile-icon{color:#bd1230}
.account-profile .profile-signout .profile-link:hover{background:#fff0f3}
@media(max-width:820px){.rhead{padding-right:72px}.rhead>.account-profile{right:14px;top:10px}}
"""

ICONS = {
    '/': '<path d="m3 10 9-7 9 7v10H3z"/><path d="M9 20v-7h6v7"/>',
    '/entries': '<rect x="5" y="3" width="15" height="18" rx="2"/><path d="M9 8h7M9 12h7M9 16h5M3 7h3M3 12h3M3 17h3"/>',
    '/entries?view=new': '<path d="M14 3H5v18h14V8zM14 3v5h5M8 14h8M12 10v8"/>',
    '/targets/planner': '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4M8 14h3M8 17h6"/>',
    '/targets': '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    '/lfo/stores': '<path d="M3 10h18L19 3H5zM5 10v11h14V10M9 21v-7h6v7"/>',
    '/stores/new':'<path d="M3 10h18L19 3H5zM5 10v11h14V10M9 16h6M12 13v6"/>',
    '/reports/targets': '<path d="M4 3v18h17M8 16v-5M13 16V7M18 16v-8"/>',
    '/admin/users': '<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 5v2"/>',
    '/admin/sales-sync': '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
    '/logout': '<path d="M10 4H4v16h6M9 12h12m-5-5 5 5-5 5"/>',
}


SCRIPT = """<script>
(()=>{
  const profile=document.getElementById('account-profile');
  if(!profile)return;
  const trigger=profile.querySelector('summary'), panel=profile.querySelector('.profile-panel');
  function position(){
    const rect=trigger.getBoundingClientRect();
    const top=Math.max(12,Math.min(rect.bottom+10,window.innerHeight-120));
    panel.style.top=top+'px';
    panel.style.right=Math.max(12,window.innerWidth-rect.right)+'px';
    panel.style.maxHeight=Math.max(96,window.innerHeight-top-12)+'px';
  }
  function close(restore){profile.open=false;if(restore)trigger.focus();}
  profile.addEventListener('toggle',()=>{trigger.setAttribute('aria-expanded',String(profile.open));if(profile.open)position();});
  document.addEventListener('pointerdown',event=>{if(profile.open&&!profile.contains(event.target))close(false);});
  document.addEventListener('focusin',event=>{if(profile.open&&!profile.contains(event.target))close(false);});
  profile.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&profile.open){event.preventDefault();close(true);}
    if(event.key==='ArrowDown'&&event.target===trigger){event.preventDefault();profile.open=true;position();panel.querySelector('a')?.focus();}
  });
  window.addEventListener('resize',()=>{if(profile.open)position();});
  document.addEventListener('scroll',event=>{if(profile.open&&!panel.contains(event.target))close(false);},true);
})();
</script>"""


def render(username, is_admin, links):
    """Callers supply permission-filtered links; user data is always escaped."""
    name = escape(str(username), quote=True)
    initial = escape(str(username)[:1].upper(), quote=True)
    role = 'Administrator' if is_admin else 'User'

    def link(href, label):
        icon = f'<svg class="profile-icon" viewBox="0 0 24 24" aria-hidden="true">{ICONS.get(href, "")}</svg>'
        return f'<a class="profile-link" href="{escape(href, quote=True)}">{icon}<span>{escape(label)}</span></a>'

    navigation = ''.join(link(href, label) for href, label in links)
    return (f'<details class="account-profile" id="account-profile">'
            f'<summary class="profile-trigger" role="button" aria-label="User profile: {name}" aria-controls="profile-panel" aria-expanded="false">{initial}</summary>'
            f'<div class="profile-panel" id="profile-panel">'
            f'<div class="profile-identity"><span class="profile-avatar" aria-hidden="true">{initial}</span>'
            f'<div class="profile-who"><b class="profile-name">{name}</b><span class="profile-role">{role}</span></div></div>'
            f'<nav class="profile-links" aria-label="Account options">{navigation}</nav>'
            f'<div class="profile-signout">{link("/logout", "Sign out")}</div></div></details>{SCRIPT}')
