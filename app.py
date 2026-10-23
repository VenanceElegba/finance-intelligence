
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from finance_core import (
    extract_pdf_transactions,
    monthly_summary,
    detect_recurring,
    detect_potential_duplicates,
    forecast_scenarios,
    summarize_data_quality,
    CATEGORY_RULES,
)

from storage import (
    init_db,
    insert_transactions,
    read_transactions,
    update_transaction,
    delete_transaction,
)

# ============================================================
# Configuration
# ============================================================

st.set_page_config(
    page_title="BNP Finance Intelligence",
    page_icon="💶",
    layout="wide",
)

st.title("💶 BNP Finance Intelligence")
st.caption(
    "Analyse financière personnelle · traitement local · règles explicables"
)

DB_PATH = os.environ.get(
    "FINANCE_DB_PATH",
    "/data/finance.sqlite3"
)

# Dans Docker, le volume ./data de l'hôte est monté dans /data.
# En exécution locale, utilise ./data/input.
if Path("/data").exists():
    INPUT_DIR = Path("/data/input")
else:
    INPUT_DIR = Path("data/input")

DEFAULT_ACCOUNT = os.environ.get(
    "BNP_DEFAULT_ACCOUNT",
    "BNP Courant"
)

# Initialisation de la base
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
INPUT_DIR.mkdir(parents=True, exist_ok=True)
init_db(DB_PATH)


# ============================================================
# Import automatique des PDF
# ============================================================

def get_pdf_files() -> list[Path]:
    """Retourne tous les PDF du dossier input et de ses sous-dossiers."""
    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    return sorted(
        (
            path
            for path in INPUT_DIR.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        ),
        key=lambda path: str(path).lower(),
    )


def get_account_name(pdf_path: Path) -> str:
    """
    Détermine le compte associé au PDF.

    Exemples :
      input/releve_octobre.pdf
          -> BNP Courant

      input/BNP Livret/releve_octobre.pdf
          -> BNP Livret

      input/BNP Courant/2026/octobre.pdf
          -> BNP Courant
    """
    try:
        relative_path = pdf_path.relative_to(INPUT_DIR)
    except ValueError:
        return DEFAULT_ACCOUNT

    if len(relative_path.parts) > 1:
        return relative_path.parts[0]

    return DEFAULT_ACCOUNT


def scan_input_pdfs() -> dict:
    """
    Analyse et importe tous les PDF du dossier input.

    La déduplication est effectuée par insert_transactions().
    Un PDF en erreur n'empêche pas les autres d'être traités.
    """
    results = {
        "files_found": 0,
        "files_processed": 0,
        "files_empty": 0,
        "files_failed": 0,
        "transactions_inserted": 0,
        "transactions_skipped": 0,
        "details": [],
    }

    try:
        pdf_files = get_pdf_files()
    except Exception as exc:
        results["files_failed"] = 1
        results["details"].append({
            "Fichier": str(INPUT_DIR),
            "Statut": "Erreur",
            "Détail": str(exc),
        })
        return results

    results["files_found"] = len(pdf_files)

    for pdf_path in pdf_files:
        try:
            pdf_bytes = pdf_path.read_bytes()
            account = get_account_name(pdf_path)

            extracted = extract_pdf_transactions(
                pdf_bytes,
                account,
                pdf_path.name,
            )

            if extracted.empty:
                results["files_empty"] += 1
                results["details"].append({
                    "Fichier": str(pdf_path.relative_to(INPUT_DIR)),
                    "Compte": account,
                    "Statut": "Aucune opération détectée",
                    "Ajoutées": 0,
                    "Doublons ignorés": 0,
                })
                continue

            inserted, skipped = insert_transactions(
                extracted,
                DB_PATH,
            )

            results["files_processed"] += 1
            results["transactions_inserted"] += inserted
            results["transactions_skipped"] += skipped

            results["details"].append({
                "Fichier": str(pdf_path.relative_to(INPUT_DIR)),
                "Compte": account,
                "Statut": "Traité",
                "Opérations extraites": len(extracted),
                "Ajoutées": inserted,
                "Doublons ignorés": skipped,
            })

        except Exception as exc:
            results["files_failed"] += 1
            results["details"].append({
                "Fichier": str(pdf_path),
                "Statut": "Erreur",
                "Détail": str(exc),
            })

    return results


