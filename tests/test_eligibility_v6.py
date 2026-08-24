from src.filtering import (
    evaluate_eligibility,
    is_relevant,
    relevance_score,
    score_sector_fit,
)
from src.models import Job


def _job(title, description="", location="Austin, TX, US"):
    return Job(
        company="ExampleCo",
        external_id=title.lower().replace(" ", "-"),
        title=title,
        location=location,
        url="https://example.com/job",
        source="test",
        description=description,
    )


def test_phd_and_master_degree_roles_are_ineligible():
    phd = _job("Apple Hardware Technologies PhD Internships", "PhD students only")
    ms = _job("Hardware Technologies Masters Engineering Internships", "MS students only")
    assert evaluate_eligibility(phd).status == "ineligible"
    assert evaluate_eligibility(ms).status == "ineligible"


def test_graduate_only_internship_titles_are_ineligible_without_detail_text():
    phd_pool = _job(
        "NVIDIA 2027 Internships: Ph.D. Research Generative AI",
        "Machine learning, deep learning, and Python.",
    )
    masters_pool = _job(
        "Hardware Technologies Masters Engineering Internships",
        "ASIC RTL and computer architecture.",
    )
    assert evaluate_eligibility(phd_pool).status == "ineligible"
    assert evaluate_eligibility(masters_pool).status == "ineligible"
    assert not is_relevant(phd_pool, 12, target_year=2027)
    assert phd_pool.score == -50


def test_explicit_bachelors_allowance_overrides_other_degree_mentions():
    mixed_degree = _job(
        "BS/MS/PhD Hardware Engineering Intern",
        "Bachelor's, Master's, or PhD students may apply for RTL verification work.",
    )
    researchers = _job(
        "Machine Learning Undergraduate Intern",
        "Bachelor's students work alongside PhD researchers on deep learning.",
    )
    assert evaluate_eligibility(mixed_degree).status == "eligible"
    assert evaluate_eligibility(researchers).status == "eligible"


def test_unrelated_master_word_is_not_treated_as_a_degree_restriction():
    master_data = _job(
        "Master Data Software Intern",
        "Build Python services for data governance; degree requirement is not listed.",
    )
    assert evaluate_eligibility(master_data).status == "uncertain"


def test_non_intern_and_new_grad_roles_are_ineligible():
    pm = _job("Amazon Program Manager - Site Operations, Annapurna Labs Silicon", "full-time program manager role")
    new_grad = _job("Software Engineer New Grad", "full-time software engineer new grad")
    assert evaluate_eligibility(pm).status == "ineligible"
    assert evaluate_eligibility(new_grad).status == "ineligible"


def test_valid_undergrad_hardware_and_ml_roles_remain_eligible():
    hw = _job("Apple Hardware Technologies Undergrad Engineering Internships", "embedded systems, RTL, silicon design")
    ml = _job("Apple Machine Learning and Artificial Intelligence Undergrad Internships", "deep learning, model evaluation, Python")
    assert evaluate_eligibility(hw).status == "eligible"
    assert evaluate_eligibility(ml).status == "eligible"
    assert is_relevant(hw, 12, target_year=2027)
    assert is_relevant(ml, 12, target_year=2027)


def test_skillbridge_is_ineligible_without_eligibility_flag():
    skillbridge = _job("Micron DOW SkillBridge Intern – Semiconductor Equipment Technician", "DOD SkillBridge intern")
    assert evaluate_eligibility(skillbridge).status == "ineligible"


def test_grad_window_excluding_may_2029_is_ineligible():
    restricted = _job(
        "Hardware Engineering Intern",
        "Must graduate before December 2028 and have no more than one year remaining.",
    )
    assert evaluate_eligibility(restricted).status == "ineligible"


def test_sector_scores_distinguish_hardware_and_data_ml():
    hardware = _job("NVIDIA Hardware ASIC Design Intern", "RTL verification, digital design, computer architecture, SystemVerilog")
    data_ml = _job("Microsoft AI Software Engineering Intern", "Python, machine learning, deep learning, model evaluation")
    hw_scores = score_sector_fit(hardware)
    ml_scores = score_sector_fit(data_ml)
    assert hw_scores["hardware"] > hw_scores["swe"]
    assert ml_scores["data_ml"] >= ml_scores["swe"]
    assert relevance_score(hardware, target_year=2027) >= 12
    assert relevance_score(data_ml, target_year=2027) >= 12


def test_short_sector_keywords_match_tokens_not_substrings():
    ordinary_swe = _job(
        "Software Engineering Intern",
        "This is a paid internship with training in APIs and distributed systems.",
    )
    ai_role = _job(
        "AI Engineering Intern",
        "Artificial intelligence and machine learning with Python.",
    )
    assert score_sector_fit(ordinary_swe)["data_ml"] == 0
    assert score_sector_fit(ai_role)["data_ml"] > 0


def test_company_name_never_inflates_sector_fit():
    generic = _job("Engineering Intern", "Bachelor's student opportunity.")
    generic.company = "NVIDIA"
    circuit = _job("Digital Circuit Design Intern", "Bachelor's student opportunity.")
    assert score_sector_fit(generic)["data_ml"] == 0
    assert score_sector_fit(circuit)["hardware"] > score_sector_fit(circuit)["data_ml"]


def test_internal_is_not_misclassified_as_an_internship():
    internal_audit = _job(
        "Vice President, Internal Audit",
        "Full-time leadership role for internal systems and controls.",
    )
    assert evaluate_eligibility(internal_audit).status == "ineligible"
    assert not is_relevant(internal_audit, 12, target_year=2027)
