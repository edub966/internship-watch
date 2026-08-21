from src.locations import AMBIGUOUS, NON_US, US, classify_us_location


def test_eightfold_us_country_code_is_accepted():
    d = classify_us_location("San Diego, CA, US", "eightfold")
    assert d.status == US


def test_eightfold_foreign_locations_are_rejected():
    samples = [
        "Shanghai, Shanghai, CN",
        "Chengdu, Sichuan, CN",
        "Tijuana, B.C., MX",
        "Tirat Carmel, Haifa, IL",
        "Cork, County Cork, IE",
    ]
    for loc in samples:
        assert classify_us_location(loc, "eightfold").status == NON_US, loc


def test_eightfold_two_part_country_code_does_not_get_mistaken_for_state():
    # IL is both Illinois' state code and Israel's ISO country code. On
    # Eightfold standardizedLocations the trailing short token is country.
    assert classify_us_location("Tirat Carmel, IL", "eightfold").status == NON_US


def test_workday_city_state_is_accepted_for_us_roles():
    assert classify_us_location("Santa Clara, CA", "workday").status == US
    assert classify_us_location("Austin, TX", "workday").status == US


def test_workday_canadian_province_is_rejected():
    assert classify_us_location("Toronto, ON", "workday").status == NON_US
    assert classify_us_location("Vancouver, BC", "workday").status == NON_US


def test_remote_us_is_accepted_but_bare_remote_fails_closed():
    assert classify_us_location("Remote - United States", "workday").status == US
    assert classify_us_location("US - Remote", "workday").status == US
    assert classify_us_location("Remote", "workday").status == AMBIGUOUS


def test_multi_location_is_eligible_if_any_location_is_us():
    d = classify_us_location("Bangalore, Karnataka, IN | San Diego, CA, US", "eightfold")
    assert d.status == US


def test_unknown_location_fails_closed():
    assert classify_us_location("North America", "workday").status == AMBIGUOUS
    assert classify_us_location("", "workday").status == AMBIGUOUS
