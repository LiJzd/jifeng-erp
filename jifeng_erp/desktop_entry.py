"""Keep the module's v16 navigation on the native Desk workspace."""

import frappe


WORKSPACE = "集沣生产协作"
LEGACY_PAGES = {"jifeng-modern", "jifeng-legacy"}


def repair_legacy_desktop_entry():
    """Update only public Jifeng sidebars, retaining surviving child identities.

    Workspace fixtures supply the native shortcuts. Personal sidebars and other
    apps' navigation remain owned by their users; repeated installation is a no-op.
    """
    if not frappe.db.exists("DocType", "Workspace Sidebar"):
        return False  # Frappe versions without the v16 desktop use Workspace.
    if not frappe.db.exists("Workspace", WORKSPACE):
        return False
    workspace = frappe.get_doc("Workspace", WORKSPACE)
    changed = False
    for name in (WORKSPACE, "Jifeng ERP"):
        if not frappe.db.exists("Workspace Sidebar", name):
            continue
        sidebar = frappe.get_doc("Workspace Sidebar", name)
        if sidebar.for_user or sidebar.app not in (None, "", "jifeng_erp"):
            continue
        if sidebar.module != "Jifeng ERP":
            continue
        if _update_sidebar(sidebar, workspace.shortcuts):
            sidebar.save()
            sidebar.add_comment("Info", "集沣导航改用白色原生工作区：移除双工作台入口，常用业务和二开功能直接进入原生页面，保留其余侧栏子行及个人配置。")
            changed = True
    if changed:
        frappe.clear_cache()
    return changed


def _update_sidebar(sidebar, shortcuts):
    first = next((row for row in sidebar.items if row.type == "Link"), None)
    changed = False
    if first and (
        (first.link_type == "Page" and first.link_to in LEGACY_PAGES)
        or (first.link_type == "Workspace" and first.link_to == "Jifeng ERP"
            and not frappe.db.exists("Workspace", "Jifeng ERP"))
    ):
        first.link_type, first.link_to = "Workspace", WORKSPACE
        first.label, first.route_options = "主页", ""
        changed = True
    for row in list(sidebar.items):
        if row.type == "Link" and row.link_type == "Page" and row.link_to in LEGACY_PAGES:
            sidebar.remove(row)
            changed = True
    existing = {(row.link_type, row.link_to) for row in sidebar.items if row.type == "Link"}
    added = []
    for shortcut in shortcuts:
        key = (shortcut.type, shortcut.link_to)
        if key in existing:
            continue
        added.append(sidebar.append("items", {
            "type": "Link", "label": shortcut.label,
            "link_type": shortcut.type, "link_to": shortcut.link_to,
        }))
        existing.add(key)
    by_target = {}
    for row in sidebar.items:
        if row.type == "Link":
            by_target.setdefault((row.link_type, row.link_to), row)
    managed = [by_target[(shortcut.type, shortcut.link_to)] for shortcut in shortcuts]
    managed_ids = {id(row) for row in managed}
    rows = [row for row in sidebar.items if id(row) not in managed_ids]
    home = next((i for i, row in enumerate(rows)
                 if row.type == "Link" and (row.link_type, row.link_to) == ("Workspace", WORKSPACE)), None)
    if home is None:
        home = next((i for i, row in enumerate(rows) if row.type == "Link"), -1)
    ordered = rows[:home + 1] + managed + rows[home + 1:]
    if added or [id(row) for row in ordered] != [id(row) for row in sidebar.items]:
        sidebar.set("items", ordered)
        # Document.set retains existing child idx; persist the new order explicitly.
        for index, row in enumerate(sidebar.items, 1):
            row.idx = index
        changed = True
    return changed
