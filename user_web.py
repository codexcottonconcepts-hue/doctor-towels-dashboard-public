"""Account administration with explicit, server-enforced access grants."""
import secrets
import time
from html import escape
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse

import auth
import dashboard_policy as dp


def esc(value):
    return escape(str(value), quote=True)


def csrf(request):
    return request.session.setdefault("users_csrf", secrets.token_urlsafe(32))


def manageable(actor, target):
    if not actor or dict(actor).get('disabled',False):
        return False
    if actor["is_admin"]:
        return True
    return bool(target and not target["is_admin"]
                and target["username"] != actor["username"]
                and auth.permissions_for(dict(target, disabled=0)) <= auth.permissions_for(actor)
                and dp.policy_is_subset(dp.policy_for(dict(target, disabled=0)), dp.policy_for(actor)))


def dashboard_access_from_form(form, actor, target=None):
    """A missing marker is an older form: preserve access, never restore All."""
    if 'dashboard_policy_version' not in form:
        return dp.policy_for(dict(target,disabled=0)) if target else new_dashboard_policy(actor)
    if form.getlist('dashboard_policy_version') != ['1']:
        raise ValueError('Invalid dashboard access form. Reload Users & access.')
    policy = {'version':1, **{key:form.getlist('dashboard_'+key) for key in dp.CATALOGS}, 'scopes':{}}
    for key in dp.SCOPE_LABELS:
        all_values = form.getlist('scope_all_'+key)
        if all_values and all_values != ['1']:
            raise ValueError('Invalid All data selection.')
        policy['scopes'][key] = None if all_values else form.getlist('scope_'+key)
    return dp.normalize_policy(policy)


def new_dashboard_policy(actor):
    """Start new dashboard users with the two charts requested by the owner."""
    policy = dp.policy_for(actor)
    preset = {'pages':['home'],'views':['overview'],'widgets':['channel_sales','monthly_sales'],
              'filters':['fy','month','brand','exec','customer','sub','state'],'drilldowns':[]}
    for key,values in preset.items():
        policy[key] = [value for value in values if value in policy[key]]
    return dp.normalize_policy(policy)


def dashboard_access_form(actor, selected, data):
    own = dp.policy_for(actor)
    options = dp.options_from_data(data,actor,selected)
    parts = ['<section class="dashboard-access"><h3>Dashboard view access</h3>'
             '<input type="hidden" name="dashboard_policy_version" value="1">'
             '<p>Choose the dashboard pages, sections, filters and breakup links this user may see. '
             'Dashboard View above is also required. An unchecked section is hidden.</p>'
             '<button type="button" class="sec" data-dashboard-preset>Only channel and monthly charts</button>'
             '<p class="muted">This preset enables Dashboard View and the two overview charts. Your allowed data selections stay as selected below.</p>']
    headings = {'pages':'Pages','views':'Views','widgets':'Cards, charts and tables','filters':'Filter controls','drilldowns':'Breakup links'}
    for key,catalog in dp.CATALOGS.items():
        fields = []
        for value,label in catalog.items():
            checked = ' checked' if value in selected[key] else ''
            disabled = ' disabled' if value not in own[key] else ''
            fields.append(f'<label class="dashboard-choice"><input type="checkbox" name="dashboard_{key}" value="{esc(value)}"{checked}{disabled}><span>{esc(label)}</span></label>')
        parts.append(f'<details class="dashboard-access-group" open><summary>{headings[key]}</summary>'
                     '<div class="dashboard-access-tools"><button type="button" class="sec" data-dash-check="all">Select available</button>'
                     '<button type="button" class="sec" data-dash-check="none">Clear</button>'
                     '<input type="search" data-dash-search placeholder="Find an option" aria-label="Search '+headings[key]+'"></div>'
                     '<div class="dashboard-choices">'+''.join(fields)+'</div></details>')
    parts.append('<h3>Allowed dashboard data</h3><p>All data includes future values. '
                 'Turn off All data to choose specific values. No selections means no access to that data. '
                 'Different groups narrow the selection together. Month access repeats in every financial year.</p>'
                 '<p class="muted">These data limits apply to the dashboard and its sales breakups. '
                 'Channel limits also restrict sales registers, new sales entries and dispatch edits. '
                 'These operations still require their separate permissions above. Other data limits apply to dashboard views only. '
                 'Hiding a card does not hide amounts also shown in another allowed chart.</p>')
    for key,label in dp.SCOPE_LABELS.items():
        selected_values = selected['scopes'][key]
        all_checked = ' checked' if selected_values is None else ''
        all_disabled = ' disabled' if own['scopes'][key] is not None else ''
        fields = []
        for value in options[key]:
            checked = ' checked' if selected_values is not None and value in selected_values else ''
            fields.append(f'<label class="dashboard-choice"><input type="checkbox" name="scope_{key}" value="{esc(value)}"{checked}><span>{esc(value)}</span></label>')
        parts.append(f'<details class="dashboard-access-group" data-scope-group="{key}" open><summary>{esc(label)}</summary>'
                     f'<label class="dashboard-all"><input type="checkbox" name="scope_all_{key}" value="1" data-dash-all{all_checked}{all_disabled}> All data (including future values)</label>'
                     f'<input type="search" data-dash-search placeholder="Find {esc(label.lower())}" aria-label="Search allowed {esc(label.lower())}">'
                     '<div class="dashboard-choices">'+(''.join(fields) or '<p class="muted">No permitted values are currently available.</p>')+'</div></details>')
    return ''.join(parts)+'</section>'


