import pytest

from src.enrichment.taxonomy import find_mentions


def concepts(text: str) -> set[str]:
    return {m.concept for m in find_mentions(text)}


# --- Classification -----------------------------------------------------

@pytest.mark.parametrize("text", [
    "ML techniques such as classification, regression and clustering.",
    "Experience with image classification and object detection.",
    "Train binary classification models for churn.",
    "Build classifiers for fraud.",
])
def test_classification_detected(text):
    assert "Classification" in concepts(text)


@pytest.mark.parametrize("text", [
    "Maintain the job classification framework for HR.",
    "Ensure data classification and security policies are followed.",
    "Tax classification of imported goods.",
])
def test_classification_not_detected_outside_modelling(text):
    assert "Classification" not in concepts(text)


# --- Regression vs regression testing ------------------------------------

@pytest.mark.parametrize("text", [
    "Experience with linear and logistic regression.",
    "ML models (classification and regression).",
    "Applied regression analysis and GLMs.",
])
def test_regression_modelling_detected(text):
    assert "Regression" in concepts(text)


@pytest.mark.parametrize("text", [
    "Automate regression testing for the release pipeline.",
    "Run the regression suite before each deploy.",
    "Investigate performance regression in the API.",
    "Manual regression checks by QA.",
    "Catch model quality, performance, and regressions early.",
])
def test_regression_testing_not_detected(text):
    assert "Regression" not in concepts(text)


# --- Other new techniques ------------------------------------------------

def test_anomaly_detection():
    assert "Anomaly Detection" in concepts("Build anomaly detection models for sensor data.")
    assert "Anomaly Detection" in concepts("Models that detect anomalies in vibration signals.")
    assert "Anomaly Detection" in concepts("Experience with outlier detection.")
    assert "Anomaly Detection" not in concepts("Identify patterns, trends, and anomalies in reports.")


def test_feature_engineering():
    assert "Feature Engineering" in concepts("Perform EDA and feature engineering on structured data.")
    assert "Feature Engineering" not in concepts("Software engineering of new product features.")


def test_model_evaluation():
    assert "Model Evaluation" in concepts("Own model evaluation and validation.")
    assert "Model Evaluation" in concepts("Solid grounding in evaluation metrics and cross-validation.")
    assert "Model Evaluation" not in concepts("Define key performance metrics for the growth team.")


def test_model_deployment():
    assert "Model Deployment" in concepts("Experience with model deployment tools.")
    assert "Model Deployment" in concepts("Develop, deploy, and optimize AI/ML models.")
    assert "Model Deployment" not in concepts("Deploy the marketing website to the CDN.")


def test_production_ml():
    assert "Production ML" in concepts("Experience building production-grade machine learning systems.")
    assert "Production ML" in concepts("Own ML models in production environments.")
    assert "Production ML" in concepts("Productionize machine learning models.")


def test_mlops():
    assert "MLOps" in concepts("Familiarity with MLOps and LLMOps concepts.")
    assert "MLOps" in concepts("Knowledge of machine learning operations.")


def test_ranking():
    assert "Ranking" in concepts("Recommendation systems including retrieval and ranking.")
    assert "Ranking" in concepts("Expertise in Learning to Rank for search.")
    assert "Ranking" not in concepts("Experiment with low-rank adaptation (LoRA).")
    assert "Ranking" not in concepts("The university's world ranking improved.")


def test_optimisation():
    assert "Optimisation" in concepts("Experience with constrained optimisation (MILP).")
    assert "Optimisation" in concepts("Build forecasting and optimisation use cases.")
    assert "Optimisation" in concepts("Operations research (optimization) for routing.")
    assert "Optimisation" not in concepts("Drive cost optimization across vendors.")
    assert "Optimisation" not in concepts("Bachelor's in Data Science, Operations Research, or equivalent.")


def test_existing_techniques_preserved():
    text = ("predictive modelling, forecasting, time series, A/B testing, causal inference, "
            "recommender system, NLP, computer vision")
    assert {"Forecasting", "Time Series", "A/B Testing", "Causal Inference",
            "Recommendation Systems", "NLP", "Computer Vision"} <= concepts(text)
    assert "Predictive Modeling" in concepts("predictive modelling for demand")
