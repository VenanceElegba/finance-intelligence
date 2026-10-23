"""Fonctions pures pour l'analyse locale de transactions."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date
from typing import Iterable

import pandas as pd
import fitz

DATE_RE = re.compile(r"^\s*(\d{2}[/.]\d{2}[/.]\d{2,4})\s+(.*?)\s*$")
AMOUNT_RE = re.compile(r"(?<!\w)(-?\s*\d{1,3}(?:[ .]\d{3})*(?:,\d{2})|-?\s*\d+(?:,\d{2})?)\s*(?:€|EUR)?\s*$", re.I)

CATEGORY_RULES = {
    "Logement": ["LOYER", "BAILLEUR", "CHARGES LOCATIVES", "EDF", "ENGIE", "TOTALENERGIES"],
    "Alimentation": ["CARREFOUR", "AUCHAN", "LIDL", "MONOPRIX", "ALDI", "LECLERC", "FRANPRIX", "INTERMARCHE"],
    "Transport": ["NAVIGO", "RATP", "SNCF", "UBER", "BOLT", "TOTAL", "ESSO", "SHELL"],
    "Télécom & Internet": ["ORANGE", "SFR", "BOUYGUES", "FREE MOBILE", "FREEBOX"],
    "Abonnements & logiciels": ["NETFLIX", "SPOTIFY", "AMAZON PRIME", "APPLE.COM/BILL", "GOOGLE", "MICROSOFT"],
    "Santé": ["PHARMACIE", "DOCTOLIB", "MUTUELLE", "CPAM"],
    "Famille & enfant": ["CRECHE", "CRÈCHE", "KINDER", "BEBE", "BÉBÉ"],
    "Restaurants & loisirs": ["RESTAURANT", "DELIVEROO", "UBER EATS", "JUST EAT", "CINEMA", "CINÉMA"],
    "Banque & assurances": ["COTISATION", "COMMISSIONS", "FRAIS BANCAIRES", "ASSURANCE", "CARDIF", "BNP PARIBAS PROTECTION"],
    "Crédit & intérêts": ["ECHEANCE PRET", "ÉCHÉANCE PRÊT", "PRELEVEMENT CREDIT", "INTERETS DEBITEURS", "INTÉRÊTS"],
    "Impôts & administration": ["DGFIP", "TRESOR PUBLIC", "TRÉSOR PUBLIC", "IMPOT", "IMPÔT", "URSSAF"],
    "Épargne & investissements": ["LIVRET", "PEA", "ASSURANCE VIE", "BOURSE", "SGI", "VIREMENT VERS EPARGNE"],
}

INCOME_HINTS = ["SALAIRE", "PAYE", "PAIE", "VIREMENT RECU", "VIREMENT REÇU", "CAF", "REMBOURSEMENT", "INDEMNITE", "INDEMNITÉ"]
TRANSFER_HINTS = ["VIREMENT", "TRANSFERT", "LIVRET", "EPARGNE", "ÉPARGNE", "COMPTE A COMPTE", "COMPTE À COMPTE"]
REVIEW_CATEGORY = "À vérifier"

def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value).strip().upper()

def parse_amount(value: str) -> float | None:
    s = value.replace("€", "").replace("EUR", "").replace(" ", "").replace("\u00a0", "")
    # Les espaces et points peuvent être séparateurs de milliers ; la virgule est décimale.
    s = s.replace(".", "").replace(",", ".")
    try:
        return round(float(s), 2)
    except (TypeError, ValueError):
        return None

def categorize(label: str, amount: float) -> tuple[str, float]:
    """Retourne (catégorie, confiance indicative entre 0 et 1)."""
    norm = normalize_text(label)
    for category, keywords in CATEGORY_RULES.items():
        if any(normalize_text(k) in norm for k in keywords):
            return category, 0.88
    if any(normalize_text(k) in norm for k in TRANSFER_HINTS):
        return "Transfert interne potentiel", 0.55
    if any(normalize_text(k) in norm for k in INCOME_HINTS) or amount > 0:
        return ("Revenus" if amount > 0 else REVIEW_CATEGORY), (0.68 if amount > 0 else 0.25)
    if amount < 0:
        return REVIEW_CATEGORY, 0.20
    return REVIEW_CATEGORY, 0.20

def transaction_fingerprint(tx_date: str, label: str, amount: float, account: str) -> str:
    # Compte inclus pour éviter de fusionner des opérations de comptes distincts.
    raw = "|".join([str(tx_date), normalize_text(label), f"{float(amount):.2f}", normalize_text(account)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _parse_date(s: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d/%m/%y", "%d.%m.%y"):
        try:
            return date.strptime(s, fmt).isoformat()  # date.strptime is not available on all supported Python versions
        except (ValueError, AttributeError):
            pass
    from datetime import datetime
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d/%m/%y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None

def extract_pdf_transactions(pdf_bytes: bytes, account: str, source_name: str = "upload.pdf") -> pd.DataFrame:
    """Extrait les opérations d'un PDF BNP texte, en s'appuyant sur les coordonnées des colonnes.

    La mise en page BNP place date, date de valeur, débit/crédit et libellé dans
    des colonnes séparées. L'extraction par coordonnées évite de dépendre de
    l'ordre de lecture du texte PDF. Les opérations restent à contrôler.
    """
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        # PyMuPDF pages dépendent du document ouvert : on copie le texte et les mots
        # pendant que le document est encore ouvert.
        full_text = "\n".join(page.get_text("text") for page in doc)
        page_words = [page.get_text("words") for page in doc]

    # L'année est récupérée depuis la période du relevé quand les dates sont DD.MM.
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", full_text)]
    default_year = years[0] if years else date.today().year

    # Détermine l'année du début de période à partir de la date "du ... au ...".
    period_match = re.search(
        r"\bdu\s+\d{1,2}\s+\w+\s+(20\d{2})\s+au\s+\d{1,2}\s+\w+\s+(20\d{2})",
        normalize_text(full_text),
        flags=re.I,
    )
    if period_match:
        default_year = int(period_match.group(1))

    rows = []
    for words in page_words:
        # Chaque tuple words contient x0,y0,x1,y1,texte,bloc,ligne,mot.
        words = sorted(words, key=lambda w: (w[1], w[0]))
        line_groups = []
        for word in words:
            x0, y0, x1, y1, token = word[:5]
            if not line_groups or abs(y0 - line_groups[-1]["y"]) > 2.0:
                line_groups.append({"y": y0, "words": [word]})
            else:
                line_groups[-1]["words"].append(word)
                line_groups[-1]["y"] = sum(w[1] for w in line_groups[-1]["words"]) / len(line_groups[-1]["words"])

        current = None
        for group in line_groups:
            line_words = sorted(group["words"], key=lambda w: w[0])
            # Ignore lines above/below the table, and summary rows.
            left_date = next(
                (w[4] for w in line_words if w[0] < 60 and re.fullmatch(r"\d{2}[./]\d{2}", w[4])),
                None,
            )
            tokens = [w[4] for w in line_words]
            normalized_line = normalize_text(" ".join(tokens))
            # Ignore legal/footer text, which otherwise could be appended to the
            # final transaction of a page.
            if group["y"] > 680 or "BNP PARIBAS SA AU CAPITAL" in normalized_line or "SCPT" in normalized_line:
                if current and current.get("amount") is not None and current.get("label_parts"):
                    rows.append(current)
                current = None
                continue
            if "TOTAL DES OPERATIONS" in normalized_line or "SOLDE CREDITEUR" in normalized_line:
                if current and current.get("amount") is not None and current.get("label_parts"):
                    rows.append(current)
                current = None
                continue

            # Find amounts by their horizontal position in the debit/credit columns.
            debit_tokens = [w[4] for w in line_words if 390 <= w[0] < 480 and re.search(r"\d", w[4])]
            credit_tokens = [w[4] for w in line_words if w[0] >= 480 and re.search(r"\d", w[4])]
            debit_value = parse_amount(" ".join(debit_tokens)) if debit_tokens else None
            credit_value = parse_amount(" ".join(credit_tokens)) if credit_tokens else None
            amount = None
            if debit_value is not None and debit_value > 0:
                amount = -abs(debit_value)
            elif credit_value is not None and credit_value > 0:
                amount = abs(credit_value)

            value_date_token = next(
                (w[4] for w in line_words if 300 <= w[0] < 390 and re.fullmatch(r"\d{2}[./]\d{2}", w[4])),
                None,
            )

            if left_date:
                # A dated row starts a new transaction. Finish the previous one.
                if current and current.get("amount") is not None and current.get("label_parts"):
                    rows.append(current)
                day, month = map(int, re.split(r"[./]", left_date))
                tx_year = default_year
                # If a statement spans New Year, select the closest plausible year
                # from the years appearing in the statement.
                if len(years) > 1 and month == 1:
                    tx_year = min(years)
                elif len(years) > 1 and month == 12:
                    tx_year = max(years)
                try:
                    tx_date = date(tx_year, month, day).isoformat()
                except ValueError:
                    current = None
                    continue
                label_words = [w[4] for w in line_words if 60 <= w[0] < 300]
                # Ignore repeated table headers.
                if not label_words or normalize_text(" ".join(label_words)) in {"NATURE DES OPERATIONS"}:
                    current = None
                    continue
                value_date = tx_date
                if value_date_token:
                    vd, vm = map(int, re.split(r"[./]", value_date_token))
                    value_year = tx_year
                    if len(years) > 1 and vm == 1:
                        value_year = min(years)
                    elif len(years) > 1 and vm == 12:
                        value_year = max(years)
                    try:
                        value_date = date(value_year, vm, vd).isoformat()
                    except ValueError:
                        value_date = tx_date
                current = {
                    "date": tx_date,
                    "value_date": value_date,
                    "label_parts": [" ".join(label_words)],
                    "amount": amount,
                }
            elif current:
                # Continuation rows are part of the preceding transaction only.
                continuation = [w[4] for w in line_words if 60 <= w[0] < 300]
                if continuation:
                    current["label_parts"].append(" ".join(continuation))
                if amount is not None and current.get("amount") is None:
                    current["amount"] = amount

        if current and current.get("amount") is not None and current.get("label_parts"):
            rows.append(current)

    data = []
    for row in rows:
        label = re.sub(r"\s+", " ", " ".join(row["label_parts"])).strip()
        amount = row["amount"]
        if not label or amount is None:
            continue
        category, confidence = categorize(label, amount)
        data.append({
            "date": row["date"], "value_date": row.get("value_date", row["date"]),
            "label": label, "amount": amount, "account": account,
            "category": category, "confidence": confidence,
            "needs_review": confidence < 0.7 or category == REVIEW_CATEGORY,
            "source_file": source_name,
        })
    return pd.DataFrame(data, columns=["date","value_date","label","amount","account","category","confidence","needs_review","source_file"])

def load_pdf(path, account: str) -> pd.DataFrame:
    with open(path, "rb") as f:
        return extract_pdf_transactions(f.read(), account, getattr(path, "name", str(path)))

def monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["month","income","expenses","net"])
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x = x.dropna(subset=["date"])
    x["month"] = x["date"].dt.to_period("M").astype(str)
    # Les flux positifs et négatifs sont affichés séparément ; les transferts doivent être exclus des dépenses nettes.
    excluded = x["category"].isin(["Transfert interne potentiel", "Épargne & investissements"])
    x["income"] = x["amount"].where((x["amount"] > 0) & ~excluded, 0)
    x["expenses"] = (-x["amount"]).where((x["amount"] < 0) & ~excluded, 0)
    return x.groupby("month", as_index=False).agg(income=("income","sum"), expenses=("expenses","sum")).assign(
        net=lambda z: z["income"] - z["expenses"]
    )

def detect_recurring(df: pd.DataFrame, min_occurrences: int = 3) -> pd.DataFrame:
    cols = ["normalized_label","category","occurrences","avg_amount","last_date","account"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    x = df[df["amount"] < 0].copy()
    if x.empty:
        return pd.DataFrame(columns=cols)
    x["normalized_label"] = x["label"].map(normalize_text).str.replace(r"\d+", "", regex=True).str.strip()
    # Évite les libellés trop courts et les groupes peu informatifs.
    x = x[x["normalized_label"].str.len() >= 4]
    out = x.groupby(["normalized_label","category","account"], as_index=False).agg(
        occurrences=("date","count"), avg_amount=("amount", lambda s: round(abs(s).mean(),2)),
        last_date=("date","max")
    )
    return out[out["occurrences"] >= min_occurrences].sort_values(["occurrences","avg_amount"], ascending=[False,False])

def detect_potential_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    x = df.copy()
    keys = ["date","label","amount","account"]
    return x[x.duplicated(keys, keep=False)].sort_values(keys)

def forecast_scenarios(df: pd.DataFrame, months: int = 12, monthly_savings_cut: float = 0.0,
                        starting_cash: float = 0.0, annual_return_pct: float = 0.0) -> pd.DataFrame:
    summary = monthly_summary(df)
    if summary.empty:
        income = expenses = 0.0
    else:
        # Moyenne des 6 derniers mois disponibles plutôt que toute la période.
        recent = summary.tail(6)
        income = float(recent["income"].mean())
        expenses = float(recent["expenses"].mean())
    rows = []
    base_balance = float(starting_cash)
    optimized_balance = float(starting_cash)
    monthly_rate = (1 + annual_return_pct / 100) ** (1/12) - 1 if annual_return_pct > -100 else 0
    for m in range(1, months + 1):
        base_balance = base_balance * (1 + monthly_rate) + income - expenses
        optimized_balance = optimized_balance * (1 + monthly_rate) + income - max(0, expenses - monthly_savings_cut)
        rows.append({
            "month_number": m, "income_estimate": round(income,2),
            "expenses_estimate": round(expenses,2),
            "base_balance": round(base_balance,2),
            "optimized_balance": round(optimized_balance,2),
        })
    return pd.DataFrame(rows)

def summarize_data_quality(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"rows": 0, "unreviewed": 0, "duplicates": 0, "missing_dates": 0, "missing_labels": 0}
    return {
        "rows": int(len(df)),
        "unreviewed": int(df.get("needs_review", pd.Series(False, index=df.index)).fillna(True).astype(bool).sum()),
        "duplicates": int(df.duplicated(["date","label","amount","account"], keep=False).sum()),
        "missing_dates": int(df["date"].isna().sum()),
        "missing_labels": int(df["label"].isna().sum() + (df["label"].astype(str).str.strip() == "").sum()),
    }