def permission_matrix(actor, selected):
    columns = ("view", "create", "edit", "delete", "access")
    rows = []
    for group in auth.PERMISSION_GROUPS:
        cells = []
        for action in columns:
            key = group.get(action)
            if not key:
                cells.append('<td class="muted">—</td>')
                continue
            allowed = auth.can(actor, key)
            checked = " checked" if key in selected else ""
            disabled = " disabled" if not allowed else ""
            label = esc(f'{group["label"]}: {action.title()}')
            cells.append(f'<td><input type="checkbox" name="permissions" value="{esc(key)}" aria-label="{label}"{checked}{disabled}></td>')
        rows.append(f'<tr><th scope="row">{esc(group["label"])}</th>{"".join(cells)}</tr>')
    return ('<div class="permission-scroll"><table class="permissions"><caption>Access permissions</caption>'
            '<thead><tr><th>Area</th><th>View</th><th>Create</th><th>Edit</th><th>Delete</th><th>Access</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="muted">Edit includes View for that section. New sales entries require Create plus Edit for '
            'Invoice &amp; customer, Sales classification, Location and Sales values. Dispatch details are optional. '
            'Delete removes an entire sales entry or monthly target, not an individual section. '
            'Export also requires permission to view the relevant register or report.</p>'
            '<p class="muted">Courier, weight &amp; transport: Edit allows the courier type, tracking number, weight and transport service on existing sales entries '
            'and includes View registers and Dispatch details View. It does not require Sales entries Edit. '
            'Stock supplied date: Edit independently allows that date to change and includes the same visibility. '
            'Select both permissions to allow dispatch and stock supplied date changes together. '
            'Select View for the other sales sections to show their register details without allowing changes. '
            'Leave Sales entries Edit and the section Edit boxes off for these specific fields. '
            'Broader dispatch changes require both Sales entries Edit and Dispatch details Edit.</p>'
            '<p class="muted">Dashboard and Target Reports grant access to their sales summaries independently '
            'of the entry sections. Leave these off when a user must not see sales summaries.</p>')


STYLE = '''<style>
.permission-scroll{overflow-x:auto}.permissions{width:100%;min-width:580px}
.permissions caption{text-align:left;font-size:17px;font-weight:700;padding:18px 0 8px}
.permissions td{text-align:center}.permissions th:first-child{text-align:left}
.permissions input[type=checkbox]{width:18px;height:18px;margin:0;accent-color:#c8102e}
.permissions td,.permissions th{padding:10px 8px;border-bottom:1px solid #e2e8f0}
.user-actions{display:flex;gap:12px;align-items:center;justify-content:flex-end;flex-wrap:wrap}
.user-actions form{margin:0}.user-actions button{margin:0}
#ru{width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;font:inherit}
.dashboard-access{margin:26px 0}.dashboard-access p{line-height:1.5}.dashboard-access-group{margin:10px 0;border:1px solid #dce2eb;border-radius:8px;padding:12px}
.dashboard-access-group summary{cursor:pointer;font-weight:600;margin-bottom:10px}.dashboard-choices{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;max-height:240px;overflow:auto;margin-top:10px}
.dashboard-choice,.dashboard-all{display:flex;align-items:flex-start;gap:8px;font-weight:400}.dashboard-choice input,.dashboard-all input{width:17px;height:17px;flex-shrink:0;margin:2px 0;accent-color:#c8102e}
.dashboard-choice[hidden]{display:none}.dashboard-choice span{overflow-wrap:anywhere}.dashboard-access-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.dashboard-access-tools button{margin:0;padding:6px 10px}.dashboard-access input[type=search]{max-width:360px;padding:8px 10px;margin-top:6px}
.dashboard-access-group[data-all=true] .dashboard-choices{opacity:.55}.dashboard-all{margin-bottom:10px}
</style>'''

