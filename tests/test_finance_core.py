import pandas as pd
from finance_core import parse_amount, categorize, transaction_fingerprint, monthly_summary, detect_recurring, forecast_scenarios, extract_pdf_transactions
import fitz

def test_parse_french_amounts():
    assert parse_amount("1 234,56 €") == 1234.56
    assert parse_amount("-45,90") == -45.90

def test_categorize_known_keyword():
    category, confidence = categorize("CARREFOUR MARKET PARIS", -42.5)
    assert category == "Alimentation"
    assert confidence >= 0.8

def test_fingerprint_is_stable_and_account_scoped():
    a = transaction_fingerprint("2024-01-01", "CARREFOUR", -10, "Courant")
    b = transaction_fingerprint("2024-01-01", "CARREFOUR", -10, "Courant")
    c = transaction_fingerprint("2024-01-01", "CARREFOUR", -10, "Livret")
    assert a == b
    assert a != c

def sample_df():
    return pd.DataFrame([
        {"date":"2024-01-10","label":"SALAIRE","amount":2500.0,"account":"Courant","category":"Revenus","needs_review":False},
        {"date":"2024-01-11","label":"CARREFOUR","amount":-100.0,"account":"Courant","category":"Alimentation","needs_review":False},
        {"date":"2024-02-10","label":"SALAIRE","amount":2500.0,"account":"Courant","category":"Revenus","needs_review":False},
        {"date":"2024-02-11","label":"CARREFOUR","amount":-120.0,"account":"Courant","category":"Alimentation","needs_review":False},
        {"date":"2024-03-10","label":"SALAIRE","amount":2500.0,"account":"Courant","category":"Revenus","needs_review":False},
        {"date":"2024-03-11","label":"CARREFOUR","amount":-110.0,"account":"Courant","category":"Alimentation","needs_review":False},
    ])

def test_monthly_summary():
    result = monthly_summary(sample_df())
    assert len(result) == 3
    assert result.iloc[0]["income"] == 2500
    assert result.iloc[0]["expenses"] == 100

def test_recurring_detected_after_three_occurrences():
    result = detect_recurring(sample_df())
    assert not result.empty
    assert "CARREFOUR" in result.iloc[0]["normalized_label"]

def test_forecast_has_requested_horizon():
    result = forecast_scenarios(sample_df(), months=12, monthly_savings_cut=50, starting_cash=1000)
    assert len(result) == 12
    assert result.iloc[-1]["optimized_balance"] > result.iloc[-1]["base_balance"]


def test_internal_transfer_is_not_income():
    category, _ = categorize("VIREMENT VERS LIVRET A", 300.0)
    assert category == "Épargne & investissements"
    generic_category, _ = categorize("VIREMENT VERS COMPTE PERSO", -50.0)
    assert generic_category == "Transfert interne potentiel"

def test_extract_pdf_text_transaction():
    # Mimic the BNP statement's separate date, label and debit columns.
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((28, 100), "01.02")
    page.insert_text((68, 100), "CARREFOUR MARKET")
    page.insert_text((311, 100), "02.02")
    page.insert_text((423, 100), "45,90")
    raw = doc.tobytes()
    doc.close()
    result = extract_pdf_transactions(raw, "BNP Courant", "test.pdf")
    assert len(result) == 1
    assert result.iloc[0]["date"].endswith("-02-01")
    assert result.iloc[0]["amount"] == -45.90
    assert result.iloc[0]["label"] == "CARREFOUR MARKET"
    assert result.iloc[0]["value_date"].endswith("-02-02")
