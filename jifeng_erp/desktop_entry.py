"""Repair the known stale v16 module entry without replacing site navigation."""

import frappe


def repair_legacy_desktop_entry():
    """The old module sidebar points at a Workspace that was renamed.

    Frappe chooses a desktop icon's route from its first sidebar Link. Keep all
    existing rows and site choices; repair only our exact missing old Workspace
    reference, through a native save with an explanatory audit comment.
    """
    if not frappe.db.exists("DocType", "Workspace Sidebar"):
        return False  # Frappe versions without the v16 desktop use Workspace.
    if not frappe.db.exists("Workspace Sidebar", "Jifeng ERP"):
        return False
    sidebar = frappe.get_doc("Workspace Sidebar", "Jifeng ERP")
    if sidebar.for_user or sidebar.app not in (None, "", "jifeng_erp"):
        return False
    if sidebar.module != "Jifeng ERP":
        return False
    first = next((row for row in sidebar.items if row.type == "Link"), None)
    if not first or (first.link_type, first.link_to) != ("Workspace", "Jifeng ERP"):
        return False
    if frappe.db.exists("Workspace", "Jifeng ERP"):
        return False  # A real site Workspace is an explicit choice, not stale.
    page = frappe.get_doc("Page", "jifeng-legacy")
    if page.module != "Jifeng ERP":
        frappe.throw("经典工作台入口与集沣模块不一致，请核对页面配置")
    first.link_type, first.link_to = "Page", "jifeng-legacy"
    first.label, first.icon, first.route_options = "经典业务工作台", "panel-top", ""
    sidebar.save()
    sidebar.add_comment("Info", "集沣桌面入口修复：旧 Jifeng ERP 工作区已不存在，将模块侧栏首项改为经典业务工作台；保留原子行身份及其余侧栏、图标、角色和个人配置。")
    frappe.clear_cache()
    return True
