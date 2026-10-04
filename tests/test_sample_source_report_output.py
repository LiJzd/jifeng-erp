"""Output-boundary regression models, not native DB/queue/file evidence."""

from contextlib import contextmanager
import copy
import importlib.util
from io import StringIO
import csv
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


def freeze(value):
    if isinstance(value, dict):
        return tuple((key, freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


class TestSampleSourceReportOutput(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe._dict = Row
        self.frappe._ = lambda value: value
        self.frappe.unscrub = lambda value: value.title().replace("_", " ")
        self.frappe.session = Row(user="actor")
        self.frappe.PermissionError = PermissionError
        self.frappe.throw = Mock(side_effect=lambda message=None, error=ValueError, **kw: (_ for _ in ()).throw(error(message)))
        self.frappe.read_only = lambda: lambda function: function
        self.frappe.whitelist = lambda: lambda function: function
        self.frappe.parse_json = lambda value: __import__("json").loads(value) if isinstance(value, str) else value
        self.frappe.cache = Row(hget=Mock(return_value=0))
        self.frappe.db = Row(rollback=Mock())
        self.frappe.get_all = Mock(return_value=["JSON-1"])
        self.frappe.enqueue = Mock()
        self.orders = types.ModuleType("jifeng_erp.sample_orders")
        self.orders._freeze = freeze
        self.scripts = types.ModuleType("jifeng_erp.sample_source_report_scripts")
        self.scripts._native_module = Mock()
        self.app = types.ModuleType("jifeng_erp")
        self.app.__path__ = [str(ROOT / "jifeng_erp")]
        self.modules = patch.dict(sys.modules, {
            "frappe": self.frappe, "jifeng_erp": self.app,
            "jifeng_erp.sample_orders": self.orders,
            "jifeng_erp.sample_source_report_scripts": self.scripts,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        name = "jifeng_erp.sample_source_report_output"
        spec = importlib.util.spec_from_file_location(name, ROOT / "jifeng_erp/sample_source_report_output.py")
        self.subject = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.subject
        self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(self.subject)
        self.report = Row(name="Production Analytics", ref_doctype="Work Order", custom_columns=[],
                          add_total_row=1, snapshot_report=0, prepared_report=0)
        self.context = Row(requested_name=self.report.name, canonical_name=self.report.name, report=self.report)
        self.frame = Row(requested_name=self.report.name, filters={"company": "JF"},
                         output_options={"generation": self.subject.generation_options(self.report)})
        self.api = Row(resolve_report=Mock(return_value=self.context), current_frame=lambda: self.frame,
                       record_extra_dependency=Mock(return_value={}), check_artifact=Mock(),
                       generation=Mock(), new_pending_file=Mock(), seal=Mock())
        self.subject._artifacts = lambda: self.api
        self.columns = [{"fieldname": "work_order", "fieldtype": "Link", "options": "Work Order"},
                        {"fieldname": "qty", "fieldtype": "Float"}]
        self.chart = {"data": {"values": [7]}}
        self.native = Row(
            get_report_result=Mock(return_value=(copy.deepcopy(self.columns), [["WO-1", 7]], "message", self.chart,
                                                [{"value": 7}], False)),
            ljust_list=lambda value, length: [*value, *([None] * (length - len(value)))],
            get_column_as_dict=lambda column: copy.deepcopy(column),
            normalize_result=lambda rows, columns: [dict(zip([c["fieldname"] for c in columns], row)) for row in rows],
            get_filtered_data=Mock(side_effect=lambda _dt, _cols, rows, _user: rows),
            add_total_row=Mock(side_effect=lambda rows, _cols, **kw: [*rows, {"work_order": "Total", "qty": sum(r["qty"] for r in rows)}]),
            translate_report_data=Mock(side_effect=lambda rows, _total: rows),
            cint=lambda value: int(value or 0), sbool=lambda value: value not in (None, False, 0, "0", "false"),
            validate_filters_permissions=Mock(), run=Mock(return_value={"native": True}),
        )
        self.subject._native = lambda: self.native
        self.subject._ordinary_native = lambda: self.native

    def generate(self, **kwargs):
        return self.subject.generate_report_result(self.report, {"company": "JF"}, context=self.context, **kwargs)

    def test_preserves_message_chart_summary_and_native_total(self):
        result = self.generate()
        self.assertEqual(result["result"], [{"work_order": "WO-1", "qty": 7}, {"work_order": "Total", "qty": 7}])
        self.assertIs(result["chart"], self.chart)
        self.assertEqual(result["message"], "message")
        self.assertEqual(result["report_summary"], [{"value": 7}])

    def test_wrong_actual_user_cannot_reuse_generation(self):
        with self.assertRaises(PermissionError):
            self.generate(user="other")
        self.native.get_report_result.assert_not_called()

    def test_scoped_body_cannot_start_native_auto_prepared_thread_and_restores_flag(self):
        original = self.native.get_report_result.return_value
        for failed in (False, True):
            self.report.disable_prepared_report_automation = False

            def body(report, _filters):
                self.assertIs(report.disable_prepared_report_automation, True)
                if failed:
                    raise RuntimeError("native body failed")
                return original

            self.native.get_report_result.side_effect = body
            if failed:
                with self.assertRaises(RuntimeError):
                    self.generate()
            else:
                self.generate()
            self.assertIs(self.report.disable_prepared_report_automation, False)

    def test_wrong_requested_or_canonical_report_denied_before_body(self):
        self.frame.requested_name = "another custom report"
        with self.assertRaises(PermissionError):
            self.generate()
        self.native.get_report_result.assert_not_called()

    def test_unbound_unsaved_column_denied_before_body(self):
        with self.assertRaises(PermissionError):
            self.generate(custom_columns=[{"fieldname": "secret", "insert_after_index": 0}])
        self.native.get_report_result.assert_not_called()

    def test_input_filter_not_frame_filter_denied(self):
        with self.assertRaises(PermissionError):
            self.subject.generate_report_result(self.report, {"company": "OTHER"}, context=self.context)
        self.native.get_report_result.assert_not_called()

    def test_saved_column_cannot_be_replaced_after_context_resolution(self):
        changed = copy.deepcopy(self.report)
        changed.custom_columns = [{"fieldname": "secret", "link_field": "work_order", "doctype": "Work Order"}]
        self.frame.output_options["generation"] = self.subject.generation_options(changed)
        with self.assertRaises(PermissionError):
            self.subject.generate_report_result(changed, {"company": "JF"}, context=self.context)

    def test_custom_chain_reads_every_requested_identity_via_observer(self):
        columns = [
            {"fieldname": "customer", "doctype": "Work Order", "link_field": "work_order"},
            {"fieldname": "customer_name", "doctype": "Customer", "link_field": "customer"},
        ]
        rows = [{"work_order": "WO-1"}, {"work_order": "WO-2"}, {"work_order": None}]

        def read(_frame, *, doctype, field, names):
            if (doctype, field) == ("Work Order", "customer"):
                return {name: "C1" for name in names}
            return {"C1": "name"}

        self.api.record_extra_dependency.side_effect = read
        result = self.subject._add_custom_column_data(self.frame, columns, rows)
        self.assertEqual(result[:2], [
            {"work_order": "WO-1", "customer": "C1", "customer_name": "name"},
            {"work_order": "WO-2", "customer": "C1", "customer_name": "name"},
        ])
        calls = self.api.record_extra_dependency.call_args_list
        self.assertEqual(set(calls[0].kwargs["names"]), {"WO-1", "WO-2"})
        self.assertEqual(calls[1].kwargs["names"], ["C1"])

    def test_denied_extra_dependency_rejects_entire_payload(self):
        extra = {"fieldname": "customer_name", "doctype": "Work Order", "link_field": "work_order", "insert_after_index": 0}
        self.frame.output_options["generation"] = self.subject.generation_options(self.report, [extra])
        self.api.record_extra_dependency.side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError):
            self.generate(custom_columns=[extra])
        self.native.add_total_row.assert_not_called()

    def test_unresolved_custom_chain_terminates_and_preserves_native_empty_values(self):
        rows = [{"work_order": "WO-1"}]
        self.subject._add_custom_column_data(self.frame, [{"fieldname": "missing", "link_field": "absent", "doctype": "Item"}], rows)
        self.assertEqual(rows, [{"work_order": "WO-1"}])
        self.api.record_extra_dependency.assert_not_called()

    def test_conflicting_custom_suffix_preserves_native_id_and_str_link(self):
        self.api.record_extra_dependency.return_value = {"WO-1": "C1"}
        columns = [{"fieldname": "customer-work_order", "id": "customer-work_order", "doctype": "Work Order", "link_field": "work_order"}]
        rows = self.subject._add_custom_column_data(self.frame, columns, [{"work_order": "WO-1"}])
        self.assertEqual(rows[0]["customer-work_order"], "C1")

    def test_ordinary_run_does_not_require_receipt_or_pinned_module(self):
        self.api.resolve_report.return_value = None
        self.subject._native = Mock(side_effect=AssertionError("ordinary must not pin"))
        result = self.subject.run("ordinary", {"x": 1}, user="backward-compatible")
        self.assertEqual(result, {"native": True})
        self.api.generation.assert_not_called()

    def test_prepared_selected_dn_uses_persisted_dto_service_not_execution_frame(self):
        self.report.prepared_report = 1
        prepared = types.ModuleType("jifeng_erp.sample_source_prepared_reports")
        prepared.get_prepared_report_result = Mock(return_value={"prepared_report": True, "doc": Row(name="PR")})
        with patch.dict(sys.modules, {prepared.__name__: prepared}):
            filters = {"company": "JF", "prepared_report_name": "PR"}
            self.subject.run(self.report.name, filters, user="spoof")
        prepared.get_prepared_report_result.assert_called_once_with(self.report, {"company": "JF"}, "PR", "actor")
        self.assertEqual(filters["prepared_report_name"], "PR")
        self.api.generation.assert_not_called()

    def test_export_parameters_do_not_persist_credentials_or_client_proof(self):
        params = self.subject._export_parameters({"report_name": "R", "filters": {},
                                                 "csrf_token": "secret", "proof": True, "sid": "private"})
        self.assertNotIn("csrf_token", params)
        self.assertNotIn("proof", params)
        self.assertNotIn("sid", params)

    def test_csv_native_dict_and_list_mapping_and_quotes(self):
        columns = [{"fieldname": "code"}, {"fieldname": "text"}]
        for rows in ([{"code": "001", "text": 'x,"y"\nline', "unused": "secret"}],
                     [["001", 'x,"y"\nline']]):
            content = self.subject._csv_bytes({"columns": columns, "result": rows})
            self.assertEqual(list(csv.reader(StringIO(content.decode()))), [["code", "text"], ["001", 'x,"y"\nline']])
            self.assertNotIn(b"secret", content)

    def test_csv_empty_does_not_create_artifact(self):
        self.assertIsNone(self.subject._csv_bytes({"columns": [], "result": [[1]]}))

    def test_old_native_scoped_job_cannot_sign_new_output(self):
        self.frappe.get_doc = Mock(return_value=Row(report_name=self.report.name))
        with self.assertRaises(PermissionError):
            self.subject.before_job("frappe.core.doctype.prepared_report.prepared_report.generate_report",
                                    {"prepared_report": "PR"})
        self.api.generation.assert_not_called()

    def test_old_native_ordinary_job_keeps_native_path(self):
        self.api.resolve_report.return_value = None
        self.frappe.get_doc = Mock(return_value=Row(report_name="ordinary"))
        self.subject.before_job("frappe.core.doctype.prepared_report.prepared_report.convert_json_to_csv",
                                {"prepared_report_name": "PR"})

    def test_foreground_response_happens_only_after_generation_exit(self):
        events = []

        @contextmanager
        def generation(*args, **kwargs):
            events.append("begin")
            yield self.frame
            events.append("final-source-check")

        self.api.generation = generation
        self.subject.generate_report_result = Mock(return_value={"columns": self.columns, "result": []})
        self.subject._format_export = Mock(side_effect=lambda *_args: ("R", "csv", b"bytes"))
        params = self.subject._export_parameters({"report_name": self.report.name, "filters": {"company": "JF"}})
        value = self.subject._export_generation(params, {})
        events.append("response")
        self.assertEqual(events, ["begin", "final-source-check", "response"])
        self.assertEqual(value[:3], ("R", "csv", b"bytes"))

    def test_cached_export_keeps_actual_json_parent_even_with_narrow_request(self):
        self.report.prepared_report = 1
        self.subject.run = Mock(return_value={"prepared_report": True, "doc": Row(name="WIDE-PR"),
                                              "columns": self.columns, "result": []})
        self.subject._format_export = Mock(return_value=("R", "csv", b"bytes"))
        calls = []

        @contextmanager
        def generation(*args, **kwargs):
            calls.append(kwargs)
            yield self.frame

        self.api.generation = generation
        params = self.subject._export_parameters({"report_name": self.report.name, "filters": {"company": "JF", "customer": "narrow"}})
        self.subject._export_generation(params, {})
        self.assertEqual(calls[0]["source_artifact"], "JSON-1")
        self.api.check_artifact.assert_called_once_with("JSON-1")

    def test_export_formats_visible_order_without_copying_prepared_document(self):
        class PreparedDocument:
            def __deepcopy__(self, memo):
                raise AssertionError("Document must not be copied by the formatter")

        utils = types.ModuleType("frappe.desk.utils")
        utils.get_csv_bytes = lambda data, _params: repr(data).encode()
        self.native.format_fields = Mock()
        self.native.handle_html = lambda value: value
        self.native.valid_report_name = lambda *_args: True
        self.native.build_xlsx_data = Mock(side_effect=lambda data, **kw: (
            [[row["work_order"], row["qty"]] for row in data.result], [], []))
        payload = {"doc": PreparedDocument(), "columns": copy.deepcopy(self.columns),
                   "result": [{"work_order": "first", "qty": 1}, {"work_order": "second", "qty": 2}],
                   "add_total_row": 1}
        before = copy.deepcopy(payload["result"])
        parameters = self.subject._export_parameters({
            "report_name": self.report.name, "file_format_type": "CSV", "filters": {},
            "visible_idx": [1, 0, 1, 99],
        })
        with patch.dict(sys.modules, {utils.__name__: utils}):
            value = self.subject._format_export(parameters, {}, payload)
        self.assertEqual(value[2], b"[['second', 2], ['first', 1], ['second', 2], ['Total', 5]]")
        self.assertEqual(payload["result"], before)

    def test_visible_export_respects_native_skip_total_result(self):
        utils = types.ModuleType("frappe.desk.utils")
        utils.get_csv_bytes = lambda data, _params: repr(data).encode()
        self.native.format_fields = Mock()
        self.native.handle_html = lambda value: value
        self.native.valid_report_name = lambda *_args: True
        self.native.build_xlsx_data = Mock(return_value=([], [], []))
        parameters = self.subject._export_parameters({
            "report_name": self.report.name, "file_format_type": "CSV", "filters": {},
            "visible_idx": [0],
        })
        with patch.dict(sys.modules, {utils.__name__: utils}):
            self.subject._format_export(parameters, {}, {"columns": self.columns,
                "result": [{"work_order": "first", "qty": 1}], "add_total_row": False})
        self.native.add_total_row.assert_not_called()

    def test_source_change_at_exit_cannot_publish_binary_response(self):
        @contextmanager
        def changed(*_args, **_kwargs):
            yield self.frame
            raise PermissionError("source changed")

        utils = types.ModuleType("frappe.desk.utils")
        utils.pop_csv_params = lambda _data: {}
        utils.provide_binary_file = Mock()
        self.api.generation = changed
        self.frappe.local = Row(form_dict=Row(report_name=self.report.name, filters={"company": "JF"}))
        self.frappe.permissions = Row(can_export=Mock())
        self.native.clean_params = Mock()
        self.native.parse_json = Mock()
        self.subject.generate_report_result = Mock(return_value={"columns": self.columns, "result": []})
        self.subject._format_export = Mock(return_value=("R", "csv", b"bytes"))
        with patch.dict(sys.modules, {utils.__name__: utils}), self.assertRaises(PermissionError):
            self.subject.export_query()
        utils.provide_binary_file.assert_not_called()
        self.api.new_pending_file.assert_not_called()

    def test_uncertain_worker_rollback_does_not_reach_success_commit_or_leak_body(self):
        for worker, body, arguments in (
            (self.subject.convert_json_to_csv, "_convert_json_to_csv", ("PR",)),
            (self.subject.run_export_query_job, "_run_export_query_job", ("actor@example.invalid", {}, {})),
        ):
            committed = False

            def sensitive_body(*_args):
                private_payload = "fixture-sensitive-payload"
                try:
                    raise PermissionError(private_payload)
                except Exception:
                    return self.subject._rollback_error()

            self.frappe.db.rollback.side_effect = RuntimeError("cleanup unknown")
            with patch.object(self.subject, body, sensitive_body):
                try:
                    worker(*arguments)
                except RuntimeError as error:
                    self.assertEqual(str(error), self.subject._DENIED)
                    self.assertIsNone(error.__context__)
                    frames = []
                    trace = error.__traceback__
                    while trace:
                        frames.append(trace.tb_frame.f_code.co_name)
                        trace = trace.tb_next
                    self.assertNotIn("sensitive_body", frames)
                else:
                    committed = True
            self.assertFalse(committed)

    def test_worker_classification_denial_rolls_back_before_returning_generic_error(self):
        self.frappe.get_doc = Mock(return_value=Row(report_name=self.report.name, filters="private-fixture"))
        workers = (
            (self.subject.convert_json_to_csv, ("PR",)),
            (self.subject.run_export_query_job, ("actor@example.invalid", {"report_name": self.report.name}, {})),
        )
        for worker, arguments in workers:
            with self.subTest(worker=worker.__name__):
                self.frappe.db.rollback.reset_mock()
                self.api.resolve_report.side_effect = PermissionError("private classification context")
                self.assertEqual(worker(*arguments), {"status": "Error", "message": self.subject._DENIED})
                self.frappe.db.rollback.assert_called_once_with()
                self.api.generation.assert_not_called()

    def test_worker_classification_unknown_rollback_raises_after_private_body_has_finished(self):
        self.frappe.get_doc = Mock(return_value=Row(report_name=self.report.name, filters="private-fixture"))
        self.api.resolve_report.side_effect = PermissionError("private classification context")
        self.frappe.db.rollback.side_effect = RuntimeError("cleanup unknown")
        workers = (
            (self.subject.convert_json_to_csv, ("PR",)),
            (self.subject.run_export_query_job, ("actor@example.invalid", {"report_name": self.report.name}, {})),
        )
        for worker, arguments in workers:
            with self.subTest(worker=worker.__name__):
                try:
                    worker(*arguments)
                except RuntimeError as error:
                    self.assertEqual(str(error), self.subject._DENIED)
                    self.assertIsNone(error.__context__)
                    trace, frames = error.__traceback__, []
                    while trace:
                        frames.append(trace.tb_frame.f_code.co_name)
                        trace = trace.tb_next
                    self.assertNotIn("_convert_json_to_csv", frames)
                    self.assertNotIn("_run_export_query_job", frames)
                else:
                    self.fail("uncertain cleanup must prevent native success commit")

    def test_queued_document_or_export_parameter_failure_uses_same_cleanup_boundary(self):
        self.frappe.get_doc = Mock(side_effect=PermissionError("document was removed"))
        result = self.subject.convert_json_to_csv("PR")
        self.assertEqual(result, {"status": "Error", "message": self.subject._DENIED})
        self.frappe.db.rollback.assert_called_once_with()
        self.frappe.db.rollback.reset_mock()
        with patch.object(self.subject, "_export_parameters", side_effect=PermissionError("parameters changed")):
            result = self.subject.run_export_query_job("actor@example.invalid", {}, {})
        self.assertEqual(result, {"status": "Error", "message": self.subject._DENIED})
        self.frappe.db.rollback.assert_called_once_with()

    def test_ordinary_worker_native_exception_is_not_swallowed_or_rolled_back_by_app(self):
        self.api.resolve_report.return_value = None
        self.frappe.get_doc = Mock(return_value=Row(report_name="ordinary"))
        native_prepared = types.ModuleType("frappe.core.doctype.prepared_report.prepared_report")
        native_prepared.convert_json_to_csv = Mock(side_effect=ValueError("ordinary native error"))
        self.native.run_export_query_job = Mock(side_effect=ValueError("ordinary native error"))
        with patch.dict(sys.modules, {native_prepared.__name__: native_prepared}):
            with self.assertRaisesRegex(ValueError, "ordinary native error"):
                self.subject.convert_json_to_csv("PR")
        with self.assertRaisesRegex(ValueError, "ordinary native error"):
            self.subject.run_export_query_job("actor@example.invalid", {"report_name": "ordinary"}, {})
        self.frappe.db.rollback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
