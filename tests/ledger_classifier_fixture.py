"""Load the actual classifier AST without unrelated native module imports."""
import ast
from dataclasses import dataclass
from pathlib import Path
import types


def classifier(frappe):
    source = Path(__file__).parents[1] / "jifeng_erp/ledger_report_output.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    selected = [node for node in tree.body if
                isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "LEDGER_REPORTS" for target in node.targets)
                or isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in {"Target", "_require", "resolve_target"}]
    module = types.ModuleType("jifeng_erp.ledger_report_output")
    module.frappe, module.dataclass = frappe, dataclass
    # dataclass needs a real registered module. These old test fixtures only
    # classify ordinary reports, so their Target is never instantiated.
    selected = [node for node in selected if not isinstance(node, ast.ClassDef)]
    module.Target = lambda *args: types.SimpleNamespace(requested_name=args[0], canonical_name=args[1], kind=args[2], chain=args[3])
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), vars(module))
    return module
