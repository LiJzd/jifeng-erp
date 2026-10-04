"""Report guards preserve native output and reject precise protected sources."""

from contextvars import ContextVar
import ast
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Row(dict):
    __getattr__ = dict.get

    def as_dict(self):
        return dict(self)


def freeze(value):
    if isinstance(value, dict):
        return tuple((key, freeze(item)) for key, item in sorted(value.items()) if not key.startswith('__'))
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return type(value).__name__, repr(value)


class ReportModels(unittest.TestCase):
    def setUp(self):
        self.docs, self.rows, self.raw, self.checked, self.reads, self.field_checks = {}, [], {}, [], [], []
        self.denied_fields = set()
        fr = types.ModuleType('frappe')
        fr.PermissionError = PermissionError
        fr.session = Row(user='actor')
        fr.get_doc = lambda dt, name: copy.deepcopy(self.docs[dt, name])
        fr.db = Row(sql=Mock(side_effect=lambda query, **kwargs: copy.deepcopy(self.rows)))
        fr.get_all = Mock(side_effect=lambda dt, **kwargs: copy.deepcopy(self.raw.get(dt, [])))
        def throw(message, error=ValueError):
            raise error(message)
        fr.throw = throw
        self.fr = fr
        source = types.ModuleType('jifeng_erp.sample_source_permissions')
        source.PARENTS = frozenset({'Jifeng Sample Order', 'Quotation', 'Sales Order', 'Work Order', 'Jifeng Flow Order'})
        source._AUTHORIZING = ContextVar('test_report_auth', default=frozenset())
        source._key = lambda doc, user: (doc.doctype, doc.name, user, 0)
        source._reference = lambda dt, name: copy.deepcopy(self.docs.get((dt, name)))
        source._scope = lambda doc: bool(doc.get('protected'))
        def native_read(doc, user):
            self.reads.append((doc.doctype, doc.name, user))
            if not doc.get('readable', True):
                raise PermissionError('PRIVATE-DOC-NAME-AND-VALUE')
        source._native_read = native_read
        def child_parent(doc):
            parent = copy.deepcopy(self.docs.get((doc.parenttype, doc.parent)))
            if not parent or doc.parentfield != 'items':
                raise PermissionError('PRIVATE-CHILD')
            matches = [row for row in parent.get('items', []) if row.name == doc.name]
            if len(matches) != 1 or any(matches[0].get(key) != doc.get(key)
                                       for key in ('name', 'parent', 'parenttype', 'parentfield')):
                raise PermissionError('PRIVATE-CHILD')
            return parent
        source._child_parent = child_parent
        def check(doc, user):
            self.checked.append((doc.doctype, doc.name, user))
            native_read(doc, user)
            company = copy.deepcopy(self.docs['Company', doc.company])
            self.subject.observe_dependency(company, fields={'name', 'modified'}, permission='select')
            for key in doc.get('dependencies', []):
                dependency = copy.deepcopy(self.docs[tuple(key)])
                native_read(dependency, user)
                self.subject.observe_dependency(dependency, fields=set(dependency), permission='read')
            self.subject.observe_dependency(doc, completed_source=True)
        source._check = Mock(side_effect=check)
        self.source = source
        quantity = types.ModuleType('jifeng_erp.flow_quantity')
        def fields(dt, wanted, permission_type='read', parenttype=None):
            self.field_checks.append((dt, tuple(wanted), parenttype))
            if any((dt, name) in self.denied_fields for name in wanted):
                raise PermissionError('PRIVATE-MASK-FIELD')
        quantity.check_quantity_fields = fields
        samples = types.ModuleType('jifeng_erp.sample_orders')
        samples._freeze = freeze
        app = types.ModuleType('jifeng_erp')
        app.sample_source_permissions = source
        self.modules = {'frappe': fr, 'jifeng_erp': app,
                        'jifeng_erp.sample_source_permissions': source,
                        'jifeng_erp.sample_orders': samples, 'jifeng_erp.flow_quantity': quantity}
        with patch.dict(sys.modules, self.modules):
            spec = importlib.util.spec_from_file_location('sample_reports_under_test', ROOT / 'jifeng_erp/sample_source_reports.py')
            self.subject = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = self.subject
            spec.loader.exec_module(self.subject)
        self.company = self.doc('Company', 'JF')
        self.work = self.doc('Work Order', 'WO', protected=True, company='JF', qty=3,
                             produced_qty=0, modified='version1', creation='created', production_item='ITEM',
                             docstatus=1, status='Not Started')
        self.rows = [Row(work_order='WO')]

    def doc(self, dt, name, **values):
        row = Row(doctype=dt, name=name, **values)
        self.docs[dt, name] = row
        return row

    def report(self, name='Open Work Orders'):
        subject = self.subject
        class Native(Row):
            def execute_query_report(self, filters):
                self.calls.append(('query', filters))
                if self.action:
                    self.action()
                return self.result
            def execute_script_report(self, filters):
                self.calls.append(('script', filters))
                if self.action:
                    self.action()
                return self.result
        class Report(subject.SampleSourceReportMixin, Native):
            pass
        query = 'MODEL NATIVE QUERY ' + name
        if name in subject.REPORT_SPECS:
            subject.REPORT_SPECS[name] = dict(subject.REPORT_SPECS[name], query_sha256=hashlib.sha256(query.encode()).hexdigest())
        document = Report(doctype='Report', name=name, report_name=name, report_type='Query Report',
                          ref_doctype='Work Order', module='Manufacturing', is_standard='Yes',
                          query=query, reference_report=None, report_script=None, snapshot_report=0)
        document.calls, document.action = [], None
        document.result = [object(), object()]
        self.docs['Report', name] = Row(document)
        return document

    def issued(self):
        detail = self.doc('Stock Entry Detail', 'SED', parent='STE', parenttype='Stock Entry',
                          parentfield='items', item_code='RAW', s_warehouse='WH', amount=27,
                          transfer_qty=3, stock_uom='Nos', description='material', serial_no=None,
                          t_warehouse='WIP', modified='detail-v1')
        self.entry = self.doc('Stock Entry', 'STE', company='JF', work_order='WO', posting_date='date',
                              docstatus=1, purpose='Material Transfer for Manufacture', items=[Row(detail)],
                              modified='entry-v1')
        self.bin = self.doc('Bin', 'BIN', item_code='RAW', warehouse='WH', projected_qty=9, modified='bin-v1')
        self.raw['Bin'] = [Row(self.bin)]
        self.rows = [Row(work_order='WO', stock_entry='STE', stock_entry_detail='SED',
                         item_code='RAW', s_warehouse='WH')]
        return self.report('Issued Items Against Work Order')

    def test_native_args_and_return_identity_preserved_after_two_full_checks(self):
        report, filters = self.report(), Row(company='IGNORED-NATIVE-SQL', from_date='date')
        result = report.execute_query_report(filters)
        self.assertIs(result, report.result)
        self.assertIs(report.calls[0][1], filters)
        self.assertEqual(self.checked, [('Work Order', 'WO', 'actor')] * 2)
        self.assertEqual(self.fr.db.sql.call_count, 2)
        self.assertIsNone(self.subject._OBSERVING.get())
        self.assertFalse(self.subject._EXECUTING.get())

    def test_other_report_entirely_native_without_source_queries(self):
        report = self.report('Ordinary Report')
        filters = object()
        self.assertIs(report.execute_query_report(filters), report.result)
        self.fr.db.sql.assert_not_called()
        self.source._check.assert_not_called()
        self.assertIs(report.execute_script_report(filters), report.result)

    def test_missing_exact_registration_is_native_not_blanket_report_denial(self):
        report = self.report('Work Order Summary')
        with patch.object(self.subject, '_spec', return_value=None):
            self.assertIs(report.execute_script_report({}), report.result)
        self.source._check.assert_not_called()

    def test_actual_companion_registry_is_exact_ten_with_fixed_script_fingerprints(self):
        name = 'jifeng_erp.sample_source_report_scripts'
        modules = {**self.modules, 'jifeng_erp.sample_source_reports': self.subject}
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(name, ROOT / 'jifeng_erp/sample_source_report_scripts.py')
            companion = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {name: companion}):
                spec.loader.exec_module(companion)
                scripts = companion.get_script_specs()
                self.assertEqual(set(scripts) | set(self.subject.REPORT_SPECS), set(self.subject.KNOWN_REPORTS))
                self.assertFalse(set(scripts) & set(self.subject.REPORT_SPECS))
                for report_name in scripts:
                    registered = self.subject._spec(report_name)
                    self.assertEqual(registered['kind'], 'Script Report')
                    self.assertEqual(registered['is_standard'], 'Yes')
                    self.assertEqual(len(registered['module_sha256']), 64)
                    self.assertTrue(registered['execute_module'].startswith('erpnext.'))
                    self.assertTrue(callable(registered['selector']))

    def test_ordinary_work_order_preserves_native_without_extra_permissions(self):
        self.work['protected'], self.work['readable'] = False, False
        report = self.report()
        self.assertIs(report.execute_query_report({}), report.result)
        self.assertFalse(self.checked)
        self.assertFalse(self.field_checks)

    def test_unreadable_source_refuses_before_native_and_error_has_no_id_or_value(self):
        self.work['readable'] = False
        report = self.report()
        with self.assertRaises(PermissionError) as error:
            report.execute_query_report({})
        self.assertFalse(report.calls)
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertNotIn('WO', str(error.exception))

    def test_missing_selected_reference_is_not_silently_ordinary(self):
        del self.docs['Work Order', 'WO']
        report = self.report()
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertFalse(report.calls)

    def test_work_order_quantity_or_creation_mask_refuses(self):
        for field in ('creation', 'produced_qty'):
            self.denied_fields = {('Work Order', field)}
            report = self.report()
            with self.assertRaises(PermissionError):
                report.execute_query_report({})
            self.assertFalse(report.calls)

    def test_wrong_query_fingerprint_rejects_even_same_report_name(self):
        report = self.report()
        report['query'] += ' WHERE company=caller_company'
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertFalse(report.calls)
        self.fr.db.sql.assert_not_called()

    def test_type_refdoctype_module_standard_and_alias_definition_must_match(self):
        for field, value in (('report_type', 'Script Report'), ('ref_doctype', 'Item'),
                             ('module', 'Custom'), ('is_standard', 'No'),
                             ('reference_report', 'Other'), ('report_script', 'data=private')):
            with self.subTest(field=field):
                report = self.report()
                report[field] = value
                with self.assertRaises(PermissionError):
                    report.execute_query_report({})
                self.assertFalse(report.calls)

    def test_stored_report_definition_change_refuses_live_clean_definition(self):
        report = self.report()
        self.docs['Report', report.name]['query'] += ' changed'
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertFalse(report.calls)

    def test_query_report_cannot_be_dispatched_through_script_method(self):
        report = self.report()
        with self.assertRaises(PermissionError):
            report.execute_script_report({})
        self.assertFalse(report.calls)

    def test_definition_change_during_native_refuses_output(self):
        report = self.report()
        report.action = lambda: self.docs['Report', report.name].update(module='Custom')
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertEqual(len(report.calls), 1)

    def test_source_membership_or_multiplicity_changes_refuse(self):
        for rows in ([], [Row(work_order='WO'), Row(work_order='WO')]):
            report = self.report()
            report.action = lambda rows=rows: setattr(self, 'rows', rows)
            self.rows = [Row(work_order='WO')]
            with self.assertRaises(PermissionError):
                report.execute_query_report({})

    def test_row_order_only_change_preserves_same_multiset(self):
        other = self.doc('Work Order', 'OTHER', protected=False)
        self.rows = [Row(work_order=self.work.name), Row(work_order=other.name)]
        report = self.report()
        report.action = lambda: self.rows.reverse()
        self.assertIs(report.execute_query_report({}), report.result)

    def test_parent_version_and_business_values_change_even_without_modified_refuse(self):
        for field, value in (('modified', 'version2'), ('produced_qty', 2), ('qty', 4)):
            original = copy.deepcopy(self.work)
            report = self.report()
            report.action = lambda field=field, value=value: self.work.update({field: value})
            with self.assertRaises(PermissionError):
                report.execute_query_report({})
            self.work.clear()
            self.work.update(original)

    def test_legally_changed_bom_across_execute_is_not_hidden_by_each_check(self):
        bom = self.doc('BOM', 'BOM', modified='v1', quantity=3)
        self.work['dependencies'] = [('BOM', 'BOM')]
        report = self.report()
        report.action = lambda: bom.update(quantity=4)
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertEqual(self.checked, [('Work Order', 'WO', 'actor')] * 2)

    def test_source_dependency_membership_changes_refuse(self):
        self.doc('Item', 'ITEM', modified='v1')
        report = self.report()
        report.action = lambda: self.work.update(dependencies=[('Item', 'ITEM')])
        with self.assertRaises(PermissionError):
            report.execute_query_report({})

    def test_post_read_permission_revocation_refuses_output(self):
        item = self.doc('Item', 'ITEM', modified='v1')
        self.work['dependencies'] = [('Item', 'ITEM')]
        report = self.report()
        report.action = lambda: item.update(readable=False)
        with self.assertRaises(PermissionError) as error:
            report.execute_query_report({})
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_uncompleted_recursive_permission_is_not_a_report_receipt(self):
        report = self.report()
        token = self.source._AUTHORIZING.set(frozenset({self.source._key(self.work, 'actor')}))
        try:
            with self.assertRaises(PermissionError):
                report.execute_query_report({})
        finally:
            self.source._AUTHORIZING.reset(token)
        self.assertFalse(report.calls)
        self.source._check.assert_not_called()

    def test_missing_callbacks_cannot_masquerade_as_full_dependency_capture(self):
        report = self.report()
        self.source._check.side_effect = None
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertFalse(report.calls)

    def test_final_parent_callback_only_requires_actual_company_source_read(self):
        report = self.report()
        self.source._check.side_effect = lambda doc, user: self.subject.observe_dependency(doc, completed_source=True)
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertFalse(report.calls)

    def test_observer_no_frame_is_no_op_even_missing_document_attributes(self):
        self.subject.observe_dependency(object(), fields=['private'])
        self.assertIsNone(self.subject._OBSERVING.get())

    def test_actual_sources_source_callback_records_fields_tables_without_extra_query(self):
        # Execute the real shared Source.source method, retaining its successful
        # read/permission/projection ordering; no fake observer call substitutes.
        tree = ast.parse((ROOT / 'jifeng_erp/sample_orders.py').read_bytes())
        source_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == '_Sources')
        namespace = {'frappe': self.fr, '_freeze': freeze, '_name': lambda name: name,
                     '_projection': Mock(), '_table': Mock(), '_invalid': lambda message: self.fr.throw(message)}
        exec(compile(ast.Module(body=[source_class], type_ignores=[]), 'actual_sources_class', 'exec'), namespace)
        document = self.doc('Item', 'ITEM', modified='item-v1', stock_uom='Nos', defaults=[])
        permissions = []
        document.check_permission = lambda permission: permissions.append(permission)
        document.meta = Row(get_valid_columns=lambda: ['name', 'modified', 'stock_uom'],
                             get_table_fields=lambda: [Row(fieldname='defaults', options='Item Default')])
        original_get_doc = self.fr.get_doc
        self.fr.get_doc = lambda dt, name: document if dt == 'Item' else original_get_doc(dt, name)
        self.fr.get_meta = lambda dt: Row(get_valid_columns=lambda: ['name', 'parent', 'parenttype', 'parentfield', 'company'])
        reader = namespace['_Sources'].__new__(namespace['_Sources'])
        reader.sources = {}
        frame = {'user': 'actor', 'documents': {}}
        token = self.subject._OBSERVING.set(frame)
        modules = {**self.modules, 'jifeng_erp.sample_source_reports': self.subject}
        try:
            with patch.dict(sys.modules, modules):
                returned = reader.source('Item', 'ITEM', fields={'name', 'modified'}, full=True)
        finally:
            self.subject._OBSERVING.reset(token)
        self.assertIs(returned, document)
        self.assertEqual(permissions, ['read', 'select'])
        self.assertEqual(namespace['_projection'].call_count, 1)
        self.assertEqual(namespace['_table'].call_count, 1)
        state = frame['documents']['Item', 'ITEM']
        self.assertEqual(state['state'], freeze(document))
        self.assertFalse(state['completed_source'])
        self.assertEqual(state['reads'], {
            (('modified', 'name', 'stock_uom'),
             (('defaults', 'Item Default', ('company', 'name', 'parent', 'parentfield', 'parenttype')),), 'read')})

    def test_actual_check_callback_follows_final_native_read_and_recursive_return_does_not_complete(self):
        tree = ast.parse((ROOT / 'jifeng_erp/sample_source_permissions.py').read_bytes())
        method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_check')
        events = []
        documents = ContextVar('actual_report_check_documents', default=())
        namespace = {'frappe': self.fr, '_key': self.source._key, '_AUTHORIZING': self.source._AUTHORIZING,
                     '_CHECKING_DOCUMENTS': documents, '_new': lambda doc: False,
                     '_unshared': lambda doc: events.append('unshared'),
                     '_storage_identity': lambda doc: events.append('identity'),
                     '_native_read': lambda doc, user: events.append('native-read'),
                     '_complete_fields': lambda doc: events.append('fields'),
                     '_origins': lambda doc: [], '_scope': self.source._scope,
                     '_gate': lambda doc: events.append('body'), '_deny': lambda: self.fr.throw('denied', PermissionError)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'actual_check', 'exec'), namespace)
        frame = {'user': 'actor', 'documents': {}}
        token = self.subject._OBSERVING.set(frame)
        modules = {**self.modules, 'jifeng_erp.sample_source_reports': self.subject}
        try:
            with patch.dict(sys.modules, modules):
                namespace['_check'](copy.deepcopy(self.work), 'actor')
                self.assertEqual(events[-1], 'native-read')
                self.assertTrue(frame['documents']['Work Order', 'WO']['completed_source'])
                frame['documents'].clear()
                active = self.source._AUTHORIZING.set(frozenset({self.source._key(self.work, 'actor')}))
                try:
                    namespace['_check'](copy.deepcopy(self.work), 'actor')
                finally:
                    self.source._AUTHORIZING.reset(active)
                self.assertFalse(frame['documents'])
        finally:
            self.subject._OBSERVING.reset(token)
        self.assertFalse(documents.get())
        self.assertFalse(self.source._AUTHORIZING.get())

    def test_report_roles_and_columns_live_stored_and_post_execution_are_bound(self):
        for field in ('roles', 'columns'):
            report = self.report()
            self.docs['Report', report.name][field] = [Row(value='different')]
            with self.assertRaises(PermissionError):
                report.execute_query_report({})
            self.assertFalse(report.calls)
            report = self.report()
            report.action = lambda field=field: self.docs['Report', report.name].update({field: [Row(value='changed')]})
            with self.assertRaises(PermissionError):
                report.execute_query_report({})

    def test_fixed_native_filter_validator_preserves_safe_ordinary_error(self):
        report = self.report()
        def invalid(filters):
            raise ValueError('native invalid date')
        self.subject.REPORT_SPECS[report.name]['validate_filters'] = invalid
        with self.assertRaisesRegex(ValueError, 'native invalid date'):
            report.execute_query_report({'date': 'invalid'})
        self.assertFalse(report.calls)
        self.fr.db.sql.assert_not_called()

    def test_exists_raw_contributors_and_docstatus_changes_are_frozen_without_stock_grant(self):
        report = self.report('Work Orders in Progress')
        self.raw['Stock Entry'] = [Row(name='ANY-CANCELLED', modified='v1', work_order='WO', docstatus=2)]
        report.action = lambda: self.raw['Stock Entry'][0].update(docstatus=1)
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertTrue(all(dt != 'Stock Entry' for dt, name, user in self.reads))
        calls = [call.kwargs for call in self.fr.get_all.call_args_list]
        self.assertTrue(all(value['filters'] == {'work_order': 'WO'} for value in calls))
        self.assertTrue(all('docstatus' in value['fields'] for value in calls))

    def test_source_read_contract_changes_are_frozen_and_next_report_has_no_cached_grant(self):
        report = self.report()
        prior = self.source._check.side_effect
        stage = [0]
        def changed_fields(doc, user):
            prior(doc, user)
            stage[0] += 1
            self.subject.observe_dependency(self.company,
                                            fields={'name'} if stage[0] == 1 else {'name', 'modified'}, permission='select')
        self.source._check.side_effect = changed_fields
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertIsNone(self.subject._OBSERVING.get())
        self.source._check.side_effect = prior
        self.work['readable'] = False
        fresh = self.report()
        with self.assertRaises(PermissionError):
            fresh.execute_query_report({})
        self.assertFalse(fresh.calls)

    def test_callback_changed_state_within_same_read_fails_and_frame_clears(self):
        report = self.report()
        def change(doc, user):
            self.subject.observe_dependency(self.company, permission='select')
            self.company['modified'] = 'new'
            self.subject.observe_dependency(self.company, permission='select')
        self.source._check.side_effect = change
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertIsNone(self.subject._OBSERVING.get())
        self.assertFalse(self.subject._EXECUTING.get())

    def test_native_exception_preserved_and_internal_frames_reset(self):
        report = self.report()
        def fail():
            raise ValueError('native error')
        report.action = fail
        with self.assertRaisesRegex(ValueError, 'native error'):
            report.execute_query_report({})
        self.assertIsNone(self.subject._OBSERVING.get())
        self.assertFalse(self.subject._EXECUTING.get())

    def test_legal_native_filter_normalization_same_scope_is_preserved(self):
        report, filters = self.report(), Row(value=1)
        report.action = lambda: filters.update(value=2)
        self.assertIs(report.execute_query_report(filters), report.result)
        self.assertEqual(filters.value, 2)
        self.assertIs(report.calls[0][1], filters)

    def test_actual_session_user_changed_during_execution_refuses(self):
        report = self.report()
        report.action = lambda: self.fr.session.update(user='other')
        with self.assertRaises(PermissionError):
            report.execute_query_report({})

    def test_issued_exact_join_has_no_omitted_child_or_ui_filter(self):
        report = self.issued()
        self.assertIs(report.execute_query_report({'company': 'caller', 'from_date': 'caller'}), report.result)
        query = self.fr.db.sql.call_args.args[0]
        self.assertNotIn('parenttype=', query)
        self.assertNotIn('parentfield=', query)
        self.assertNotIn('company=', query)
        self.assertNotIn('posting_date', query)
        self.assertEqual(self.fr.db.sql.call_args.kwargs, {'as_dict': True})
        self.assertIn(('Stock Entry Detail', self.subject._DETAIL_FIELDS, 'Stock Entry'), self.field_checks)

    def test_issued_unreadable_stock_entry_or_amount_mask_refuses_before_native(self):
        for target in ('native', 'mask'):
            report = self.issued()
            if target == 'native':
                self.entry['readable'] = False
            else:
                self.denied_fields.add(('Stock Entry Detail', 'amount'))
            with self.assertRaises(PermissionError):
                report.execute_query_report({})
            self.assertFalse(report.calls)

    def test_issued_alien_child_identity_or_company_refuses_whole_report(self):
        for target in ('parenttype', 'parentfield', 'company', 'wrong_work_order'):
            report = self.issued()
            if target in ('parenttype', 'parentfield'):
                self.docs['Stock Entry Detail', 'SED'][target] = 'alien'
            elif target == 'company':
                self.entry['company'] = 'OTHER'
            else:
                self.entry['work_order'] = 'OTHER-WO'
            with self.assertRaises(PermissionError):
                report.execute_query_report({})
            self.assertFalse(report.calls)

    def test_issued_ordinary_rows_do_not_gain_stock_or_bin_authorization(self):
        self.work['protected'] = False
        report = self.issued()
        self.entry['readable'], self.bin['readable'] = False, False
        self.denied_fields.add(('Stock Entry Detail', 'amount'))
        self.assertIs(report.execute_query_report({}), report.result)
        self.assertFalse(self.reads)
        self.assertFalse(self.field_checks)
        self.fr.get_all.assert_not_called()

    def test_issued_detail_amount_bin_qty_and_parent_change_refuse_post_result(self):
        for dt, name, field, value in (('Stock Entry Detail', 'SED', 'amount', 999),
                                      ('Bin', 'BIN', 'projected_qty', 999),
                                      ('Stock Entry', 'STE', 'posting_date', 'changed')):
            report = self.issued()
            report.action = lambda dt=dt, name=name, field=field, value=value: self.docs[dt, name].update({field: value})
            with self.assertRaises(PermissionError):
                report.execute_query_report({})

    def test_issued_bin_membership_changes_and_mask_are_not_silent_null(self):
        report = self.issued()
        report.action = lambda: self.raw.update(Bin=[])
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        report = self.issued()
        self.denied_fields.add(('Bin', 'projected_qty'))
        with self.assertRaises(PermissionError):
            report.execute_query_report({})
        self.assertFalse(report.calls)

    def test_scope_sql_preserves_cancelled_entry_exists_and_stopped_open(self):
        self.subject.select_open_work_orders({'company': 'caller'})
        sql = self.fr.db.sql.call_args.args[0]
        self.assertNotIn('status', sql.replace('docstatus', ''))
        self.assertNotIn('purpose', sql)
        self.assertEqual(sql.count('docstatus'), 1)
        self.subject.select_completed_work_orders({})
        self.assertIn('>=wo.qty', self.fr.db.sql.call_args.args[0])
        self.subject.select_work_orders_in_progress({})
        sql = self.fr.db.sql.call_args.args[0]
        self.assertIn("wo.status!='Stopped'", sql)
        self.assertEqual(sql.count('docstatus'), 1)
        self.assertNotIn('company', sql)


if __name__ == '__main__':
    unittest.main()
