from src.filtering import is_relevant, matches_target_year
from src.models import Job


def job(title):
    return Job("Qualcomm", "1", title, "San Diego, CA, US", "x", "test", description="embedded firmware")


def test_explicit_old_cycle_is_rejected():
    j = job("2026 Intern-Embedded SW Engineer")
    assert not matches_target_year(j, 2027)
    assert not is_relevant(j, 12, target_year=2027)


def test_fy27_and_2027_are_accepted():
    assert matches_target_year(job("FY27 Intern - Digital Design Verification"), 2027)
    assert matches_target_year(job("Software Engineering Intern - Summer 2027"), 2027)


def test_no_year_fails_closed_for_a_configured_target_cycle():
    assert not matches_target_year(job("Hardware Engineering Intern"), 2027)