DASHBOARD_FORM_SCRIPT = '''<script>
document.querySelectorAll('[data-dashboard-preset]').forEach(button=>button.addEventListener('click',()=>{
  const form=button.closest('form');
  const preset={pages:['home'],views:['overview'],widgets:['channel_sales','monthly_sales'],filters:['fy','month','brand','exec','customer','sub','state'],drilldowns:[]};
  form.querySelectorAll('input[name="permissions"]:not(:disabled)').forEach(box=>box.checked=box.value==='dashboard.view');
  for(const [key,values] of Object.entries(preset))form.querySelectorAll('input[name="dashboard_'+key+'"]:not(:disabled)').forEach(box=>box.checked=values.includes(box.value));
  const admin=form.querySelector('input[name="is_admin"]');if(admin)admin.checked=false;
}));
document.querySelectorAll('.dashboard-access-group').forEach(group=>{
  const all=group.querySelector('[data-dash-all]');
  const apply=()=>{if(!all)return;group.dataset.all=String(all.checked);group.querySelectorAll('.dashboard-choice input').forEach(box=>box.disabled=all.checked);};
  if(all){all.addEventListener('change',apply);apply();}
  group.querySelector('[data-dash-search]')?.addEventListener('input',event=>{const term=event.target.value.toLocaleLowerCase();group.querySelectorAll('.dashboard-choice').forEach(label=>label.hidden=!label.textContent.toLocaleLowerCase().includes(term));});
  group.querySelectorAll('[data-dash-check]').forEach(button=>button.addEventListener('click',()=>group.querySelectorAll('.dashboard-choice input:not(:disabled)').forEach(box=>box.checked=button.dataset.dashCheck==='all')));
});
</script>'''


