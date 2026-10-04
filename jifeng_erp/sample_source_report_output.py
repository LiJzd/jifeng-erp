"""Fixed finalizers for the ten source reports and their native output formats.

Native row normalization, permissions, totals and formatting remain in use.
Only the small custom-field traversal and dispatch points need local versions:
an HTTP override cannot observe native internal calls to those helpers.
"""

import copy
import csv
import importlib
from io import StringIO
import json

import frappe

from jifeng_erp.sample_orders import _freeze
from jifeng_erp.sample_source_report_scripts import _native_module


_QUERY_SHA = "cb9a0c1c9e200483777b1708f2bd79173989dd689ab175f307af62e1141c7af5"
_DENIED = "无法完整读取报表来源，请核对权限后重新生成"
_CHANGED = "报表筛选、列或来源已变化，请重新生成"
_ROLLBACK_UNCONFIRMED = object()


def _artifacts():
    from jifeng_erp import sample_source_report_artifacts

    return sample_source_report_artifacts


def _require(value, message=_DENIED):
    if not value:
        frappe.throw(message, frappe.PermissionError)


def _native():
    return _native_module(
        "frappe.desk.query_report", _QUERY_SHA,
        ("get_report_result", "normalize_result", "get_column_as_dict", "get_filtered_data",
         "add_total_row", "translate_report_data", "format_fields", "build_xlsx_data",
         "valid_report_name", "validate_filters_permissions"),
    )


def _ordinary_native():
    return importlib.import_module("frappe.desk.query_report")


def generation_options(report, custom_columns=None, is_tree=False, parent_field=None):
    """The exact output inputs used by both a worker and its finalizer."""
    return {
        "saved_custom_columns": copy.deepcopy(report.get("custom_columns") or []),
        "custom_columns": copy.deepcopy(custom_columns or []),
        "is_tree": bool(is_tree),
        "parent_field": parent_field,
    }


def _custom_data(frame, columns, result):
    """Native traversal with one fixed, fully observed source-read service."""
    values = {}
    for column in columns:
        link_field = column.get("link_field")
        if not link_field:
            continue
        if isinstance(link_field, str):
            link_field = frappe._dict({"fieldname": link_field, "names": []})
        names = list({row.get(link_field.get("fieldname")) for row in result
                      if row.get(link_field.get("fieldname"))})
        if names:
            values[column.get("doctype"), column.get("fieldname")] = (
                _artifacts().record_extra_dependency(
                    frame, doctype=column.get("doctype"), field=column.get("fieldname"), names=names,
                )
            )
    return values


def _add_custom_column_data(frame, custom_columns, result):
    doctypes_with_conflicts = []
    for column in custom_columns:
        if len(column["fieldname"].split("-")) > 1:
            doctypes_with_conflicts.append(frappe.unscrub(column["fieldname"].split("-")[1]))
        column["fieldname"] = column["fieldname"].split("-")[0]
    pending = custom_columns
    while pending:
        data = _custom_data(frame, pending, result)
        unresolved = []
        for column in pending:
            key = column.get("doctype"), column.get("fieldname")
            if key not in data:
                unresolved.append(column)
                continue
            for row in result:
                link_field = column.get("link_field")
                if isinstance(link_field, str):
                    link_field = frappe._dict({"fieldname": link_field, "names": []})
                reference = row.get(link_field.get("fieldname"))
                if not reference:
                    continue
                if key[0] in doctypes_with_conflicts:
                    column["fieldname"] = column.get("id")
                row[column.get("fieldname")] = data.get(key).get(reference)
        if len(unresolved) == len(pending):
            break
        pending = unresolved
    return result


