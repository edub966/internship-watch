import yaml

from src.main import build_source
from src.sources.eightfold import EightfoldSource
from src.sources.greenhouse import GreenhouseSource
from src.sources.lever import LeverSource
from src.sources.workday import WorkdaySource


NEW_COMPANIES = {
    "Databricks", "Stripe", "Palantir",
    "Applied Materials", "KLA", "Lam Research", "GlobalFoundries",
    "Microchip", "Roblox", "Autodesk",
    "Northrop Grumman", "RTX", "Capital One", "Mastercard", "HubSpot",
    "Cox Enterprises", "Home Depot", "Fiserv", "Equifax",
}


def _config():
    with open("config/companies.yaml", "r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)


def test_expansion_adds_19_verified_companies_without_staged_overlap():
    config = _config()
    active = {company["name"]: company for company in config["companies"]}
    staged = {company["name"] for company in config["staged_companies"]}
    assert len(active) == 38
    assert NEW_COMPANIES <= set(active)
    assert not (set(active) & staged)
    for name in NEW_COMPANIES:
        build_source(active[name])


def test_expansion_uses_only_shared_provider_adapters():
    active = {company["name"]: company for company in _config()["companies"]}

    for name in ("Databricks", "Stripe", "Roblox", "HubSpot"):
        source = build_source(active[name])
        assert isinstance(source, GreenhouseSource)
        assert source.title_terms

    assert isinstance(build_source(active["Palantir"]), LeverSource)
    for name in ("Lam Research", "GlobalFoundries"):
        assert isinstance(build_source(active[name]), EightfoldSource)

    faceted_workday = NEW_COMPANIES - {
        "Databricks", "Stripe", "Roblox", "HubSpot", "Palantir",
        "Lam Research", "GlobalFoundries",
    }
    for name in faceted_workday:
        source = build_source(active[name])
        assert isinstance(source, WorkdaySource)
        assert source.facet_terms