def show_scan_results(results: dict) -> None:
    """Affiche le bilan d'un scan."""
    c1, c2, c3, c4 = st.columns(4)

    c1.metric("PDF trouvés", results["files_found"])
    c2.metric("PDF traités", results["files_processed"])
    c3.metric(
        "Opérations ajoutées",
        results["transactions_inserted"],
    )
    c4.metric(
        "Doublons ignorés",
        results["transactions_skipped"],
    )

    if results["files_failed"]:
        st.warning(
            f"{results['files_failed']} fichier(s) en erreur. "
            "Consulte le détail ci-dessous."
        )

    if results["files_empty"]:
        st.warning(
            f"{results['files_empty']} PDF sans opération détectée. "
            "Vérifie que ces relevés contiennent du texte sélectionnable "
            "et que leur format est compatible."
        )

    if results["details"]:
        st.dataframe(
            pd.DataFrame(results["details"]),
            use_container_width=True,
            hide_index=True,
        )

    if not results["files_found"]:
        st.info(
            f"Aucun PDF dans `{INPUT_DIR}`. "
            "Ajoute tes relevés dans ce dossier, puis relance le scan."
        )


# Scan automatique à la première exécution de chaque session Streamlit.
# Le bouton manuel permet de rescanner après ajout de nouveaux fichiers.
if "startup_scan_done" not in st.session_state:
    with st.spinner("Recherche et import automatique des relevés PDF…"):
        st.session_state["startup_scan_results"] = scan_input_pdfs()
        st.session_state["startup_scan_done"] = True


# ============================================================
# Navigation
# ============================================================

with st.sidebar:
    st.header("Navigation")

    page = st.radio(
        "Section",
        [
            "Vue d'ensemble",
            "Importer",
            "Transactions",
            "Récurrences",
            "Prévisions",
            "Qualité des données",
            "Exporter",
        ],
    )

    st.divider()

    st.caption("Import automatique")
    st.write(f"**Dossier surveillé :** `{INPUT_DIR}`")
    st.write(f"**Compte par défaut :** {DEFAULT_ACCOUNT}")

    if st.button("Scanner les PDF maintenant", use_container_width=True):
        with st.spinner("Analyse de tous les PDF…"):
            st.session_state["manual_scan_results"] = scan_input_pdfs()

        st.rerun()

    st.divider()
    st.caption("Confidentialité")
    st.write(
        "Aucune API externe. Les données restent dans la base SQLite locale."
    )
    st.caption(
        "Vérifie les opérations incertaines avant de prendre une décision."
    )


# Affichage du résultat du scan manuel
if "manual_scan_results" in st.session_state:
    st.subheader("Résultat du dernier scan")
    show_scan_results(st.session_state["manual_scan_results"])
    st.divider()

# Résultat du scan de démarrage
elif "startup_scan_results" in st.session_state:
    startup_results = st.session_state["startup_scan_results"]

    if startup_results["files_found"] > 0:
        with st.expander("Résultat de l'import automatique", expanded=False):
            show_scan_results(startup_results)


# Relire la base après les imports automatiques
df = read_transactions(DB_PATH)


# ============================================================
# Vue d'ensemble
# ============================================================

if page == "Vue d'ensemble":
    st.subheader("Vue d'ensemble")

    if df.empty:
        st.info(
            "Aucune transaction importée. "
            "Place tes relevés PDF dans le dossier input."
        )
    else:
        accounts = sorted(df["account"].dropna().unique().tolist())

        selected = st.multiselect(
            "Comptes inclus",
            accounts,
            default=accounts,
        )

        x = df[df["account"].isin(selected)].copy()

        if not x.empty:
            summary = monthly_summary(x)

            total_income = (
                float(summary["income"].sum())
                if not summary.empty
                else 0
            )

            total_expenses = (
                float(summary["expenses"].sum())
                if not summary.empty
                else 0
            )

            net = total_income - total_expenses

            c1, c2, c3, c4 = st.columns(4)

            c1.metric(
                "Revenus détectés",
                f"{total_income:,.2f} €".replace(",", " "),
            )
            c2.metric(
                "Dépenses hors transferts",
                f"{total_expenses:,.2f} €".replace(",", " "),
            )
            c3.metric(
                "Solde net des flux",
                f"{net:,.2f} €".replace(",", " "),
            )
            c4.metric("Opérations", f"{len(x):,}".replace(",", " "))

            st.caption(
                "Les indicateurs reposent sur les opérations importées. "
                "Ils ne représentent pas nécessairement le solde bancaire réel."
            )

            if not summary.empty:
                st.plotly_chart(
                    px.line(
                        summary,
                        x="month",
                        y=["income", "expenses", "net"],
                        markers=True,
                        title="Flux mensuels (€)",
                        labels={
                            "month": "Mois",
                            "value": "Euros",
                            "variable": "Série",
                        },
                    ),
                    use_container_width=True,
                )

            spending = x[
                (x["amount"] < 0)
                & ~x["category"].isin([
                    "Transfert interne potentiel",
                    "Épargne & investissements",
                ])
            ].copy()

            if not spending.empty:
                spending["spend"] = -spending["amount"]

                bycat = (
                    spending.groupby("category", as_index=False)["spend"]
                    .sum()
                    .sort_values("spend", ascending=False)
                )

                col1, col2 = st.columns(2)

                with col1:
                    st.plotly_chart(
                        px.bar(
                            bycat,
                            x="category",
                            y="spend",
                            title="Dépenses par catégorie (€)",
                        ),
                        use_container_width=True,
                    )

                with col2:
                    st.plotly_chart(
                        px.pie(
                            bycat,
                            names="category",
                            values="spend",
                            title="Répartition des dépenses",
                        ),
                        use_container_width=True,
                    )

            st.subheader("Dernières opérations")
            st.dataframe(
                x.sort_values("date", ascending=False).head(20),
                use_container_width=True,
                hide_index=True,
            )


