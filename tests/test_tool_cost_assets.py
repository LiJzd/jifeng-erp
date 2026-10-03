"""Shared native-source UI integration, without browser or network activity."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).parents[1]


class TestToolCostAssets(unittest.TestCase):
    def test_production_component_dom_protocol(self):
        result = subprocess.run(
            ["node", str(ROOT / "tests/tool_cost_component.cjs")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("one-write recovery", result.stdout)

    def test_archive_toolbar_uses_same_cost_component(self):
        archive = (ROOT / "jifeng_erp/public/js/jifeng_tool_archive.js").read_text(encoding="utf-8")
        component = (ROOT / "jifeng_erp/public/js/jifeng_tool_costs.js").read_text(encoding="utf-8")
        css = (ROOT / "jifeng_erp/public/css/jifeng_tool_costs.css").read_text(encoding="utf-8")
        self.assertIn('this.button("实际费用", () => this.openCosts(row.name)', archive)
        self.assertIn('this.button("实际费用", () => this.openCosts(state.archive.name, state))', archive)
        self.assertIn("window.JifengToolCosts.open", archive)
        self.assertIn("this.costs.discardFor", archive)
        self.assertNotIn("localStorage", component)
        self.assertNotIn("sessionStorage", component)
        self.assertNotIn("innerHTML", component)
        self.assertIn(".jfe-tool-costs [hidden]", css)


if __name__ == "__main__":
    unittest.main()