@frappe.read_only()
def generate_report_result(report, filters=None, user=None, custom_columns=None,
                           is_tree=False, parent_field=None, *, context=None):
    """Complete native payload inside the exact generation frame, before seal."""
    api = _artifacts()
    context = context or api.resolve_report(report.get("custom_report") or report.name)
    if context is None:
        return _ordinary_native().generate_report_result(
            report, filters, user, custom_columns, is_tree, parent_field,
        )
    native = _native()
    user = user or frappe.session.user
    _require(user == frappe.session.user)
    frame = api.current_frame()
    options = generation_options(report, custom_columns, is_tree, parent_field)
    if frame is None:
        with api.generation(context.requested_name, filters, artifact_kind="Execution",
                            output_options={"generation": options}):
            return generate_report_result(report, filters, user, custom_columns,
                                          is_tree, parent_field, context=context)
    _require(frame.requested_name == context.requested_name
             and report.name == context.canonical_name, _CHANGED)
    _require(_freeze(frame.output_options.get("generation")) == _freeze(options), _CHANGED)
    _require(_freeze(report.get("custom_columns") or [])
             == _freeze(context.report.get("custom_columns") or []), _CHANGED)
    effective_filters = json.loads(filters) if isinstance(filters, str) and filters else (filters or {})
    _require(_freeze(effective_filters) == _freeze(frame.filters), _CHANGED)

    # This is the pinned native generate_report_result pipeline, with its one
    # internal custom-field call replaced by the observed traversal above.
    # Native automation starts a separate DB writer thread after 15 seconds.
    # Fixed generators choose their prepared path before entering this frame.
    automation = report.get("disable_prepared_report_automation")
    report.disable_prepared_report_automation = True
    try:
        raw = native.get_report_result(report, effective_filters) or []
    finally:
        report.disable_prepared_report_automation = automation
    columns, result, message, chart, summary, skip_total = native.ljust_list(raw, 6)
    columns = [native.get_column_as_dict(col) for col in (columns or [])]
    original_names = [col["fieldname"] for col in columns]
    result = native.normalize_result(result, columns)
    if report.get("custom_columns"):
        columns = [copy.deepcopy(col) for col in report.custom_columns
                   if col.get("link_field") or col["fieldname"] in original_names]
    if custom_columns:
        for column in copy.deepcopy(custom_columns):
            columns.insert(column["insert_after_index"] + 1, column)
    extras = [col for col in columns if col["fieldname"] not in original_names]
    if extras:
        result = _add_custom_column_data(frame, extras, result)
    if result:
        result = native.get_filtered_data(report.ref_doctype, columns, result, user)
    has_total = native.cint(report.add_total_row) and result and not skip_total
    if has_total:
        result = native.add_total_row(result, columns, is_tree=is_tree, parent_field=parent_field)
    if isinstance(effective_filters, dict) and effective_filters.get("translate_data"):
        result = native.translate_report_data(result, has_total)
    payload = {
        "result": result, "columns": columns, "message": message, "chart": chart,
        "report_summary": summary, "skip_total_row": skip_total or 0, "status": None,
        "execution_time": frappe.cache.hget("report_execution_time", report.name) or 0,
    }
    if report.snapshot_report and report.doctype_to_sync:
        latest = frappe.db.get_all("DuckDB Sync", filters={
            "doc_type": report.doctype_to_sync[0].doc_type, "docstatus": 1,
        }, fields=["creation"], pluck="creation", order_by="creation desc", limit=1)
        if latest:
            payload.update(snapshot_report=True, snapshot_at=latest[0])
    return payload


@frappe.whitelist()
def run(report_name, filters=None, user=None, ignore_prepared_report=False, custom_columns=None,
        is_tree=False, parent_field=None, are_default_filters=True, js_filters=None):
    """Public signature remains native; actual user is always this session."""
    api = _artifacts()
    context = api.resolve_report(report_name)
    if context is None:
        return _ordinary_native().run(report_name, filters, user, ignore_prepared_report,
                                     custom_columns, is_tree, parent_field, are_default_filters, js_filters)
    native = _native()
    actor = frappe.session.user
    native.validate_filters_permissions(report_name, filters, actor, js_filters)
    report = context.report
    if native.sbool(are_default_filters) and report.get("custom_filters"):
        filters = copy.deepcopy(report.custom_filters)
    if report.prepared_report and not native.sbool(ignore_prepared_report) and not custom_columns:
        from jifeng_erp.sample_source_prepared_reports import get_prepared_report_result

        selected = json.loads(filters) if isinstance(filters, str) and filters else copy.deepcopy(filters or {})
        dn = selected.pop("prepared_report_name", None)
        payload = get_prepared_report_result(report, selected, dn or "", actor)
    else:
        custom_columns = frappe.parse_json(custom_columns) if custom_columns else None
        with api.generation(report_name, filters, artifact_kind="Execution", output_options={
            "generation": generation_options(report, custom_columns, is_tree, parent_field),
        }):
            payload = generate_report_result(report, filters, actor, custom_columns,
                                             is_tree, parent_field, context=context)
    payload["add_total_row"] = report.add_total_row and not payload.get("skip_total_row", False)
    if native.sbool(are_default_filters) and report.get("custom_filters"):
        payload["custom_filters"] = report.custom_filters
    return payload


