import yaml

from src.main import build_source
from src.sources.greenhouse import GreenhouseSource
from src.sources.smartrecruiters import SmartRecruitersSource


def test_active_roster_has_tier_and_priority_sector_metadata():
    with open("config/companies.yaml", "r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    active = [company for company in config["companies"] if company.get("enabled", True)]
    assert len(active) == 38
    assert len({company["name"] for company in active}) == len(active)
    for company in active:
        assert company["company_tier"] in {"A", "B", "C"}
        assert company["priority_sectors"]
        assert set(company["priority_sectors"]) <= {"hardware", "swe", "data_ml"}
        build_source(company)


def test_phase3_commissions_five_companies_through_shared_providers():
    with open("config/companies.yaml", "r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    by_name = {company["name"]: company for company in config["companies"]}

    for name in ("Cloudflare", "Datadog", "MongoDB"):
        source = build_source(by_name[name])
        assert isinstance(source, GreenhouseSource)
        assert source.title_terms

    for name in ("ServiceNow", "Bosch"):
        source = build_source(by_name[name])
        assert isinstance(source, SmartRecruitersSource)
        assert source.country == "us"

    staged_names = {company["name"] for company in config.get("staged_companies", [])}
    assert not (set(by_name) & staged_names)
