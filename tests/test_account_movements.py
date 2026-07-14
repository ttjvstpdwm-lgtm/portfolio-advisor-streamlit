import io
import unittest

import pandas as pd

from account_movements import (
    category_outflows,
    monthly_cashflow,
    movement_kpis,
    parse_current_account_excel,
    top_movements,
)


class AccountMovementsImportTest(unittest.TestCase):
    def test_parse_current_account_excel_income_events(self):
        rows = [
            ["Conto Corrente: 123", None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            [
                "Data_Operazione",
                "Data_Valuta",
                "Entrate",
                "Uscite",
                "Descrizione",
                "Descrizione_Completa",
                "Stato",
                "Moneymap",
            ],
            [
                pd.Timestamp("2026-05-22"),
                pd.Timestamp("2026-05-22"),
                184.03,
                None,
                "Stacco Cedole Italia",
                "Ced.su 10.000,000 BTP-22NV28 ITALIACUM",
                "Contabilizzato",
                "Investimenti",
            ],
            [
                pd.Timestamp("2026-05-22"),
                pd.Timestamp("2026-05-22"),
                None,
                -23.01,
                "Ritenuta su Cedole",
                "Rit.ced.su 10.000,000 BTP-22NV28 ITALIAC UM",
                "Contabilizzato",
                "Tasse e tributi",
            ],
            [
                pd.Timestamp("2026-05-20"),
                pd.Timestamp("2026-05-20"),
                510.00,
                None,
                "Div.lordo Port.Remunerato",
                "Acc.div.Port.Rem. SAIPEM",
                "Contabilizzato",
                "Investimenti",
            ],
            [
                pd.Timestamp("2026-05-20"),
                pd.Timestamp("2026-05-20"),
                None,
                -132.60,
                "Ritenuta div. Port. Remunerato",
                "Add.rit.Port.Rem. SAIPEM",
                "Contabilizzato",
                "Tasse e tributi",
            ],
        ]
        raw = pd.DataFrame(rows)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            raw.to_excel(writer, sheet_name="Movimenti", header=False, index=False)

        income, movements = parse_current_account_excel(buffer.getvalue(), "movements.xlsx")

        self.assertEqual(len(movements), 4)
        self.assertEqual(len(income), 2)
        self.assertEqual(set(income["Stato"]), {"Completo"})

        btp = income[income["Strumento"].str.contains("BTP", na=False)].iloc[0]
        self.assertAlmostEqual(float(btp["Lordo"]), 184.03)
        self.assertAlmostEqual(float(btp["Ritenuta"]), 23.01)
        self.assertAlmostEqual(float(btp["Netto"]), 161.02)

        saipem = income[income["Strumento"].eq("SAIPEM")].iloc[0]
        self.assertAlmostEqual(float(saipem["Lordo"]), 510.00)
        self.assertAlmostEqual(float(saipem["Ritenuta"]), 132.60)
        self.assertAlmostEqual(float(saipem["Tax rate"]), 0.26)

        kpis = movement_kpis(movements)
        self.assertEqual(kpis["movement_count"], 4)
        self.assertAlmostEqual(kpis["inflows"], 694.03)
        self.assertAlmostEqual(kpis["outflows"], 155.61)
        self.assertAlmostEqual(kpis["net_cashflow"], 538.42)
        self.assertAlmostEqual(kpis["operating_outflows"], 155.61)
        self.assertAlmostEqual(kpis["non_operating_outflows"], 0.0)

        monthly = monthly_cashflow(movements)
        self.assertEqual(len(monthly), 1)
        self.assertAlmostEqual(float(monthly.iloc[0]["Saldo netto"]), 538.42)

        categories = category_outflows(movements)
        self.assertIn("Tasse e tributi", categories["Categoria"].tolist())

        self.assertEqual(top_movements(movements, "out", n=1).iloc[0]["Uscite"], -132.60)
        self.assertEqual(top_movements(movements, "in", n=1).iloc[0]["Entrate"], 510.00)


if __name__ == "__main__":
    unittest.main()