def _csv_bytes(parsed):
    """Same native dict/list row mapping and UTF-8 CSV representation."""
    columns, rows = parsed.get("columns", []), parsed.get("result", [])
    if not columns or not rows:
        return None
    fields = [column.get("fieldname") for column in columns if column.get("fieldname")]
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fields} if isinstance(row, dict)
                        else {field: row[index] for index, field in enumerate(fields)})
    return buffer.getvalue().encode("utf-8")


@frappe.whitelist()
def enqueue_json_to_csv_conversion(prepared_report_name):
    from frappe.core.doctype.prepared_report import prepared_report as native

    doc = frappe.get_doc("Prepared Report", prepared_report_name)
    if _artifacts().resolve_report(doc.report_name) is None:
        return native.enqueue_json_to_csv_conversion(prepared_report_name)
    doc.check_permission("read")
    doc.get_prepared_data()  # Current authorization, not a long-lived worker proof.
    frappe.enqueue("jifeng_erp.sample_source_report_output.convert_json_to_csv", queue="long",
                   prepared_report_name=prepared_report_name, enqueue_after_commit=True)


def _rollback_error():
    try:
        frappe.db.rollback()
    except Exception:
        return _ROLLBACK_UNCONFIRMED
    return {"status": "Error", "message": _DENIED}


def convert_json_to_csv(prepared_report_name):
    result = _convert_json_to_csv(prepared_report_name)
    if result is _ROLLBACK_UNCONFIRMED:
        # Raise outside the sensitive body and its exception handler. Native RQ
        # must not enter its success/commit branch after uncertain cleanup.
        raise RuntimeError(_DENIED) from None
    return result


def _convert_json_to_csv(prepared_report_name):
    """Only this fixed worker can create a CSV bound to its sealed parent JSON."""
    try:
        api = _artifacts()
        doc = frappe.get_doc("Prepared Report", prepared_report_name)
        context = api.resolve_report(doc.report_name)
    except Exception:
        return _rollback_error()
    if context is None:
        from frappe.core.doctype.prepared_report.prepared_report import convert_json_to_csv as native

        return native(prepared_report_name)
    try:
        doc.check_permission("read")
        data, display_name = doc.get_prepared_data(with_file_name=True)
        parents = frappe.get_all("Jifeng Report Artifact", filters={
            "prepared_report": doc.name, "artifact_kind": "JSON", "state": "Sealed",
        }, pluck="name")
        _require(len(parents) == 1)
        api.check_artifact(parents[0])
        content = _csv_bytes(json.loads(data))
        if content is None:
            return {"status": "Empty"}
        with api.generation(context.requested_name, doc.filters, prepared_report=doc.name,
                            artifact_kind="CSV", source_artifact=parents[0],
                            output_options={"conversion": "native-json-to-csv"}) as frame:
            file_doc = api.new_pending_file(frame, payload=data, content=content,
                                           file_name=f"csv_{display_name[:-8]}.csv")
            file_doc.save(ignore_permissions=True)
            api.seal(frame, file_doc)
            frappe.get_doc({
                "doctype": "Notification Log", "subject": "Your CSV file is ready for download",
                "email_content": f'Click <a href="{file_doc.file_url}" target="_blank">here</a> to download the file.',
                "for_user": frappe.session.user, "type": "Alert", "document_type": "File",
                "document_name": file_doc.name, "link": file_doc.file_url,
            }).insert(ignore_permissions=True)
        return {"status": "Completed"}
    except Exception:
        # A fixed worker must not send result locals to the native RQ error logger.
        return _rollback_error()


_EXPORT_KEYS = (
    "report_name", "filters", "custom_columns", "file_format_type", "include_indentation",
    "include_filters", "visible_idx", "ignore_visible_idx", "include_hidden_columns",
    "applied_filters", "export_in_background",
)


def _export_parameters(parameters):
    # Persist only inputs the native formatter actually uses, never session,
    # csrf, arbitrary form fields or a client-provided receipt/proof.
    return frappe._dict({key: copy.deepcopy(parameters.get(key)) for key in _EXPORT_KEYS})