def register(app, current_user, login_redirect, page, dashboard_data=None):
    def denied(user, message="You do not have permission to manage this account."):
        return HTMLResponse(page("Access denied", f'<div class="card"><h1>Access denied</h1><p>{esc(message)}</p></div>', user), status_code=403)

    def render(request, user, message="", error="", created=None, status=200):
        token = csrf(request)
        hidden = f'<input type="hidden" name="csrf" value="{esc(token)}">'
        selected_user = request.query_params.get("edit", "").strip().lower()
        target = auth.get_user(selected_user) if selected_user else None
        if selected_user and (not target or not manageable(user, target)):
            return denied(user)
        banner = f'<div class="err" role="alert">{esc(error)}</div>' if error else ""
        if message:
            banner += f'<div class="ok" role="status">{esc(message)}</div>'
        if created:
            banner += (f'<div class="ok">Password for <b>{esc(created[0])}</b>: <code>{esc(created[1])}</code>'
                       '<br>Copy it now. It is not shown again.</div>')
        rows = []
        for row in auth.all_users():
            editable = manageable(user, row)
            tag = "Administrator · full access" if row["is_admin"] else f'{len(auth.permissions_for(row))} permissions'
            last = time.strftime("%d %b %Y %H:%M", time.localtime(row["last_login"])) if row["last_login"] else "Never"
            actions = ""
            if editable:
                if not row["is_admin"]:
                    actions += f'<a href="/admin/users?edit={quote(row["username"], safe="")}#access-form">Edit access</a>'
                if row["username"] != user["username"]:
                    actions += (f'<form method="post" action="/admin/users/disable">{hidden}'
                                f'<input type="hidden" name="username" value="{esc(row["username"])}">'
                                f'<input type="hidden" name="disabled" value="{0 if row["disabled"] else 1}">'
                                f'<button class="sec">{"Enable" if row["disabled"] else "Disable"}</button></form>')
            rows.append(f'<tr><td><b>{esc(row["username"])}</b><br><small>{tag}{" · Disabled" if row["disabled"] else ""}</small></td>'
                        f'<td>{esc(last)}</td><td><div class="user-actions">{actions}</div></td></tr>')
        selected = auth.permissions_for(dict(target, disabled=0)) if target else {'dashboard.view'} & auth.permissions_for(user)
        dashboard_selected = dp.policy_for(dict(target,disabled=0)) if target else new_dashboard_policy(user)
        try:
            available_data = dashboard_data() if dashboard_data else {}
        except Exception:
            available_data = {}
            banner += '<p class="muted">Current dashboard choices are temporarily unavailable. Saved selections are retained.</p>'
        action = "/admin/users/permissions" if target else "/admin/users/create"
        if target:
            fields = f'<input type="hidden" name="username" value="{esc(target["username"])}"><p>Account: <b>{esc(target["username"])}</b></p>'
        else:
            fields = ('<div class="row"><div><label for="nu">Username</label><input id="nu" name="username" type="text" autocomplete="username" autocapitalize="none" required></div>'
                      '<div><label for="np">Password</label><input id="np" name="password" type="password" autocomplete="new-password" placeholder="Leave blank to generate"></div></div>')
        admin_toggle = ('<label class="chk"><input type="checkbox" name="is_admin" value="1">'
                        ' Administrator — full access to every area</label>') if user["is_admin"] and not target else ""
        body = (STYLE + f'<h1>Users &amp; access</h1><p class="muted">Choose the areas and actions each user can access. Changes apply on their next request.</p>{banner}'
                f'<div class="card permission-scroll"><table><thead><tr><th>User / access</th><th>Last signed in</th><th>Actions</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
                f'<div class="card" id="access-form"><h2>{"Edit user access" if target else "Add a user"}</h2><form method="post" action="{action}">{hidden}{fields}'
                f'{permission_matrix(user, selected)}{dashboard_access_form(user,dashboard_selected,available_data)}{admin_toggle}<button>{"Save access" if target else "Create user"}</button>'
                f'{" <a href=/admin/users>Cancel</a>" if target else ""}</form></div>'+DASHBOARD_FORM_SCRIPT)
        choices = [u for u in auth.all_users() if manageable(user, u)]
        options = ''.join(f'<option value="{esc(u["username"])}">{esc(u["username"])}</option>' for u in choices)
        if choices:
            body += (f'<div class="card"><h2>Reset a password</h2><form method="post" action="/admin/users/password">{hidden}'
                     f'<div class="row"><div><label for="ru">Username</label><select id="ru" name="username">{options}</select></div>'
                     '<div><label for="rp">New password</label><input id="rp" name="password" type="password" autocomplete="new-password" placeholder="Leave blank to generate"></div></div>'
                     '<button>Reset password</button></form></div>')
        return HTMLResponse(page("Users & access", body, user), status_code=status, headers={"Cache-Control": "no-store"})

    @app.get("/admin/users", response_class=HTMLResponse)
    def users(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not auth.can(user, "users.manage"):
            return denied(user)
        return render(request, user)

    async def mutate(request, action):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not auth.can(user, "users.manage"):
            return denied(user)
        # Checkbox scopes can legitimately contain thousands of customers.
        form = await request.form(max_files=0, max_fields=20000)
        token = request.session.get("users_csrf", "")
        if not token or not secrets.compare_digest(token.encode("utf-8"), str(form.get("csrf", "")).encode("utf-8")):
            return denied(user, "Your form session expired. Reload Users & access and try again.")
        username = str(form.get("username", "")).strip().lower()
        target = auth.get_user(username)
        if action != "create" and (not target or not manageable(user, target)):
            return denied(user)
        try:
            if action in ("create", "permissions"):
                permissions = auth.normalize_permissions(form.getlist("permissions"))
                if not permissions <= auth.permissions_for(user):
                    return denied(user, "You can only grant permissions you have yourself.")
                dashboard_access = dashboard_access_from_form(form,user,target if action=='permissions' else None)
                if not dp.policy_is_subset(dashboard_access,dp.policy_for(user)):
                    return denied(user, "You can only grant dashboard sections and data access that you have yourself.")
                if action == "permissions":
                    if target["is_admin"]:
                        return denied(user, "Administrators have full access. Edit permissions on a standard account.")
                    auth.set_permissions(username, permissions, dashboard_access=dashboard_access)
                    return render(request, user, message=f"Access updated for {username}.")
                is_admin = form.get("is_admin") == "1"
                if is_admin and not user["is_admin"]:
                    return denied(user, "Only an administrator can create another administrator.")
                password = str(form.get("password", "")).strip()
                generated = not password
                password = password or auth.generate_password()
                auth.create_user(username, password, is_admin=is_admin, permissions=permissions, dashboard_access=dashboard_access)
                return render(request, user, message=f"Created {username}.", created=(username, password) if generated else None)
            if action == "password":
                password = str(form.get("password", "")).strip()
                generated = not password
                password = password or auth.generate_password()
                auth.set_password(username, password)
                return render(request, user, message=f"Password reset for {username}.", created=(username, password) if generated else None)
            if action == "disable":
                disabled = form.get("disabled") == "1"
                if username == user["username"]:
                    raise ValueError("You cannot disable your own account.")
                if disabled and target["is_admin"] and not target["disabled"] and auth.admin_count() <= 1:
                    raise ValueError("The last active administrator cannot be disabled.")
                auth.set_disabled(username, disabled)
                return render(request, user, message=f'{username} {"disabled" if disabled else "enabled"}.')
        except ValueError as exc:
            return render(request, user, error=str(exc), status=400)

    @app.post("/admin/users/create")
    async def create(request: Request):
        return await mutate(request, "create")

    @app.post("/admin/users/permissions")
    async def permissions(request: Request):
        return await mutate(request, "permissions")

    @app.post("/admin/users/password")
    async def password(request: Request):
        return await mutate(request, "password")

    @app.post("/admin/users/disable")
    async def disable(request: Request):
        return await mutate(request, "disable")
