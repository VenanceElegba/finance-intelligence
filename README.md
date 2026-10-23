# BNP Finance Intelligence

Application locale d'analyse de relevés BNP Paribas : import de PDF texte, catégorisation explicable par règles, détection de récurrences/anomalies, tableau de bord, simulations et exports.

## Confidentialité

- Traitement local : aucun appel à une API externe, aucun modèle cloud.
- Docker publie l'interface uniquement sur `127.0.0.1`.
- Les PDF importés et la base SQLite restent dans `./data`.
- Ne publiez jamais `data/`, `.env`, les relevés ou les exports dans Git.
- Utilisez le chiffrement complet du disque et une sauvegarde locale protégée. L'application ne chiffre pas elle-même la base.
- Les libellés bancaires peuvent être ambigus : les opérations incertaines doivent être vérifiées.

## Démarrage

Prérequis : Docker Desktop ou Docker Engine + Docker Compose.

```bash
git clone https://github.com/VenanceElegba/finance-intelligence.git
cd finance-intelligence
mkdir -p data/input data/exports
mkdir -p data/input/BNPCourant
mkdir -p data/input/BNPLivret
# Placez les relevés PDF texte dans data/input (facultatif : import via l'interface)
docker compose up --build -d
```

Ouvrir http://127.0.0.1:8501

Arrêt :
```bash
docker compose down
```

Les données SQLite persistent dans `./data/finance.sqlite3`. Pour réinitialiser complètement, arrêtez le service puis sauvegardez/supprimez explicitement cette base. Ne supprimez pas les relevés originaux par automatisme.

## Utilisation

1. Dans **Importer**, choisissez un PDF texte et donnez-lui un nom de compte (ex. `BNP Courant`, `BNP Livret`).
2. Vérifiez le nombre d'opérations extraites et les dates. Si l'extraction est faible ou incorrecte, n'interprétez pas les graphiques avant correction.
3. Dans **Transactions**, filtrez les comptes, corrigez les catégories et marquez les opérations à vérifier.
4. Dans **Qualité des données**, examinez les doublons potentiels et les opérations sans catégorie fiable.
5. Dans **Prévisions**, saisissez des hypothèses d'économies mensuelles. Les projections sont des scénarios déterministes, pas des promesses de rendement.

## Formats BNP

Le parseur est volontairement conservateur et conçu pour des PDF contenant du texte sélectionnable. Les mises en page BNP peuvent varier selon le produit, le compte et l'année. Le parseur prend en charge les lignes de transaction courantes avec une date, un libellé et un montant en euros ; il ne garantit pas l'extraction de toutes les variantes. Comparez toujours les totaux extraits avec les totaux et soldes figurant sur le relevé. Les PDF scannés ne sont pas pris en charge par cette version.

## Tests

```bash
docker compose run --rm app pytest -q
```

Ou localement avec Python 3.12+ :
```bash
python -m venv .venv
source .venv/bin/activate  # Windows : .venv\Scripts\activate
pip install -r requirements.txt
pytest -q
streamlit run app.py
```

## Architecture

- `app.py` : interface Streamlit.
- `finance_core.py` : extraction PDF, normalisation, règles, analyses et prévisions.
- `storage.py` : persistance SQLite et déduplication.
- `tests/` : tests unitaires sans relevé bancaire réel.

## Limites importantes

- La classification automatique est une suggestion basée sur des règles locales, pas une vérité comptable.
- Les virements internes sont estimés à partir des libellés ; vérifiez-les pour éviter de compter un transfert comme dépense.
- Les doublons sont signalés par empreinte, mais deux opérations légitimes similaires peuvent se ressembler.
- La prévision de base extrapole les moyennes historiques et ne prédit pas avec certitude les changements futurs.
- L'application fournit de l'aide à la décision budgétaire, pas un conseil financier, fiscal ou juridique personnalisé.