def _json_parent(payload):
    document = payload.get("doc")
    if not payload.get("prepared_report") or document is None:
        return None
    parents = frappe.get_all("Jifeng Report Artifact", filters={
        "prepared_report": document.name, "artifact_kind": "JSON", "state": "Sealed",
    }, pluck="name")
    _require(len(parents) == 1)
    _artifacts().check_artifact(parents[0])
    return parents[0]


def _format_export(parameters, csv_params, payload):
    """Native export transformation, preserving visible order and formats."""
    from frappe.desk.utils import get_csv_bytes

    native = _native()
    # Prepared DTOs contain a real Document. Only columns and rows are mutated
    # by native formatting; copying the document can copy request/DB state.
    data = frappe._dict(payload)
    data.columns = copy.deepcopy(payload.get("columns") or [])
    data.result = copy.deepcopy(payload.get("result") or [])
    name, format_type = parameters.report_name, parameters.file_format_type
    data.report_name = name
    data.filters = parameters.filters
    data.applied_filters = parameters.applied_filters
    if not data.columns:
        return None
    visible = parameters.visible_idx or []
    if isinstance(visible, str):
        visible = json.loads(visible)
    if visible and not native.sbool(parameters.ignore_visible_idx):
        rows = [data.result[index] for index in visible if 0 <= index < len(data.result)]
        if native.cint(data.get("add_total_row")):
            rows = native.add_total_row(rows, data.columns)
        data["result"] = rows
    native.format_fields(data, format_type)
    values, widths, styles = native.build_xlsx_data(
        data, include_indentation=parameters.include_indentation,
        include_filters=parameters.include_filters,
        include_hidden_columns=parameters.include_hidden_columns,
        build_styles=format_type == "Excel",
    )
    if format_type == "CSV":
        extension = "csv"
        content = get_csv_bytes([[native.handle_html(value) if isinstance(value, str) else value
                                  for value in row] for row in values], copy.deepcopy(csv_params))
    elif format_type == "Excel":
        extension = "xlsx"
        content = native.make_xlsx(values, name, column_widths=widths, styles=styles).getvalue()
    else:
        frappe.throw(title=frappe._("Unsupported file format: {0}").format(format_type),
                     msg=frappe._("Only CSV and Excel formats are supported for export"))
    if parameters.include_filters:
        for value in (data.filters or {}).values():
            suffix = ""
            if isinstance(value, list):
                suffix = "_" + ",".join(value)
            elif isinstance(value, str) and value not in {"Yes", "No"}:
                suffix = "_" + value
            if native.valid_report_name(name, suffix):
                name += suffix
    return name, extension, content


def _export_generation(parameters, csv_params, *, save_file=False):
    api = _artifacts()
    context = api.resolve_report(parameters.report_name)
    _require(context is not None)
    native = _native()
    native.validate_filters_permissions(parameters.report_name, parameters.filters, frappe.session.user)
    columns = frappe.parse_json(parameters.custom_columns or "[]")
    prepared = bool(context.report.prepared_report and not columns)
    parent, payload = None, None
    if prepared:
        # This payload may represent wider, historical filters. Its own sealed
        # JSON is the lineage root; current request filters do not replace it.
        payload = run(parameters.report_name, parameters.filters,
                      custom_columns=columns, are_default_filters=False)
        parent = _json_parent(payload)
    options = {
        "generation": generation_options(context.report, columns),
        "export": dict(parameters), "csv": copy.deepcopy(csv_params),
    }
    with api.generation(parameters.report_name, parameters.filters,
                        artifact_kind="Export" if save_file else "Execution",
                        source_artifact=parent, output_options=options) as frame:
        if payload is None:
            payload = generate_report_result(context.report, parameters.filters,
                                             custom_columns=columns, context=context)
            payload["add_total_row"] = (context.report.add_total_row
                                        and not payload.get("skip_total_row", False))
        formatted = _format_export(parameters, csv_params, payload)
        if formatted is None:
            return None
        name, extension, content = formatted
        file_doc = None
        if save_file:
            from frappe.desk.utils import create_exported_report_folder_if_not_exists

            create_exported_report_folder_if_not_exists()
            encoded = frappe.safe_encode(frappe.as_json(payload, indent=None, separators=(",", ":")))
            file_doc = api.new_pending_file(frame, payload=encoded, content=content,
                                           file_name=f"{name}.{extension}")
            file_doc.save(ignore_permissions=True)
            api.seal(frame, file_doc)
    # The Execution frame performs its final source check before bytes become
    # an HTTP response; private background files are already sealed here.
    return name, extension, content, file_doc


