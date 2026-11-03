from pathlib import Path
import re

def test_supabase_schema_has_balanced_dollar_quoted_functions():
    sql=(Path(__file__).resolve().parents[1]/"supabase"/"schema.sql").read_text(encoding="utf-8")
    assert sql.count("$$") % 2 == 0
    assert "as $\ndeclare" not in sql
    assert "end;\n$;" not in sql
    functions=re.findall(r"create or replace function[\s\S]*?\$\$;",sql,re.IGNORECASE)
    assert len(functions) >= 4

def test_accounting_data_is_not_persisted_in_supabase_schema():
    sql=(Path(__file__).resolve().parents[1]/"supabase"/"schema.sql").read_text(encoding="utf-8").lower()
    for forbidden in ("bank_statements","customer_invoices","accounting_journals","ledger_entries"):
        assert forbidden not in sql
