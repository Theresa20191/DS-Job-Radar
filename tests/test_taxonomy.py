from src.enrichment.taxonomy import concept_kinds, find_mentions, load_concepts
from src.enrichment.skill_evidence import build_evidence
from src.enrichment.skill_extractor import extract_required_and_preferred, extract_skills

# The ORIGINAL flat taxonomy, frozen. The legacy required/preferred skill
# lists are fed by exactly these patterns; keep them byte-stable.
ORIGINAL_LEGACY_PATTERNS = {
    "Python": ["python"],
    "SQL": ["sql", "mysql", "postgresql", "t-sql", "pl/sql"],
    "pandas": [r"\bpandas\b"],
    "numpy": [r"\bnumpy\b"],
    "matplotlib": ["matplotlib"],
    "seaborn": ["seaborn"],
    "R": [r"\br\b programming", r"\br language", " r,", " r/"],
    "Statistics": ["statistics", "statistical modeling", "statistical modelling", "hypothesis testing"],
    "Machine Learning": ["machine learning", r"\bml\b"],
    "Deep Learning": ["deep learning", "neural network"],
    "Scikit-learn": ["scikit-learn", "sklearn"],
    "XGBoost": ["xgboost"],
    "CatBoost": ["catboost"],
    "LightGBM": ["lightgbm"],
    "TensorFlow": ["tensorflow"],
    "PyTorch": ["pytorch"],
    "Spark": ["spark", "pyspark"],
    "Databricks": ["databricks"],
    "AWS": ["aws", "amazon web services", "sagemaker"],
    "GCP": ["gcp", "google cloud", "bigquery", "vertex ai"],
    "Azure": ["azure"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", r"\bk8s\b"],
    "Git": [r"\bgit\b", "github", "gitlab"],
    "Airflow": ["airflow"],
    "dbt": [r"\bdbt\b"],
    "A/B Testing": ["a/b test", "ab testing", "split test"],
    "Experimentation": ["experimentation", "experiment design"],
    "Causal Inference": ["causal inference"],
    "Time Series": ["time series", "time-series"],
    "Forecasting": ["forecasting", "demand forecast"],
    "NLP": [r"\bnlp\b", "natural language processing"],
    "LLM": [r"\bllm\b", "large language model", "generative ai", "genai"],
    "Generative AI": ["generative ai", "genai"],
    "Computer Vision": ["computer vision", "image recognition", "object detection"],
    "Recommendation Systems": ["recommendation system", "recommender system"],
    "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"],
    "Qlik": ["qlik"],
    "ExtraTrees": ["extra trees", "extratrees"],
    "LSTM": ["lstm"],
    "Predictive Modeling": ["predictive model", "predictive analytics"],
}


def test_legacy_patterns_byte_identical_to_original():
    legacy = {c.name: [p.regex.pattern for p in c.patterns] for c in load_concepts() if c.legacy}
    assert legacy == ORIGINAL_LEGACY_PATTERNS


def test_every_concept_is_tool_or_technique():
    assert all(c.kind in ("tool", "technique") for c in load_concepts())


def test_tool_technique_classification():
    kinds = concept_kinds()
    for tool in ("Python", "SQL", "Scikit-learn", "XGBoost", "Power BI", "Spark", "AWS"):
        assert kinds[tool] == "tool", tool
    for technique in ("Machine Learning", "Predictive Modeling", "Classification", "Regression", "Forecasting",
                      "Time Series", "Anomaly Detection", "Recommendation Systems", "Ranking", "NLP",
                      "Computer Vision", "A/B Testing", "Causal Inference", "Optimisation",
                      "Feature Engineering", "Model Evaluation", "Model Deployment", "Production ML", "MLOps"):
        assert kinds[technique] == "technique", technique


def test_domain_concepts_not_in_technical_taxonomy():
    names = {c.name.lower() for c in load_concepts()}
    for domain in ("aml", "fraud", "risk", "credit risk", "banking", "insurance", "payments", "financial crime", "fintech"):
        assert domain not in names


def test_aml_text_yields_no_technical_evidence():
    text = "Our AML team runs anti-money laundering checks; fraud and credit risk monitoring."
    assert extract_skills(text) == []
    evidence, _ = build_evidence(text, "")
    assert evidence == []


def test_applied_ml_aml_acronym_is_not_domain_evidence():
    # "Applied Machine Learning (AML)" is a technique mention (Machine
    # Learning), never AML domain/skill evidence.
    text = "Join the Applied Machine Learning (AML) team building ranking models."
    evidence, _ = build_evidence(text, "")
    names = {e.name for e in evidence}
    assert "Machine Learning" in names
    assert "AML" not in names


def test_seamless_does_not_trigger_anything_aml():
    text = "Ensure seamless integration of models with simulation engineers."
    assert "AML" not in {m.concept for m in find_mentions(text)}


def test_new_techniques_never_enter_legacy_skill_lists():
    text = "Build classification, regression, clustering and anomaly detection models; MLOps and model deployment."
    required, preferred = extract_required_and_preferred(text, text)
    new = {"Classification", "Regression", "Clustering", "Anomaly Detection", "MLOps", "Model Deployment"}
    assert not (new & set(required)) and not (new & set(preferred))
