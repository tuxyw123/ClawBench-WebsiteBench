from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from product_options import (  # noqa: E402
    UPSIMPLES_ASIN,
    load_source_option_specs,
    load_source_transaction_quote_specs,
)


FIXTURE_ROOT = ROOT / "fixtures"
APP_JS = ROOT / "static" / "app.js"
NODE_DRIVER = r"""
const fs = require("fs");
const api = require(process.argv[1]);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const selections = api.availableProductSelections(payload.quotes, payload.axes);
const states = Object.fromEntries(
  Object.entries(payload.values || {}).map(([label, values]) => [
    label,
    Object.fromEntries(values.map((value) => [
      value,
      {
        compatible: api.productOptionHasCompatibleQuote(
          selections, payload.current, payload.axes, label, value
        ),
        selectable: api.productOptionHasAnyQuote(
          selections, payload.axes, label, value
        ),
        repaired: api.repairProductSelection(
          selections, payload.current, payload.axes, label, value
        ),
      },
    ])),
  ])
);
process.stdout.write(JSON.stringify({ selections, states }));
"""


class ProductOptionCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.option_specs = load_source_option_specs(FIXTURE_ROOT)
        cls.quote_specs = load_source_transaction_quote_specs(FIXTURE_ROOT)

    def project(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = subprocess.run(
            ["node", "-e", NODE_DRIVER, str(APP_JS)],
            input=json.dumps(payload),
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_upsimples_projects_19_colors_and_disables_78_unquoted_sizes(self) -> None:
        spec = self.option_specs[UPSIMPLES_ASIN]
        axes = [str(group["label"]) for group in spec]
        values = {
            str(group["label"]): [str(value) for value in group["options"]]
            for group in spec
        }
        current = {str(group["label"]): str(group["default"]) for group in spec}
        projection = self.project(
            {
                "quotes": self.quote_specs[UPSIMPLES_ASIN],
                "axes": axes,
                "values": values,
                "current": current,
            }
        )

        self.assertEqual(axes, ["Color", "Size"])
        self.assertEqual(current, {"Color": "Black", "Size": "11x14"})
        self.assertEqual(len(projection["selections"]), 19)

        color_states = projection["states"]["Color"]
        self.assertEqual(len(color_states), 19)
        self.assertTrue(all(state["compatible"] for state in color_states.values()))
        self.assertTrue(all(state["selectable"] for state in color_states.values()))
        self.assertEqual(
            color_states["Blue"]["repaired"],
            {"Color": "Blue", "Size": "11x14"},
        )

        size_states = projection["states"]["Size"]
        self.assertEqual(len(size_states), 79)
        self.assertTrue(size_states["11x14"]["compatible"])
        self.assertTrue(size_states["11x14"]["selectable"])
        unavailable_sizes = [
            state for value, state in size_states.items() if value != "11x14"
        ]
        self.assertEqual(len(unavailable_sizes), 78)
        self.assertTrue(all(not state["compatible"] for state in unavailable_sizes))
        self.assertTrue(all(not state["selectable"] for state in unavailable_sizes))
        self.assertTrue(all(state["repaired"] is None for state in unavailable_sizes))

    def test_disconnected_quote_matrix_remains_reachable_by_minimal_repair(self) -> None:
        quotes = [
            self.quote({"Color": "Black", "Size": "1TB", "Bundle": "Drive only"}, 100),
            self.quote({"Color": "Blue", "Size": "2TB", "Bundle": "Drive only"}, 200),
            self.quote({"Color": "Blue", "Size": "3TB", "Bundle": "With case"}, 300),
        ]
        current = {"Color": "Black", "Size": "1TB", "Bundle": "Drive only"}
        projection = self.project(
            {
                "quotes": quotes,
                "axes": ["Color", "Size", "Bundle"],
                "values": {
                    "Color": ["Black", "Blue", "Green"],
                    "Size": ["1TB", "2TB", "3TB", "4TB"],
                    "Bundle": ["Drive only", "With case"],
                },
                "current": current,
            }
        )

        blue = projection["states"]["Color"]["Blue"]
        self.assertFalse(blue["compatible"])
        self.assertTrue(blue["selectable"])
        self.assertEqual(
            blue["repaired"],
            {"Color": "Blue", "Size": "2TB", "Bundle": "Drive only"},
        )
        two_tb = projection["states"]["Size"]["2TB"]
        self.assertFalse(two_tb["compatible"])
        self.assertTrue(two_tb["selectable"])
        self.assertEqual(two_tb["repaired"], blue["repaired"])
        self.assertFalse(projection["states"]["Color"]["Green"]["selectable"])
        self.assertFalse(projection["states"]["Size"]["4TB"]["selectable"])
        self.assertIn(blue["repaired"], projection["selections"])

    def test_only_complete_available_priced_quotes_enter_the_projection(self) -> None:
        quotes = [
            self.quote({"Color": "Black", "Size": "1TB"}, 100),
            {**self.quote({"Color": "Blue", "Size": "2TB"}, 200), "availability": "UNAVAILABLE"},
            {**self.quote({"Color": "Green", "Size": "3TB"}, 300), "price_minor": None},
            {**self.quote({"Color": "Red", "Size": "4TB"}, 400), "currency": ""},
            self.quote({"Color": "White"}, 500),
            self.quote({"Color": "Gold", "Size": "5TB", "Bundle": "Case"}, 600),
        ]
        projection = self.project(
            {
                "quotes": quotes,
                "axes": ["Color", "Size"],
                "values": {"Color": ["Black", "Blue"], "Size": ["1TB", "2TB"]},
                "current": {"Color": "Black", "Size": "1TB"},
            }
        )
        self.assertEqual(
            projection["selections"],
            [{"Color": "Black", "Size": "1TB"}],
        )
        self.assertFalse(projection["states"]["Color"]["Blue"]["selectable"])

    def test_browser_binding_uses_native_disabled_aria_and_quote_owned_repair(self) -> None:
        app = APP_JS.read_text(encoding="utf-8")
        self.assertIn("productOptionHasCompatibleQuote", app)
        self.assertIn("productOptionHasAnyQuote", app)
        self.assertIn("repairProductSelection", app)
        self.assertIn("control.disabled = !selectable", app)
        self.assertIn('control.setAttribute("aria-disabled", selectable ? "false" : "true")', app)
        self.assertIn("option.disabled = !selectable", app)
        self.assertIn("option.dataset.optionRequiresRepair", app)
        self.assertIn("commitProductSelection(repairedSelection)", app)
        self.assertNotIn("selectedProductOptions = { ...selectedProductOptions, [label]: value }", app)

    @staticmethod
    def quote(selection: dict[str, str], price_minor: int) -> dict[str, Any]:
        return {
            "selected_options": selection,
            "price_minor": price_minor,
            "currency": "USD",
            "availability": "AVAILABLE",
        }


if __name__ == "__main__":
    unittest.main()
