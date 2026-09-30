from copy import deepcopy
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Doc(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def is_new(self):
        return bool(self.get("__islocal"))

    def set(self, key, value):
        self[key] = value

    def as_dict(self):
        return deepcopy({key: value for key, value in self.items() if key not in ("flags", "meta")})

    def check_permission(self, permission):
        if permission in self.get("denied", ()):
            raise PermissionError("native denied " + permission)

    def has_permission(self, permission, user=None):
        try:
            self.check_permission(permission)
        except PermissionError:
            return False
        return True


class TestPersonnelChanges(unittest.TestCase):
    def setUp(self):
        self.store, self.history_counter, self.native_submits, self.employee_saves = {}, 0, [], []
        self.company_denials, self.employee_denials = {}, {}
        self.field_denials = {"read": set(), "write": set()}
        self.column_denials = {"read": set(), "write": set()}
        self.table_denials = {"read": set(), "write": set()}
        self.table_permlevel = 0
        self.permlevels = {"read": {0}, "write": {0}}
        self.child_denials = {"read": set(), "write": set()}
        self.fieldnames = {"company", "department", "designation", "branch", "status", "date_of_joining",
                           "relieving_date", "user_id", "ctc", "internal_work_history", "employment_type",
                           "grade", "reports_to", "bank_ac_no", "phone"}
        field = Doc(fieldtype="Long Text", hidden=1, read_only=1, no_copy=1, allow_on_submit=1)
        self.source_meta = types.SimpleNamespace(get_field=lambda name: field if name == "jf_history_snapshot" else None)
        self.employee_meta = types.SimpleNamespace(get_field=lambda name:
            Doc(fieldname=name, fieldtype="Table" if name == "internal_work_history" else "Data",
                options="Employee Internal Work History" if name == "internal_work_history" else None,
                permlevel=self.table_permlevel if name == "internal_work_history" else 0)
            if name in self.fieldnames else None,
            get_permitted_fieldnames=Mock(side_effect=lambda permission_type, **kwargs:
                sorted(self.fieldnames - {"internal_work_history"} - self.field_denials[permission_type])),
            get_permlevel_access=Mock(side_effect=lambda permission_type, **kwargs:
                sorted(self.permlevels[permission_type] - ({self.table_permlevel}
                    if "internal_work_history" in self.table_denials[permission_type] | self.field_denials[permission_type]
                    else set()))))
        frappe = types.ModuleType("frappe")
        frappe.PermissionError = PermissionError
        frappe.share = types.SimpleNamespace(get_shared=Mock(return_value=[]))
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.get_meta = Mock(return_value=self.employee_meta)
        frappe.db = types.SimpleNamespace(
            get_value=Mock(side_effect=lambda doctype, name, field: self.store.get((doctype, name), {}).get(field)),
            escape=lambda value: "'" + value.replace("'", "''") + "'",
            delete=Mock(side_effect=AssertionError("global delete must never run")))
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = Mock(side_effect=lambda doctype, permission_type, **kwargs:
            sorted(self.fieldnames - {"internal_work_history"} - self.field_denials[permission_type]
                   - self.column_denials[permission_type]) if doctype == "Employee" else
            sorted({"branch", "department", "designation", "from_date", "to_date"}
                   - self.child_denials[permission_type]))
        self.frappe, self.model = frappe, model
        test = self

        class Employee(Doc):
            def check_permission(self, permission):
                super().check_permission(permission)
                if permission in test.employee_denials.get((self.name, self.company), ()):
                    raise PermissionError("native employee denied " + permission)

            def save(self):
                self.check_permission("write")
                test.employee_saves.append(self.name)
                for idx, row in enumerate(self.internal_work_history or [], 1):
                    if not row.name:
                        test.history_counter += 1
                        row.name = "HIST-" + str(test.history_counter)
                    row.update(parent=self.name, parenttype="Employee", parentfield="internal_work_history", idx=idx)
                test.store[("Employee", self.name)] = Doc(self.as_dict())
                return self

        self.Employee = Employee

        def get_doc(doctype, name=None, **kwargs):
            if isinstance(doctype, dict):
                data = deepcopy(doctype)
                return Employee(data, flags=Doc()) if data["doctype"] == "Employee" else Doc(data, flags=Doc())
            if doctype == "Company":
                return Doc(doctype=doctype, name=name, denied=self.company_denials.get(name, ()))
            if doctype == "Department":
                return Doc(doctype=doctype, name=name, company="OTHER" if name == "FOREIGN-DEPT" else "COMPANY")
            data = deepcopy(self.store[(doctype, name)])
            return Employee(data, flags=Doc()) if doctype == "Employee" else Doc(data, flags=Doc())

        frappe.get_doc = Mock(side_effect=get_doc)
        frappe.get_list = Mock(return_value=[Doc(name="EMP-A"), Doc(name="EMP-B")])
        frappe.get_all = Mock(return_value=[])
        path = Path(__file__).parents[1] / "jifeng_erp/personnel_changes.py"
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.model": model}):
            spec = importlib.util.spec_from_file_location("personnel_changes_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

        def native_update(employee, details, effective):
            if not details:
                return
            if not employee.internal_work_history:
                employee.internal_work_history = [Doc(branch=employee.branch, department=employee.department,
                    designation=employee.designation, from_date=employee.date_of_joining, to_date=None)]
            history = {}
            for row in details:
                employee[row.fieldname] = row.get("new") or None
                if row.fieldname in ("branch", "department", "designation"):
                    history[row.fieldname] = row.get("new") or None
            if history:
                employee.internal_work_history.append(Doc(from_date=effective, to_date=None, **history))
            for idx, row in enumerate(employee.internal_work_history):
                if idx and row.from_date and not employee.internal_work_history[idx - 1].to_date:
                    employee.internal_work_history[idx - 1].to_date = (
                        date.fromisoformat(str(row.from_date)) - timedelta(days=1)).isoformat()

        class Source(Doc):
            def db_set(self, field, value, **kwargs):
                self[field] = value
                test.store[(self.doctype, self.name)][field] = value

        class NativePromotion(Source):
            def before_submit(self):
                if self.promotion_date and date.fromisoformat(str(self.promotion_date)) > date.today():
                    raise RuntimeError("native future promotion date")

            def on_submit(self):
                test.native_submits.append(self.doctype)
                employee = frappe.get_doc("Employee", self.employee)
                native_update(employee, self.promotion_details, self.promotion_date)
                if self.revised_ctc:
                    employee.ctc = self.revised_ctc
                employee.save()

            def on_cancel(self):
                raise AssertionError("unsafe native cancel must never run")

        class NativeTransfer(Source):
            def before_submit(self):
                if self.transfer_date and date.fromisoformat(str(self.transfer_date)) > date.today():
                    raise RuntimeError("native future transfer date")

            def on_submit(self):
                test.native_submits.append(self.doctype)
                employee = frappe.get_doc("Employee", self.employee)
                native_update(employee, self.transfer_details, self.transfer_date)
                if self.new_company and self.new_company != self.company:
                    employee.company = self.new_company
                    employee.date_of_joining = self.transfer_date
                employee.save()

            def on_cancel(self):
                raise AssertionError("unsafe native cancel must never run")

        class Promotion(self.module.EmployeePromotionMixin, NativePromotion):
            pass

        class Transfer(self.module.EmployeeTransferMixin, NativeTransfer):
            pass

        self.Promotion, self.Transfer = Promotion, Transfer
        self.employee("EMP-A")
        self.employee("EMP-B")

    def employee(self, name, history=True, **values):
        row = Doc(name=name + "-OLD", parent=name, parenttype="Employee", parentfield="internal_work_history",
                  idx=1, branch="BRANCH", department="DEPT", designation="JOB", from_date="2020-01-01", to_date=None)
        data = Doc(doctype="Employee", name=name, company="COMPANY", department="DEPT", designation="JOB",
                   branch="BRANCH", status="Active", date_of_joining="2020-01-01", relieving_date=None,
                   user_id=None, ctc=1000, internal_work_history=[row] if history else [], **values)
        self.store[("Employee", name)] = data
        return data

    def source(self, transfer=False, employee="EMP-A", fields=None, **values):
        details = [Doc(name="DETAIL-" + str(index), fieldname=name, current="CLIENT-FORGED", new=value)
                   for index, (name, value) in enumerate(fields or [("designation", "NEW-JOB")], 1)]
        common = dict(name="SOURCE-" + str(len(self.native_submits)), employee=employee,
                      company="COMPANY", docstatus=0, flags=Doc(), meta=self.source_meta, jf_history_snapshot=None)
        if transfer:
            return self.Transfer(doctype="Employee Transfer", transfer_date="2024-01-01",
                                 transfer_details=details, **common, **values)
        return self.Promotion(doctype="Employee Promotion", promotion_date="2024-01-01",
                              promotion_details=details, current_ctc=999999, revised_ctc=2000, **common, **values)

    def submit(self, source):
        self.store[(source.doctype, source.name)] = source.as_dict()
        source.before_validate()
        source.before_submit()
        source.docstatus = 1
        self.store[(source.doctype, source.name)] = source.as_dict()
        source.on_submit()
        return source

    def cancel(self, source):
        source.before_cancel()
        source.docstatus = 2
        self.store[(source.doctype, source.name)] = source.as_dict()
        source.on_cancel()

    def test_promotion_exact_history_and_ctc_restore_without_other_employee_changes(self):
        other = self.store[("Employee", "EMP-B")]
        other.internal_work_history.append(Doc(name="B-SAME-JOB", parent="EMP-B", parenttype="Employee",
            parentfield="internal_work_history", idx=2, designation="NEW-JOB", from_date="2024-01-01", to_date=None))
        before, untouched = deepcopy(self.store[("Employee", "EMP-A")]), deepcopy(other)
        source = self.submit(self.source())
        self.assertEqual((source.promotion_details[0].current, source.current_ctc), ("JOB", 1000))
        snapshot = json.loads(source.jf_history_snapshot)
        self.assertEqual(snapshot["added_history_ids"], ["HIST-1"])
        self.assertEqual(snapshot["to_date_changes"], [{"name": "EMP-A-OLD", "before": None, "after": "2023-12-31"}])
        self.cancel(source)
        self.assertEqual(self.store[("Employee", "EMP-A")], before)
        self.assertEqual(self.store[("Employee", "EMP-B")], untouched)
        self.assertEqual(self.native_submits, ["Employee Promotion"])
        self.assertEqual(self.employee_saves, ["EMP-A", "EMP-A"])
        self.frappe.db.delete.assert_not_called()

    def test_empty_original_history_removes_only_both_generated_rows(self):
        self.store[("Employee", "EMP-A")].internal_work_history = []
        source = self.submit(self.source())
        self.assertEqual(len(json.loads(source.jf_history_snapshot)["added_history_ids"]), 2)
        self.cancel(source)
        self.assertEqual(self.store[("Employee", "EMP-A")].internal_work_history, [])

    def test_blank_new_job_and_empty_old_value_are_exactly_restored(self):
        self.store[("Employee", "EMP-A")].designation = None
        source = self.submit(self.source(fields=[("designation", "")]))
        self.assertEqual(source.promotion_details[0].current, "")
        self.cancel(source)
        self.assertIsNone(self.store[("Employee", "EMP-A")].designation)

    def test_native_future_date_check_is_retained(self):
        for transfer in (False, True):
            source = self.source(transfer=transfer)
            source[source._change_date] = (date.today() + timedelta(days=1)).isoformat()
            with self.subTest(transfer=transfer), self.assertRaisesRegex(RuntimeError, "native future"):
                self.submit(source)
        self.assertEqual(self.employee_saves, [])

    def test_salary_only_promotion_preserves_native_history_and_zero_current_ctc(self):
        self.store[("Employee", "EMP-A")].ctc = 0
        source = self.source()
        source.promotion_details = []
        before_history = deepcopy(self.store[("Employee", "EMP-A")].internal_work_history)
        self.submit(source)
        self.assertEqual(source.current_ctc, 0)
        source.current_ctc = 2000  # Native fetch_if_empty during cancel refreshes zero to revised CTC.
        self.cancel(source)
        self.assertEqual(self.store[("Employee", "EMP-A")].ctc, 0)
        self.assertEqual(self.store[("Employee", "EMP-A")].internal_work_history, before_history)

    def test_same_id_company_transfer_restores_joining_date_and_native_refetched_header(self):
        source = self.source(transfer=True, fields=[("department", "FOREIGN-DEPT")], new_company="OTHER")
        original = deepcopy(self.store[("Employee", "EMP-A")])
        self.submit(source)
        moved = self.store[("Employee", "EMP-A")]
        self.assertEqual((moved.company, moved.date_of_joining), ("OTHER", "2024-01-01"))
        source.company = "OTHER"  # Native fetch_from during cancel, after docstatus becomes 2.
        self.cancel(source)
        self.assertEqual(self.store[("Employee", "EMP-A")], original)
        self.assertEqual(source.company, "COMPANY")

    def test_company_mismatch_or_target_department_mismatch_rejected(self):
        for values in ({"company": "WRONG"}, {"new_company": "OTHER"}):
            source = self.source(transfer=True)
            source.update(values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.submit(source)
        self.assertEqual(self.employee_saves, [])

    def test_cross_company_requires_prospective_native_employee_read_and_write(self):
        for permission in ("read", "write"):
            self.employee_denials[("EMP-A", "OTHER")] = (permission,)
            source = self.source(transfer=True, fields=[("department", "FOREIGN-DEPT")], new_company="OTHER")
            with self.subTest(permission=permission), self.assertRaises(PermissionError):
                self.submit(source)
        self.assertEqual(self.employee_saves, [])

    def test_cross_company_requires_native_company_selection(self):
        self.company_denials["OTHER"] = ("select",)
        with self.assertRaises(PermissionError):
            self.submit(self.source(transfer=True, fields=[("department", "FOREIGN-DEPT")], new_company="OTHER"))

    def test_employee_complete_read_or_write_required(self):
        for permission in ("read", "write"):
            self.employee_denials[("EMP-A", "COMPANY")] = (permission,)
            with self.subTest(permission=permission), self.assertRaises(PermissionError):
                self.submit(self.source())
        self.assertEqual(self.employee_saves, [])

    def test_employee_field_level_read_and_write_required_before_current_or_snapshot(self):
        for permission in ("read", "write"):
            for field in ("designation", "ctc", "internal_work_history"):
                self.field_denials[permission] = {field}
                source = self.source()
                with self.subTest(permission=permission, field=field), self.assertRaisesRegex(PermissionError, "员工字段"):
                    self.submit(source)
                self.assertEqual(source.promotion_details[0].current, "CLIENT-FORGED")
                self.assertIsNone(source.jf_history_snapshot)
            self.field_denials[permission] = set()
        self.assertEqual(self.employee_saves, [])

    def test_history_child_read_and_write_permissions_use_employee_parenttype(self):
        for permission in ("read", "write"):
            self.child_denials[permission] = {"to_date"}
            source = self.source()
            with self.subTest(permission=permission), self.assertRaises(PermissionError):
                self.submit(source)
            self.assertEqual(source.promotion_details[0].current, "CLIENT-FORGED")
            self.assertIsNone(source.jf_history_snapshot)
            self.model.get_permitted_fields.assert_any_call("Employee Internal Work History", parenttype="Employee",
                permission_type=permission, ignore_virtual=True)
            self.child_denials[permission] = set()
        self.assertEqual(self.employee_saves, [])

    def test_physical_columns_exclude_table_and_native_table_metadata_allows_safe_chain(self):
        columns = self.model.get_permitted_fields("Employee", permission_type="read", ignore_virtual=True)
        self.assertNotIn("internal_work_history", columns)
        self.assertNotIn("internal_work_history", self.employee_meta.get_permitted_fieldnames(
            user=None, permission_type="read", with_virtual_fields=False))
        source = self.submit(self.source())
        self.assertIs(self.module.personnel_change_has_permission(source, "read", user="reader"), True)
        self.query_fixtures([source])
        self.assertIn(source.name, self.module.promotion_query_conditions(user="reader"))
        self.cancel(source)
        for permission in ("read", "write"):
            self.employee_meta.get_permlevel_access.assert_any_call(user=None, permission_type=permission)
        self.employee_meta.get_permlevel_access.assert_any_call(user="reader", permission_type="read")
        self.assertEqual(self.employee_saves, ["EMP-A", "EMP-A"])

    def test_parent_table_uses_actual_permlevel_without_granting_other_fields(self):
        self.table_permlevel = 1
        source = self.source()
        with self.assertRaises(PermissionError):
            self.submit(source)
        self.permlevels = {"read": {0, 1}, "write": {0, 1}}
        self.submit(source)
        self.assertIs(self.module.personnel_change_has_permission(source, "read", user="reader"), True)
        self.permlevels["read"] = {0}
        self.assertIs(self.module.personnel_change_has_permission(source, "read", user="reader"), False)
        self.assertEqual(self.module.promotion_query_conditions(user="reader"), "1=0")

    def test_native_read_share_can_supply_zero_table_level_but_not_full_read_or_write(self):
        source = self.submit(self.source())
        self.permlevels["read"] = set()
        self.frappe.share.get_shared.return_value = ["EMP-A"]
        self.assertIs(self.module.personnel_change_has_permission(source, "read", user="reader"), True)
        self.frappe.share.get_shared.assert_called_with("Employee", "reader", rights=["read"], limit=1)
        self.employee_denials[("EMP-A", "COMPANY")] = ("read",)
        self.assertIs(self.module.personnel_change_has_permission(source, "read", user="reader"), False)
        self.employee_denials.clear()
        self.permlevels["write"] = set()
        with self.assertRaises(PermissionError):
            source.before_cancel()

    def test_parent_table_permission_is_separate_from_child_columns(self):
        for permission in ("read", "write"):
            self.table_denials[permission] = {"internal_work_history"}
            source = self.source()
            with self.subTest(permission=permission), self.assertRaises(PermissionError):
                self.submit(source)
            self.assertEqual(source.promotion_details[0].current, "CLIENT-FORGED")
            self.assertIsNone(source.jf_history_snapshot)
            self.assertEqual(set(self.model.get_permitted_fields("Employee Internal Work History",
                parenttype="Employee", permission_type=permission, ignore_virtual=True)),
                set(self.module.HISTORY_FIELDS))
            self.table_denials[permission] = set()
        self.assertEqual(self.employee_saves, [])

    def test_source_reads_and_query_require_parent_table_permission(self):
        source = self.submit(self.source())
        self.query_fixtures([source])
        self.table_denials["read"] = {"internal_work_history"}
        self.assertIs(self.module.personnel_change_has_permission(source, "read", user="reader"), False)
        self.assertEqual(self.module.promotion_query_conditions(user="reader"), "1=0")
        self.assertEqual(self.module.transfer_query_conditions(user="reader"), "1=0")
        self.assertEqual(self.child_denials["read"], set())
        self.table_denials["read"] = set()
        self.table_denials["write"] = {"internal_work_history"}
        self.assertIs(self.module.personnel_change_has_permission(source, "read", user="reader"), True)
        with self.assertRaises(PermissionError):
            source.before_cancel()
        self.assertEqual(len(self.employee_saves), 1)

    def test_metadata_fields_do_not_grant_a_denied_physical_column(self):
        self.column_denials["read"] = {"designation"}
        self.assertIn("designation", self.employee_meta.get_permitted_fieldnames(
            permission_type="read", user=None, with_virtual_fields=False))
        with self.assertRaises(PermissionError):
            self.submit(self.source())
        self.assertIs(self.module.personnel_change_has_permission(self.source(), "read"), False)
        self.assertEqual(self.employee_saves, [])

    def test_private_or_unsupported_employee_properties_never_enter_snapshot(self):
        self.field_denials["read"] = {"bank_ac_no"}
        with self.assertRaises(PermissionError):
            self.submit(self.source(fields=[("bank_ac_no", "FAKE")]))
        self.field_denials["read"] = set()
        with self.assertRaisesRegex(ValueError, "仅支持岗位属性"):
            self.submit(self.source(fields=[("bank_ac_no", "FAKE")]))
        with self.assertRaisesRegex(ValueError, "不能重复"):
            self.submit(self.source(fields=[("designation", "A"), ("designation", "B")]))

    def test_new_employee_id_branch_is_explicitly_outside_safe_chain(self):
        with self.assertRaisesRegex(ValueError, "暂不支持创建新员工编号"):
            self.submit(self.source(transfer=True, create_new_employee_id=1))
        self.assertEqual(self.native_submits, [])

    def test_snapshot_cannot_be_injected_on_new_or_existing_draft(self):
        for new in (False, True):
            source = self.source()
            if new:
                source["__islocal"] = 1
            else:
                self.store[(source.doctype, source.name)] = source.as_dict()
            source.jf_history_snapshot = '{"version":1}'
            with self.subTest(new=new), self.assertRaisesRegex(ValueError, "只能由服务器生成"):
                source.before_validate()

    def test_snapshot_update_after_submit_and_cancel_tampering_rejected(self):
        source = self.submit(self.source())
        source.jf_history_snapshot += " "
        for method in (source.before_validate, source.before_update_after_submit, source.before_cancel):
            with self.subTest(method=method.__name__), self.assertRaisesRegex(ValueError, "只能由服务器生成"):
                method()
        self.assertEqual(len(self.employee_saves), 1)

    def test_cancel_binds_exact_source_employee_date_and_details(self):
        source = self.submit(self.source())
        for key, value in (("employee", "EMP-B"), ("promotion_date", "2024-01-02"), ("revised_ctc", 3000)):
            previous = source[key]
            source[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "快照不一致"):
                source.before_cancel()
            source[key] = previous
        source.promotion_details[0].current = "CLIENT-FORGED"
        with self.assertRaisesRegex(ValueError, "快照不一致"):
            source.before_cancel()

    def test_later_field_and_history_changes_are_not_overwritten(self):
        source = self.submit(self.source())
        current = self.store[("Employee", "EMP-A")]
        after = deepcopy(current)
        changes = (("field", lambda: current.update(designation="LATER-JOB")),
                   ("row", lambda: current.internal_work_history[0].update(to_date="2025-01-01")),
                   ("new row", lambda: current.internal_work_history.append(Doc(name="LATER-ROW", parent="EMP-A",
                    parenttype="Employee", parentfield="internal_work_history", idx=3, designation="LATER-JOB"))))
        for label, change in changes:
            current.clear()
            current.update(deepcopy(after))
            change()
            changed = deepcopy(current)
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "后续变更"):
                source.before_cancel()
            self.assertEqual(current, changed)
        self.assertEqual(len(self.employee_saves), 1)

    def test_unrelated_later_employee_value_is_preserved(self):
        source = self.submit(self.source())
        self.store[("Employee", "EMP-A")].phone = "LATER-PHONE"
        self.cancel(source)
        self.assertEqual(self.store[("Employee", "EMP-A")].phone, "LATER-PHONE")

    def test_cancel_requires_native_source_cancel_and_employee_field_permissions(self):
        source = self.submit(self.source())
        source.denied = ("cancel",)
        with self.assertRaises(PermissionError):
            source.before_cancel()
        source.denied = ()
        self.field_denials["write"] = {"designation"}
        with self.assertRaises(PermissionError):
            source.before_cancel()

    def test_repeated_cancel_and_direct_callback_are_rejected(self):
        source = self.submit(self.source())
        with self.assertRaisesRegex(ValueError, "服务器锁定"):
            source.on_cancel()
        self.cancel(source)
        with self.assertRaisesRegex(ValueError, "旧人事单据"):
            source.before_cancel()
        with self.assertRaisesRegex(ValueError, "服务器锁定"):
            source.on_cancel()
        self.assertEqual(len(self.employee_saves), 2)

    def test_legacy_or_malformed_snapshot_refuses_cancel_without_guessing(self):
        for raw in (None, "not json", '{"version":1}'):
            source = self.source()
            source.docstatus = 1
            source.jf_history_snapshot = raw
            self.store[(source.doctype, source.name)] = source.as_dict()
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "人工核对迁移"):
                source.before_cancel()
        self.assertEqual(self.employee_saves, [])

    def test_employee_lock_and_default_native_save_are_used(self):
        source = self.submit(self.source())
        self.cancel(source)
        self.frappe.get_doc.assert_any_call("Employee", "EMP-A", for_update=True)
        self.assertEqual(self.employee_saves, ["EMP-A", "EMP-A"])

    def test_source_read_hook_is_additional_denial_and_never_requires_employee_write(self):
        source = self.submit(self.source())
        self.employee_denials[("EMP-A", "COMPANY")] = ("write",)
        self.field_denials["write"] = {"ctc", "designation"}
        for permission in ("read", "select", "print", "email", "export", "report", "write", "submit", "cancel"):
            with self.subTest(permission=permission):
                self.assertIs(self.module.personnel_change_has_permission(source, permission, user="reader"), True)
        self.model.get_permitted_fields.assert_any_call("Employee", user="reader", permission_type="read",
                                                       ignore_virtual=True)
        self.model.get_permitted_fields.assert_any_call("Employee Internal Work History", parenttype="Employee",
            user="reader", permission_type="read", ignore_virtual=True)

    def test_source_read_hook_revokes_later_employee_and_field_access(self):
        source = self.submit(self.source(fields=[("grade", "NEW-GRADE")]))
        self.employee_denials[("EMP-A", "COMPANY")] = ("read",)
        self.assertIs(self.module.personnel_change_has_permission(source, "read"), False)
        self.employee_denials.clear()
        for field in ("ctc", "grade", "user_id", "internal_work_history"):
            self.field_denials["read"] = {field}
            with self.subTest(field=field):
                self.assertIs(self.module.personnel_change_has_permission(source, "read"), False)
        self.field_denials["read"] = set()
        self.child_denials["read"] = {"from_date"}
        self.assertIs(self.module.personnel_change_has_permission(source, "read"), False)

    def test_transfer_reader_needs_selected_fields_but_not_unrelated_ctc(self):
        source = self.submit(self.source(transfer=True))
        self.field_denials["read"] = {"ctc", "grade", "bank_ac_no"}
        self.assertIs(self.module.personnel_change_has_permission(source, "read"), True)
        self.field_denials["read"].add("designation")
        self.assertIs(self.module.personnel_change_has_permission(source, "read"), False)

    def test_legacy_details_still_cannot_leak_hidden_fields(self):
        source = self.source(fields=[("bank_ac_no", "ACCOUNT")])
        self.field_denials["read"] = {"bank_ac_no"}
        self.assertIs(self.module.personnel_change_has_permission(source, "read"), False)
        self.assertIs(self.module.personnel_change_has_permission(source, "submit"), True)
        source.promotion_details = []
        self.assertIs(self.module.personnel_change_has_permission(source, "read"), True)

    def test_snapshot_read_refuses_extra_keys_fields_and_employee_mismatch(self):
        source = self.submit(self.source())
        snapshot = json.loads(source.jf_history_snapshot)
        for label, mutate in (
            ("extra raw", lambda data: data.update(bank_ac_no="HIDDEN")),
            ("extra field", lambda data: data["before"]["fields"].update(bank_ac_no="HIDDEN")),
            ("different employee", lambda data: data["source"].update(employee="EMP-B")),
            ("extra source", lambda data: data["source"].update(private="HIDDEN")),
        ):
            data = deepcopy(snapshot)
            mutate(data)
            source.jf_history_snapshot = json.dumps(data)
            with self.subTest(label=label):
                self.assertIs(self.module.personnel_change_has_permission(source, "read"), False)
        source.jf_history_snapshot = json.dumps(snapshot)
        self.cancel(source)
        self.assertIs(self.module.personnel_change_has_permission(source, "read"), True)

    def query_fixtures(self, sources):
        def get_all(doctype, filters, fields, **kwargs):
            if doctype in ("Employee Promotion", "Employee Transfer"):
                allowed = set(filters["employee"][1])
                return [Doc({field: source.get(field) for field in fields}) for source in sources
                        if source.doctype == doctype and source.employee in allowed]
            self.assertEqual(doctype, "Employee Property History")
            self.assertEqual(filters["parentfield"], self.module.SOURCE_TYPES[filters["parenttype"]])
            parents = set(filters["parent"][1])
            return [Doc(parent=source.name, fieldname=detail.fieldname)
                    for source in sources if source.name in parents and source.doctype == filters["parenttype"]
                    for detail in source.get(source._change_details) or []]
        self.frappe.get_all.side_effect = get_all

    def test_query_path_filters_employee_native_list_and_complete_read(self):
        source = self.source()
        source.name = "VISIBLE'NAME"
        hidden = self.source(employee="EMP-B")
        hidden.name = "HIDDEN-SOURCE"
        self.query_fixtures([source, hidden])
        self.employee_denials[("EMP-B", "COMPANY")] = ("read",)
        condition = self.module.promotion_query_conditions(user="reader", doctype="Employee Promotion")
        self.assertEqual(condition, "`tabEmployee Promotion`.`name` in ('VISIBLE''NAME')")
        self.frappe.get_list.assert_called_once_with("Employee", fields=["name"], user="reader", limit_page_length=0)
        self.frappe.get_all.assert_any_call("Employee Promotion", filters={"employee": ["in", ["EMP-A"]]},
            fields=["name", "employee", "jf_history_snapshot"], limit_page_length=0)
        self.frappe.get_list.return_value = [Doc(name="EMP-B")]
        self.assertEqual(self.module.promotion_query_conditions(user="reader"), "1=0")

    def test_query_path_field_denial_hides_snapshot_and_legacy_current_values(self):
        safe = self.source(transfer=True)
        safe.name = "SAFE-SOURCE"
        hidden = self.source(transfer=True, fields=[("bank_ac_no", "BANK")])
        hidden.name = "PRIVATE-SOURCE"
        self.query_fixtures([safe, hidden])
        self.field_denials["read"] = {"bank_ac_no", "ctc"}
        self.assertEqual(self.module.transfer_query_conditions(user="reader"),
                         "`tabEmployee Transfer`.`name` in ('SAFE-SOURCE')")
        self.assertEqual(self.module.promotion_query_conditions(user="reader"), "1=0")
        self.child_denials["read"] = {"to_date"}
        self.assertEqual(self.module.transfer_query_conditions(user="reader"), "1=0")

    def test_query_path_checks_snapshot_fields_even_when_current_details_are_empty(self):
        source = self.submit(self.source(fields=[("grade", "NEW-GRADE")]))
        source.promotion_details = []
        self.query_fixtures([source])
        self.field_denials["read"] = {"grade"}
        self.assertEqual(self.module.promotion_query_conditions(user="reader"), "1=0")
        self.field_denials["read"] = set()
        self.assertIn("'" + source.name + "'", self.module.promotion_query_conditions(user="reader"))
        source.jf_history_snapshot = "not json"
        self.assertEqual(self.module.promotion_query_conditions(user="reader"), "1=0")

    def test_query_native_employee_permission_error_and_empty_scope_are_not_grants(self):
        self.frappe.get_list.side_effect = PermissionError("native Employee denied")
        self.assertEqual(self.module.promotion_query_conditions(user="reader"), "1=0")
        self.frappe.get_all.assert_not_called()
        self.frappe.get_list.side_effect = None
        self.frappe.get_list.return_value = []
        self.assertEqual(self.module.transfer_query_conditions(user="reader"), "1=0")
        self.frappe.get_all.assert_not_called()


if __name__ == "__main__":
    unittest.main()