@frappe.whitelist()
def export_query():
    from frappe.desk.utils import pop_csv_params, provide_binary_file

    original = frappe._dict(frappe.local.form_dict)
    if _artifacts().resolve_report(original.report_name) is None:
        return _ordinary_native().export_query()
    native = _native()
    csv_params = pop_csv_params(original)
    native.clean_params(original)
    native.parse_json(original)
    parameters = _export_parameters(original)
    context = _artifacts().resolve_report(parameters.report_name)
    frappe.permissions.can_export(context.report.ref_doctype, raise_exception=True)
    if int(parameters.export_in_background or 0):
        email = frappe.get_cached_value("User", frappe.session.user, "email")
        frappe.enqueue("jifeng_erp.sample_source_report_output.run_export_query_job",
                       user_email=email, form_params=parameters, csv_params=csv_params,
                       queue="long", now=frappe.flags.in_test)
        frappe.msgprint(frappe._(
            "Your report is being generated in the background. You will receive an email on {0} "
            "with a download link once it is ready."
        ).format(email))
        return
    result = _export_generation(parameters, csv_params)
    if result is None:
        frappe.respond_as_web_page(frappe._("No data to export"),
                                  frappe._("You can try changing the filters of your report."))
        return
    name, extension, content, _file = result
    provide_binary_file(frappe._(name), extension, content)


def run_export_query_job(user_email, form_params, csv_params):
    result = _run_export_query_job(user_email, form_params, csv_params)
    if result is _ROLLBACK_UNCONFIRMED:
        raise RuntimeError(_DENIED) from None
    return result


def _run_export_query_job(user_email, form_params, csv_params):
    """Fixed recipient and final source check, followed by a private file link."""
    try:
        parameters = _export_parameters(form_params)
        context = _artifacts().resolve_report(parameters.report_name)
    except Exception:
        return _rollback_error()
    if context is None:
        return _ordinary_native().run_export_query_job(user_email, form_params, csv_params)
    try:
        email = frappe.get_cached_value("User", frappe.session.user, "email")
        _require(user_email == email and bool(email))
        context = _artifacts().resolve_report(parameters.report_name)
        frappe.permissions.can_export(context.report.ref_doctype, raise_exception=True)
        result = _export_generation(parameters, csv_params, save_file=True)
        if result is None:
            return {"status": "Empty"}
        name, _extension, _content, file_doc = result
        _artifacts().check_artifact(
            frappe.db.get_value("Jifeng Report Artifact", {"file": file_doc.name, "state": "Sealed"}, "name"),
            file_doc=file_doc,
        )
        retention = frappe.get_system_settings("delete_background_exported_reports_after") or 48
        url = frappe.utils.get_url(file_doc.get_url())
        frappe.sendmail(
            recipients=[email], subject=frappe._("Your exported report: {0}").format(name),
            message=frappe._("The report you requested has been generated.<br><br>"
                             "Click here to download:<br><a href='{0}'>{0}</a><br><br>"
                             "This link will expire in {1} hours.").format(url, retention), now=True,
        )
        return {"status": "Completed"}
    except Exception:
        return _rollback_error()


def before_job(method=None, kwargs=None, transaction_type=None):
    """Old native jobs cannot sign artifacts without the fixed worker frame."""
    kwargs = kwargs or {}
    names = {
        "frappe.core.doctype.prepared_report.prepared_report.generate_report": "prepared_report",
        "frappe.core.doctype.prepared_report.prepared_report.convert_json_to_csv": "prepared_report_name",
    }
    if method in names:
        target = kwargs.get(names[method])
        _require(isinstance(target, str) and target)
        doc = frappe.get_doc("Prepared Report", target)
        _require(_artifacts().resolve_report(doc.report_name) is None,
                 "报表权限规则已更新，请重新生成报表")
    elif method == "frappe.desk.query_report.run_export_query_job":
        parameters = kwargs.get("form_params") or {}
        _require(isinstance(parameters, dict))
        _require(_artifacts().resolve_report(parameters.get("report_name")) is None,
                 "报表权限规则已更新，请重新导出报表")