# ============================================================
# Import
# ============================================================

elif page == "Importer":
    st.subheader("Import automatique des relevés")

    st.write(
        "Les PDF du dossier input sont analysés automatiquement "
        "au démarrage de ta session. Tu n'as pas besoin de les "
        "sélectionner un par un."
    )

    st.code(str(INPUT_DIR), language="text")

    pdfs = get_pdf_files()

    st.write(f"**{len(pdfs)} PDF détecté(s)**")

    if pdfs:
        file_rows = []

        for pdf in pdfs:
            try:
                relative_name = str(pdf.relative_to(INPUT_DIR))
            except ValueError:
                relative_name = pdf.name

            file_rows.append({
                "Fichier": relative_name,
                "Compte attribué": get_account_name(pdf),
                "Taille (Ko)": round(pdf.stat().st_size / 1024, 1),
            })

        st.dataframe(
            pd.DataFrame(file_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info(
            "Le dossier est vide. Copie tes relevés PDF dans ce dossier "
            "sur ton ordinateur."
        )

    if st.button("Analyser et importer tous les PDF", type="primary"):
        with st.spinner("Traitement de tous les relevés…"):
            results = scan_input_pdfs()

        st.session_state["manual_scan_results"] = results
        st.rerun()

    st.divider()

    st.subheader("Organisation conseillée des fichiers")

    st.code(
        "data/\n"
        "└── input/\n"
        "    ├── BNP Courant/\n"
        "    │   ├── releve_janvier.pdf\n"
        "    │   └── releve_fevrier.pdf\n"
        "    └── BNP Livret/\n"
        "        └── releve_fevrier.pdf",
        language="text",
    )

    st.caption(
        "Le nom du sous-dossier devient le nom du compte. "
        "Un PDF placé directement dans input/ utilise le compte "
        f"par défaut : {DEFAULT_ACCOUNT}."
    )

    st.warning(
        "Les PDF scannés sans texte sélectionnable ne sont pas pris en charge. "
        "Vérifie les montants et les dates après l'extraction. "
        "Les opérations extraites restent à contrôler."
    )


# ============================================================
# Transactions
# ============================================================

elif page == "Transactions":
    st.subheader("Transactions et catégorisation")

    if df.empty:
        st.info("Aucune opération importée.")
    else:
        accounts = ["Tous"] + sorted(df["account"].unique().tolist())

        acc = st.selectbox("Compte", accounts)
        query = st.text_input("Recherche dans les libellés")
        only_review = st.checkbox(
            "Afficher uniquement les opérations à vérifier"
        )

        x = df.copy()

        if acc != "Tous":
            x = x[x["account"] == acc]

        if query:
            x = x[x["label"].str.contains(query, case=False, na=False)]

        if only_review:
            x = x[x["needs_review"]]

        st.dataframe(
            x.sort_values("date", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

        st.divider()
        st.markdown("#### Corriger une opération")

        tx_ids = x["id"].astype(int).tolist()

        if tx_ids:
            tx_id = st.selectbox("Identifiant de transaction", tx_ids)
            current = x[x["id"] == tx_id].iloc[0]

            st.write(
                f"**{current['date']} · {current['label']} · "
                f"{current['amount']:.2f} €**"
            )

            categories = sorted(
                set(CATEGORY_RULES.keys())
                | {
                    "Revenus",
                    "Transfert interne potentiel",
                    "Autre",
                    "À vérifier",
                }
            )

            default_idx = (
                categories.index(current["category"])
                if current["category"] in categories
                else categories.index("À vérifier")
            )

            new_cat = st.selectbox(
                "Catégorie corrigée",
                categories,
                index=default_idx,
            )

            review = st.checkbox(
                "À vérifier",
                value=bool(current["needs_review"]),
            )

            c1, c2 = st.columns(2)

            if c1.button("Enregistrer la correction", type="primary"):
                update_transaction(
                    int(tx_id),
                    new_cat,
                    review,
                    DB_PATH,
                )
                st.success("Correction enregistrée.")
                st.rerun()

            if c2.button("Supprimer cette opération"):
                delete_transaction(int(tx_id), DB_PATH)
                st.rerun()


# ============================================================
# Récurrences
# ============================================================

elif page == "Récurrences":
    st.subheader("Dépenses récurrentes potentielles")

    recurring = detect_recurring(df)

    if recurring.empty:
        st.info(
            "Pas assez d'occurrences détectées. "
            "Il faut généralement au moins trois opérations similaires."
        )
    else:
        recurring["annualized_estimate"] = recurring["avg_amount"] * 12

        st.dataframe(
            recurring,
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "La répétition d'un libellé ne garantit pas qu'il s'agit "
            "d'un abonnement. Vérifie chaque ligne."
        )


# ============================================================
# Prévisions
# ============================================================

elif page == "Prévisions":
    st.subheader("Simulateur de scénarios")

    if df.empty:
        st.info(
            "Importe des relevés pour construire une prévision "
            "basée sur les derniers mois disponibles."
        )
    else:
        c1, c2, c3 = st.columns(3)

        months = c1.selectbox(
            "Horizon",
            [6, 12, 24, 36, 60],
            index=1,
        )

        cut = c2.number_input(
            "Économies supplémentaires visées par mois (€)",
            min_value=0.0,
            max_value=10000.0,
            value=150.0,
            step=25.0,
        )

        start = c3.number_input(
            "Épargne de départ (€)",
            min_value=0.0,
            value=0.0,
            step=500.0,
        )

        rate = st.number_input(
            "Rendement annuel hypothétique (%)",
            min_value=0.0,
            max_value=15.0,
            value=0.0,
            step=0.5,
        )

        forecast = forecast_scenarios(
            df,
            int(months),
            float(cut),
            float(start),
            float(rate),
        )

        st.plotly_chart(
            px.line(
                forecast,
                x="month_number",
                y=["base_balance", "optimized_balance"],
                title="Projection de trésorerie (scénarios)",
                labels={
                    "month_number": "Mois",
                    "value": "Euros",
                    "variable": "Scénario",
                },
            ),
            use_container_width=True,
        )

        st.dataframe(
            forecast,
            use_container_width=True,
            hide_index=True,
        )

        last = forecast.iloc[-1]

        st.metric(
            "Écart simulé à l'horizon",
            f"{last['optimized_balance'] - last['base_balance']:,.2f} €"
            .replace(",", " "),
        )

        st.warning(
            "Simulation déterministe : revenus et dépenses moyens des "
            "six derniers mois constants. Le rendement est une hypothèse, "
            "non garanti. Vérifie que les transferts et comptes sélectionnés "
            "ne sont pas comptés deux fois."
        )


# ============================================================
# Qualité des données
# ============================================================

elif page == "Qualité des données":
    st.subheader("Qualité et contrôles")

    quality = summarize_data_quality(df)

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Opérations", quality["rows"])
    c2.metric("À vérifier", quality["unreviewed"])
    c3.metric("Lignes potentiellement dupliquées", quality["duplicates"])
    c4.metric("Dates manquantes", quality["missing_dates"])

    if not df.empty:
        st.markdown("#### Doublons potentiels")

        dups = detect_potential_duplicates(df)

        if dups.empty:
            st.success("Aucun doublon exact potentiel.")
        else:
            st.warning(
                "Vérifie ces lignes avant suppression : "
                "des opérations légitimes peuvent être identiques."
            )
            st.dataframe(
                dups,
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### Opérations à vérifier")

        review = df[df["needs_review"]]

        st.dataframe(
            review,
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("#### Contrôle par compte")

        st.dataframe(
            df.groupby("account", as_index=False).agg(
                operations=("id", "count"),
                earliest=("date", "min"),
                latest=("date", "max"),
                inflows=(
                    "amount",
                    lambda s: round(s[s > 0].sum(), 2),
                ),
                outflows=(
                    "amount",
                    lambda s: round(-s[s < 0].sum(), 2),
                ),
            ),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# Export
# ============================================================

elif page == "Exporter":
    st.subheader("Exporter les données")

    if df.empty:
        st.info("Aucune donnée à exporter.")
    else:
        csv = (
            df.drop(columns=["fingerprint"], errors="ignore")
            .to_csv(index=False)
            .encode("utf-8-sig")
        )

        st.download_button(
            "Télécharger toutes les transactions (CSV)",
            csv,
            "transactions_bnp.csv",
            "text/csv",
        )

        monthly = monthly_summary(df)

        st.download_button(
            "Télécharger la synthèse mensuelle (CSV)",
            monthly.to_csv(index=False).encode("utf-8-sig"),
            "synthese_mensuelle.csv",
            "text/csv",
        )

        st.caption(
            "Les exports peuvent contenir des données financières sensibles. "
            "Conserve-les dans un emplacement protégé."
        )

