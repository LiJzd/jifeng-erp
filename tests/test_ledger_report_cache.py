"""Candidate-source models only; no native DB, queue, HTTP or service proof."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


OWN = Path(__file__).parents[1]
PACKAGE = OWN / 'candidate/jifeng_erp'
if (OWN / 'jifeng_erp').is_dir():
    PACKAGE = OWN / 'jifeng_erp'
FRESH = (PACKAGE if (PACKAGE / 'ledger_report_output.py').is_file() else
         Path('D:/codex-temp/native-ledger-fresh-output-20261006-r1/candidate/jifeng_erp'))
LEDGER = 'Jifeng Purchase Receipt Ledger'


class Row(dict):
    __getattr__ = dict.get
    def __setattr__(self, key, value):
        self[key] = value
    def is_new(self):
        return bool(self.get('__new'))
    def as_dict(self):
        return dict(self)


def freeze(value):
    if isinstance(value, dict):
        return tuple((key, freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    return value


class CacheModels(unittest.TestCase):
    def setUp(self):
        self.docs, self.events, self.artifact_roots, self.shares = {}, [], {}, []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site = Path(self.tmp.name)
        (self.site / 'private/files').mkdir(parents=True)
        (self.site / 'public/files').mkdir(parents=True)
        self.fr = fr = types.ModuleType('frappe')
        fr._dict, fr.PermissionError, fr.session = Row, PermissionError, Row(user='actor')
        fr.throw = lambda msg, error=ValueError, **kwargs: (_ for _ in ()).throw(error(msg))
        fr.whitelist = lambda: lambda fn: fn
        fr.read_only = lambda: lambda fn: fn
        fr.parse_json = lambda value: json.loads(value) if isinstance(value, str) else value
        fr._ = lambda value: value
        fr.local = Row(form_dict=Row())
        fr.get_site_path = lambda *parts: str(self.site.joinpath(*parts))
        fr.enqueue = Mock()
        fr.permissions = Row(can_export=Mock())
        fr.respond_as_web_page = Mock()
        fr.db = Row(exists=lambda dt, name: (any(row.share_doctype == name.get('share_doctype')
                                                and row.share_name == name.get('share_name')
                                                for row in self.shares) if dt == 'DocShare'
                                           else (dt, name) in self.docs),
                    rollback=Mock(), escape=repr)
        test = self

        class NativeFile(Row):
            def get_full_path(doc):
                value = doc.get('file_url') or doc.get('file_name')
                if value.startswith(('http://', 'https://', '/api/method/')):
                    return value
                if value.startswith('/private/files/'):
                    return str(test.site / 'private/files' / value.removeprefix('/private/files/'))
                if value.startswith('/files/'):
                    return str(test.site / 'public/files' / value.removeprefix('/files/'))
                return str(test.site / ('private/files' if doc.get('is_private') else 'public/files') / value)
            def has_permission(doc, *args, **kwargs):
                test.events.append('native_permission')
                return doc.get('native_allowed', True)
            def is_downloadable(doc):
                return doc.has_permission()
            def get_content(doc, encodings=None):
                test.events.append('native_bytes')
                if test.on_read:
                    test.on_read()
                return b'ordinary'
            def on_trash(doc):
                test.events.append('native_unlink')

        self.NativeFile, self.on_read = NativeFile, None
        def get_doc(dt=None, name=None, **kwargs):
            construct = dt is None
            if dt is None:
                dt = kwargs.pop('doctype')
            if isinstance(dt, dict):
                return Row(copy.deepcopy(dt))
            if name is not None and not construct:
                return copy.deepcopy(self.docs[dt, name])
            if name is not None:
                kwargs['name'] = name
            return (self.File if dt == 'File' and hasattr(self, 'File') else Row)(doctype=dt, **kwargs)
        fr.get_doc = get_doc
        def get_all(dt, **kwargs):
            if dt == 'DocShare':
                rows = copy.deepcopy(self.shares)
            else:
                rows = [copy.deepcopy(doc) for (kind, _name), doc in self.docs.items() if kind == dt]
            for field, value in (kwargs.get('filters') or {}).items():
                if isinstance(value, (tuple, list)) and value[0] == 'in':
                    rows = [doc for doc in rows if doc.get(field) in value[1]]
                else:
                    rows = [doc for doc in rows if doc.get(field) == value]
            if kwargs.get('limit_page_length'):
                rows = rows[:kwargs['limit_page_length']]
            fields = kwargs.get('fields')
            return ([Row({key: doc.get(key) for key in fields}) for doc in rows]
                    if fields and fields != ['*'] else rows)
        fr.get_all = get_all
        app = types.ModuleType('jifeng_erp')
        app.__path__ = [str(PACKAGE), str(FRESH)]
        guard = types.ModuleType('jifeng_erp.ledger_source_guard')
        guard.LedgerSourceGuard, guard._freeze, guard.output_guard = object, freeze, Mock()
        scripts = types.ModuleType('jifeng_erp.sample_source_report_scripts')
        scripts._native_module = Mock()
        orders = types.ModuleType('jifeng_erp.sample_orders')
        orders._freeze = freeze
        artifacts = types.ModuleType('jifeng_erp.sample_source_report_artifacts')
        artifacts.bound_roots = lambda doc: self.artifact_roots.get(doc.get('name'), [])
        artifacts.resolve_report = Mock(return_value=None)
        artifacts.authorize_file_write = lambda doc: False
        artifacts.current_generation = lambda: None
        artifacts.artifacts_for_prepared = lambda name: []
        file_native = types.ModuleType('frappe.core.doctype.file.file')
        file_native.File, file_native.URL_PREFIXES = NativeFile, ('http://', 'https://', '/api/method/')
        desk = types.ModuleType('frappe.desk.utils')
        desk.pop_csv_params = lambda form: {}
        desk.provide_binary_file = Mock()
        self.desk, self.artifacts = desk, artifacts
        modules = {'frappe': fr, 'resource': types.ModuleType('resource'),
                   'jifeng_erp': app, guard.__name__: guard,
                   scripts.__name__: scripts, orders.__name__: orders, artifacts.__name__: artifacts,
                   'frappe.core': types.ModuleType('frappe.core'),
                   'frappe.core.doctype': types.ModuleType('frappe.core.doctype'),
                   'frappe.core.doctype.file': types.ModuleType('frappe.core.doctype.file'),
                   'frappe.core.doctype.prepared_report': types.ModuleType('frappe.core.doctype.prepared_report'),
                   'frappe.core.doctype.prepared_report.prepared_report': types.ModuleType('frappe.core.doctype.prepared_report.prepared_report'),
                   file_native.__name__: file_native, 'frappe.desk': types.ModuleType('frappe.desk'),
                   desk.__name__: desk}
        self.patch = patch.dict(sys.modules, modules)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.fresh = self.load('ledger_report_output', FRESH)
        self.cache = self.load('ledger_report_cache')
        self.files = self.load('sample_source_report_files')
        self.prepared = self.load('sample_source_prepared_reports')
        self.output = self.load('sample_source_report_output')
        self.File = type('File', (self.files.SampleSourceReportFileMixin, NativeFile), {})
        self.report(LEDGER)
        self.report('Ordinary')
        self.report('Alias', 'Custom Report', LEDGER)
        self.report('Outer', 'Custom Report', 'Alias')
        self.put('Prepared Report', 'PR', report_name='Outer', owner='actor', status='Completed')

    def load(self, slug, directory=PACKAGE):
        name = 'jifeng_erp.' + slug
        spec = importlib.util.spec_from_file_location(name, directory / (slug + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(module)
        return module

    def put(self, dt, name, **values):
        doc = Row(doctype=dt, name=name, **values)
        self.docs[dt, name] = doc
        return copy.deepcopy(doc)

    def report(self, name, kind='Script Report', reference=None):
        return self.put('Report', name, report_type=kind, reference_report=reference)

    def file(self, name, *, parenttype=None, parent=None, url='/private/files/one', private=1):
        doc = self.File(doctype='File', name=name, file_name='one', file_url=url,
                        is_private=private, is_folder=0, attached_to_doctype=parenttype,
                        attached_to_name=parent, content_hash='not-provenance')
        self.docs['File', name] = copy.deepcopy(doc)
        return doc

    def test_classifier_actual_candidate_walks_two_custom_links_without_actor_gate(self):
        self.fr.session.user = 'Guest'
        self.assertEqual(self.fresh.resolve_target('Outer').canonical_name, LEDGER)
        self.assertIsNone(self.fresh.resolve_target('Ordinary'))

    def test_classifier_missing_cycle_and_canonical_metadata_tamper_fail_closed(self):
        self.report('Loop', 'Custom Report', 'Loop')
        with self.assertRaises(PermissionError): self.fresh.resolve_target('Loop')
        with self.assertRaises(KeyError): self.fresh.resolve_target('missing')
        self.report(LEDGER, 'Query Report')
        self.assertTrue(self.cache.is_ledger_report(LEDGER))

    def test_persisted_prepared_union_rejects_forged_ordinary_name(self):
        doc = copy.deepcopy(self.docs['Prepared Report', 'PR'])
        doc.report_name = 'Ordinary'
        with self.assertRaises(PermissionError): self.cache.deny_prepared(doc)
        self.assertFalse(self.prepared.prepared_report_has_permission(doc))

    def test_new_prepared_insert_and_csv_enqueue_rejected_before_side_effect(self):
        new = Row(doctype='Prepared Report', report_name='Outer', __new=True)
        with self.assertRaises(PermissionError): self.prepared.before_validate_prepared_report(new)
        with self.assertRaises(PermissionError): self.output.enqueue_json_to_csv_conversion('PR')
        self.fr.enqueue.assert_not_called()
        self.artifacts.resolve_report.assert_not_called()

    def test_modified_prepared_object_cannot_read_or_share_through_ordinary_saved_parent(self):
        self.put('Prepared Report', 'ordinary', report_name='Ordinary', owner='actor')
        cls = type('Prepared', (self.prepared.SampleSourcePreparedReportMixin, Row), {})
        doc = cls(self.docs['Prepared Report', 'ordinary'])
        doc.report_name = 'Outer'
        self.assertFalse(doc.has_permission())
        with self.assertRaises(PermissionError): doc.get_prepared_data()
        with self.assertRaises(PermissionError): self.prepared.validate_prepared_report_share(doc)

    def test_old_native_and_owned_workers_block_before_job_body(self):
        for method, key in (
            ('frappe.core.doctype.prepared_report.prepared_report.generate_report', 'prepared_report'),
            ('frappe.core.doctype.prepared_report.prepared_report.convert_json_to_csv', 'prepared_report_name'),
            ('jifeng_erp.sample_source_prepared_reports.generate_report', 'prepared_report'),
            ('jifeng_erp.sample_source_report_output.convert_json_to_csv', 'prepared_report_name')):
            with self.assertRaises(PermissionError): self.output.before_job(method, {key: 'PR'})
        self.prepared._native = Mock(side_effect=AssertionError('no native generator'))
        self.prepared._save_status = Mock(side_effect=AssertionError('no status write'))
        for status in ('Queued', 'Started', 'Completed', 'Error', 'Cancelled'):
            with self.subTest(status=status):
                self.docs['Prepared Report', 'PR'].status = status
                before = copy.deepcopy(self.docs)
                self.fr.db.rollback.reset_mock()
                self.assertIsNone(self.prepared.generate_report('PR'))
                self.fr.db.rollback.assert_called_once_with()
                self.assertEqual(self.docs, before)
                self.artifacts.resolve_report.assert_not_called()
                self.prepared._native.assert_not_called()
                self.prepared._save_status.assert_not_called()

    def test_export_jobs_rejected_for_custom_alias_but_ordinary_untouched(self):
        for method in ('frappe.desk.query_report.run_export_query_job',
                       'jifeng_erp.sample_source_report_output.run_export_query_job'):
            with self.assertRaises(PermissionError):
                self.cache.before_job(method, {'form_params': {'report_name': 'Outer'}})
        self.cache.before_job('other.ordinary_job', {})
        self.cache.before_job('frappe.desk.query_report.run_export_query_job',
                              {'form_params': {'report_name': 'Ordinary'}})

    def test_cached_file_direct_read_download_and_native_unlink_preempted(self):
        doc = self.file('protected', parenttype='Prepared Report', parent='PR')
        with self.assertRaises(PermissionError): doc.get_content()
        self.assertFalse(doc.has_permission())
        self.assertFalse(doc.is_downloadable())
        with self.assertRaises(PermissionError): doc.on_trash()
        self.assertNotIn('native_bytes', self.events)
        self.assertNotIn('native_unlink', self.events)

    def test_ordinary_and_known_ten_delete_do_not_read_receipt_or_bytes(self):
        self.report('Open Work Orders')
        ordinary = self.file('ordinary', url='/private/files/ordinary')
        known = self.file('known', parenttype='Report', parent='Open Work Orders',
                          url='/private/files/known')
        self.artifact_roots['known'] = ['known-receipt']
        self.put('Jifeng Report Artifact', 'known-receipt', report='Open Work Orders')
        self.files._checked_capture = Mock(side_effect=AssertionError('no legacy read during trash'))
        self.files._raw_bytes = Mock(side_effect=AssertionError('no bytes during trash'))
        self.artifacts.check_artifact = Mock(side_effect=AssertionError('no receipt consumption'))
        ordinary.on_trash()
        known.on_trash()
        self.assertEqual(self.events, ['native_unlink', 'native_unlink'])
        self.files._checked_capture.assert_not_called()
        self.files._raw_bytes.assert_not_called()
        self.artifacts.check_artifact.assert_not_called()

    def test_file_physical_alias_union_cannot_escape_parent_or_hash(self):
        self.file('protected', parenttype='Report', parent='Outer')
        alias = self.file('alias')
        with self.assertRaises(PermissionError): alias.get_content()
        with self.assertRaises(PermissionError): alias.on_trash()
        detached = copy.deepcopy(self.docs['File', 'protected'])
        detached.attached_to_name, detached.attached_to_doctype = None, None
        with self.assertRaises(PermissionError): self.files._checked_capture(detached)

    def test_future_decoded_url_same_physical_alias_is_rejected_before_native_insert(self):
        self.file('protected', parenttype='Report', parent='Outer')
        alias = self.file('encoded', url='/private/files/%6fne')
        with self.assertRaises(PermissionError): alias.before_insert()
        self.assertNotIn('native_unlink', self.events)

    def test_native_file_query_sharing_and_new_attachment_write_cannot_publish_cache(self):
        doc = self.file('protected', parenttype='Report', parent='Outer')
        self.assertIn('protected', self.files.report_file_query_conditions())
        with self.assertRaises(PermissionError): doc.before_validate()
        with self.assertRaises(PermissionError): doc.validate_share(Row())
        share = Row(share_doctype='File', share_name='protected')
        with self.assertRaises(PermissionError): self.files.prevent_report_file_docshare(share)
        self.shares = [share]
        with self.assertRaises(PermissionError): self.files.report_file_query_conditions()

    def test_distinct_physical_file_same_hash_and_ordinary_permission_keep_native(self):
        self.file('protected', parenttype='Report', parent=LEDGER)
        ordinary = self.file('ordinary', url='/private/files/two')
        self.assertEqual(ordinary.get_content(), b'ordinary')
        self.assertTrue(ordinary.has_permission())
        ordinary.native_allowed = False
        self.assertFalse(ordinary.has_permission())

    def test_receipt_binding_alone_cannot_grant_cached_ledger_file(self):
        doc = self.file('protected')
        self.artifact_roots['protected'] = ['sealed']
        self.put('Jifeng Report Artifact', 'sealed', report='Outer')
        with self.assertRaises(PermissionError): doc.get_content()

    def test_late_ordinary_file_reclassification_rejected_after_native_bytes(self):
        doc = self.file('ordinary')
        self.on_read = lambda: self.docs['File', 'ordinary'].update(
            attached_to_doctype='Report', attached_to_name='Outer')
        with self.assertRaises(PermissionError): doc.get_content()
        self.assertEqual(self.events, ['native_bytes'])

    def test_report_binding_cannot_turn_custom_chain_ordinary_delete_or_rename(self):
        doc = copy.deepcopy(self.docs['Report', 'Alias'])
        doc.reference_report = 'Ordinary'
        with self.assertRaises(PermissionError): self.cache.before_validate_report(doc)
        with self.assertRaises(PermissionError): self.cache.prevent_report_delete_or_rename(doc)
        ordinary = self.docs['Report', 'Ordinary']
        self.cache.before_validate_report(ordinary)
        self.cache.prevent_report_delete_or_rename(ordinary)

    def test_same_canonical_alias_columns_and_filters_remain_editable(self):
        doc = copy.deepcopy(self.docs['Report', 'Outer'])
        doc.reference_report = LEDGER
        doc.json = '{"columns":[],"filters":[]}'
        self.cache.before_validate_report(doc)

    def test_unused_alias_can_repoint_rename_delete_despite_unrelated_cached_outputs(self):
        self.report('Unused', 'Custom Report', LEDGER)
        unused = self.docs['Report', 'Unused']
        self.cache.prevent_report_delete_or_rename(unused)
        doc = copy.deepcopy(unused)
        doc.reference_report = 'Ordinary'
        self.cache.before_validate_report(doc)

    def test_outer_alias_file_or_receipt_keeps_inner_chain_bound(self):
        self.docs.pop(('Prepared Report', 'PR'))
        original = copy.deepcopy(self.docs['Report', 'Alias'])
        changed = copy.deepcopy(original)
        changed.reference_report = 'Ordinary'
        self.file('outer-cache', parenttype='Report', parent='Outer')
        with self.assertRaises(PermissionError): self.cache.before_validate_report(changed)
        with self.assertRaises(PermissionError): self.cache.prevent_report_delete_or_rename(original)
        self.docs.pop(('File', 'outer-cache'))
        self.put('Jifeng Report Artifact', 'receipt', report='Outer')
        with self.assertRaises(PermissionError): self.cache.before_validate_report(changed)
        with self.assertRaises(PermissionError): self.cache.prevent_report_delete_or_rename(original)

    def test_canonical_five_binding_and_identity_fixed_without_cached_outputs(self):
        self.docs.pop(('Prepared Report', 'PR'))
        canonical = self.docs['Report', LEDGER]
        with self.assertRaises(PermissionError): self.cache.prevent_report_delete_or_rename(canonical)
        changed = copy.deepcopy(canonical)
        changed.report_type = 'Query Report'
        with self.assertRaises(PermissionError): self.cache.before_validate_report(changed)
        changed = copy.deepcopy(canonical)
        changed.prepared_report = 1
        changed.columns = []
        self.cache.before_validate_report(changed)

    def test_private_public_and_share_release_preflight_no_repair(self):
        self.file('protected', parenttype='Report', parent='Outer')
        self.cache.assert_ledger_output_publication_invariant()
        before = copy.deepcopy(self.docs)
        self.shares = [Row(share_doctype='File', share_name='protected')]
        with self.assertRaises(PermissionError): self.cache.assert_ledger_output_publication_invariant()
        self.shares = []
        self.docs['File', 'protected'].update(is_private=0, file_url='/files/one')
        with self.assertRaises(PermissionError): self.cache.assert_ledger_output_publication_invariant()
        self.assertEqual(before['Report', 'Outer'], self.docs['Report', 'Outer'])

    def test_prepared_delete_preempts_native_queue_side_effect(self):
        test = self
        class NativePrepared(Row):
            def on_trash(doc): test.events.append('native_queue_stop')
        cls = type('Prepared', (self.prepared.SampleSourcePreparedReportMixin, NativePrepared), {})
        doc = cls(self.docs['Prepared Report', 'PR'])
        with self.assertRaises(PermissionError): doc.on_trash()
        self.assertNotIn('native_queue_stop', self.events)

    def test_synchronous_export_passes_formatter_inside_batch1_frame_contract(self):
        self.fr.local.form_dict = Row(report_name='Outer', filters={'company': 'fixture'},
                                      file_format_type='CSV', export_in_background=0)
        native = Row(clean_params=lambda form: None, parse_json=lambda form: None)
        self.output._native = lambda: native
        self.fresh.resolve_report = Mock(return_value=Row(report=Row(ref_doctype='Purchase Receipt')))
        active = []
        def generate(*args, transform=None, **kwargs):
            active.append(True)
            try: return transform({'columns': [], 'result': []})
            finally: active.pop()
        def formatter(*args):
            self.assertEqual(active, [True])
            return 'report', 'csv', b'bytes'
        self.fresh.generate_fresh = Mock(side_effect=generate)
        self.output._format_export = formatter
        self.cache.export_query()
        self.desk.provide_binary_file.assert_called_once_with('report', 'csv', b'bytes')
        self.fr.local.form_dict.export_in_background = 1
        with self.assertRaises(PermissionError): self.cache.export_query()
        self.assertEqual(self.fresh.generate_fresh.call_count, 1)
        self.fr.enqueue.assert_not_called()


if __name__ == '__main__':
    unittest.main()
