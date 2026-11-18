from src.enrichment.role_classifier import (
    classify_characteristics,
    classify_role_family,
    classify_role_level,
    classify_role_type,
)


def test_classify_role_family_senior_ds():
    assert classify_role_family("Senior Data Scientist") == "Senior Data Scientist"


def test_classify_role_family_plain_ds():
    assert classify_role_family("Data Scientist") == "Data Scientist"


def test_classify_role_family_fraud_ds():
    assert classify_role_family("Fraud Data Scientist") == "Fraud Data Scientist"


def test_classify_role_family_ml_engineer():
    assert classify_role_family("Machine Learning Engineer") == "ML Engineer"


def test_classify_role_family_data_analyst():
    assert classify_role_family("Data Analyst") == "Data Analyst"


def test_classify_role_family_other():
    assert classify_role_family("Barista") == "Other"


def test_classify_role_level_senior():
    assert classify_role_level("Senior Data Scientist") == "Senior"


def test_classify_role_level_manager():
    assert classify_role_level("Data Scientist Manager") == "Manager"


def test_classify_role_level_default():
    assert classify_role_level("Data Scientist") == "Mid/Unspecified"


def test_true_ds_classification():
    desc = "Build and deploy production ML models. Own feature engineering and model evaluation for fraud detection using XGBoost."
    role_type, confidence = classify_role_type("Data Scientist", desc, "3+ years experience with machine learning and statistical modeling required.")
    assert role_type == "TRUE_DS"
    assert confidence > 0.5


def test_ds_title_but_analytics_heavy():
    desc = "Maintain dashboards, generate MIS reports, and perform ad-hoc reporting for stakeholders using SQL queries."
    role_type, confidence = classify_role_type("Data Scientist", desc, "Strong SQL and Excel report generation skills.")
    assert role_type == "DS_TITLE_BUT_ANALYTICS_HEAVY"


def test_analytics_heavy_with_one_incidental_ml_mention():
    # Resolution-gap regression: reporting signals clearly outnumber the
    # one incidental ML-sounding mention. Previously this fell through
    # to DS_TITLE_UNCLEAR (eligible) because the old logic only checked
    # ml_count == 0 before calling it analytics-heavy.
    desc = "Maintain dashboards and generate ad-hoc reports for stakeholders."
    reqs = "Strong SQL, Excel report generation, and some machine learning exposure a plus."
    role_type, confidence = classify_role_type("Data Scientist", desc, reqs)
    assert role_type == "DS_TITLE_BUT_ANALYTICS_HEAVY"


def test_insufficient_text_returns_unclear():
    role_type, confidence = classify_role_type("Data Scientist", "", "")
    assert role_type == "DS_TITLE_UNCLEAR"
    assert confidence <= 0.2


def test_unrecognized_title_no_signal_is_rejected():
    role_type, _ = classify_role_type("Other", "Make coffee and serve customers with a smile every day at our store." * 3, "")
    assert role_type == "NO_DS_SIGNAL"


def test_unrecognized_title_with_strong_ml_content_is_true_ds():
    desc = "Own machine learning model deployment, feature engineering, statistical modelling and model evaluation for our platform."
    role_type, confidence = classify_role_type("Other", desc, "Experience with xgboost and production ml required.")
    assert role_type == "TRUE_DS"


def test_ml_engineer_role_type():
    role_type, _ = classify_role_type("ML Engineer", "Deploy ML pipelines to production using Docker and Kubernetes at scale for our platform team.", "")
    assert role_type == "ML_ENGINEER"


def test_classify_characteristics():
    text = "We do fraud detection and risk modelling using NLP and forecasting for our fintech banking platform."
    chars = classify_characteristics(text)
    assert chars["fraud"] is True
    assert chars["risk"] is True
    assert chars["nlp"] is True
    assert chars["forecasting"] is True
    assert chars["fintech"] is True
    assert chars["computer_vision"] is False
