import pytest

import src.main as main
from src.filtering import (
    evaluate_eligibility,
    is_relevant,
    target_cycle_mismatch_reason,
)
from src.models import Job


def _job(title, description="", external_id="REQ"):
    return Job(
        company="ExampleCo",
        external_id=external_id,
        title=title,
        location="Austin, TX, United States",
        url="https://example.com/job",
        source="test",
        description=description,
    )


@pytest.mark.parametrize("external_id", ["10476811", "10476812", "10476813"])
def test_regular_amazon_engineer_is_not_rescued_by_intern_interview_text(external_id):
    job = _job(
        "Electrical and Control Design Engineer, OneMHS - Electrical and Control Engineering, Arch and Design",
        (
            "Full-time Hardware Development Engineer I. Work with internal partners, "
            "participate in intern interviews, and help train interns."
        ),
        external_id,
    )
    result = evaluate_eligibility(job)
    assert result.status == "ineligible"
    assert result.reasons == ["title does not identify an internship"]
    assert not is_relevant(job, 12, target_year=2027)
    assert job.score == -50


@pytest.mark.parametrize("external_id", ["10420146", "10464138"])
def test_amazon_year_round_jr_developer_program_is_excluded(external_id):
    job = _job(
        "Jr. Software Development Engineer - Santa Barbara, CA, Jr. Developer Program",
        "Students join this year-round internship program and work as a year-round intern.",
        external_id,
    )
    assert evaluate_eligibility(job).status == "ineligible"
    assert target_cycle_mismatch_reason(job, 2027) == (
        "year-round or six-month program excluded; only Summer internships are enabled"
    )
    assert not is_relevant(job, 12, target_year=2027)


def test_graduate_intern_in_title_is_a_hard_rejection_without_degree_detail():
    job = _job(
        "Physical Design Engineering Graduate Intern",
        "ASIC physical design and RTL verification.",
        "JR0283509",
    )
    result = evaluate_eligibility(job)
    assert result.status == "ineligible"
    assert "graduate internship title" in result.reasons
    assert not is_relevant(job, 12, target_year=2027)


@pytest.mark.parametrize("season", ["Winter", "Spring", "Fall"])
def test_off_term_2027_internships_are_excluded(season):
    job = _job(
        f"ASIC Design Intern - {season} 2027",
        "Bachelor's student working on RTL and digital design.",
    )
    assert "off-term" in target_cycle_mismatch_reason(job, 2027)
    assert not is_relevant(job, 12, target_year=2027)


@pytest.mark.parametrize("title,description", [
    ("ASIC Design Intern Co-op Summer 2027", "Bachelor's student; RTL verification."),
    ("ASIC Design Intern 2027", "This is a six-month internship working on RTL."),
    ("ASIC Design Intern 2027", "This is an academic-year internship working on RTL."),
])
def test_coops_and_long_duration_programs_are_excluded(title, description):
    job = _job(title, description)
    assert not is_relevant(job, 12, target_year=2027)


def test_undated_internship_is_not_assumed_to_be_summer_2027():
    job = _job(
        "ASIC Design Intern",
        "Bachelor's student working on RTL and computer architecture.",
    )
    assert target_cycle_mismatch_reason(job, 2027) == "Summer 2027 cycle is not explicit"
    assert not is_relevant(job, 12, target_year=2027)
    assert job.score == -30


@pytest.mark.parametrize("job", [
    _job("ASIC Design Intern - Summer 2027", "Bachelor's student; RTL verification."),
    _job("NVIDIA 2027 Internships: Hardware Verification", "Bachelor's student; SystemVerilog and RTL."),
    _job("Machine Learning Intern", "Summer 2027 internship for Bachelor's students using Python and PyTorch."),
    _job("Undergraduate Hardware Intern 2027", "Bachelor's student; ASIC and computer architecture."),
])
def test_explicit_summer_or_2027_internship_pool_remains_relevant(job):
    assert target_cycle_mismatch_reason(job, 2027) is None
    assert evaluate_eligibility(job).status == "eligible"
    assert is_relevant(job, 12, target_year=2027)


def test_production_eligibility_guard_reports_specific_rejection_reason():
    config = {"name": "ExampleCo", "type": "workday", "target_year": 2027, "us_only": True}
    regular = _job(
        "Electrical and Control Design Engineer",
        "Interview interns and work on embedded hardware.",
    )
    off_term = _job(
        "Embedded Software Intern - Winter 2027",
        "Bachelor's student using C++ and embedded Linux.",
    )
    assert main._eligibility(regular, config, 12) == (False, "title does not identify an internship")
    assert "off-term internship excluded" in main._eligibility(off_term, config, 12)[1]
