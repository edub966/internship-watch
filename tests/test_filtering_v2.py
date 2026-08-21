from src.filtering import is_relevant, relevance_score
from src.models import Job


def j(title, description="", location="US"):
    return Job("TestCo", title, title, location, "https://example.com", "test", description=description)


def test_hardware_intern_is_relevant():
    job = j("ASIC Design Intern")
    assert is_relevant(job)
    assert job.score >= 12


def test_software_engineering_intern_is_relevant():
    job = j("Software Engineering Intern")
    assert is_relevant(job)


def test_marketing_intern_is_not_relevant():
    job = j("Marketing Intern")
    assert not is_relevant(job)


def test_non_intern_engineer_is_not_relevant():
    job = j("Embedded Software Engineer")
    assert not is_relevant(job)
