import re
from dataclasses import dataclass


US = "us"
NON_US = "non_us"
AMBIGUOUS = "ambiguous"

US_STATE_CODES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC","PR",
}

US_STATE_NAMES = {
    "alabama","alaska","arizona","arkansas","california","colorado","connecticut","delaware",
    "florida","georgia","hawaii","idaho","illinois","indiana","iowa","kansas","kentucky",
    "louisiana","maine","maryland","massachusetts","michigan","minnesota","mississippi",
    "missouri","montana","nebraska","nevada","new hampshire","new jersey","new mexico",
    "new york","north carolina","north dakota","ohio","oklahoma","oregon","pennsylvania",
    "rhode island","south carolina","south dakota","tennessee","texas","utah","vermont",
    "virginia","washington","west virginia","wisconsin","wyoming","district of columbia",
    "puerto rico",
}

CANADIAN_PROVINCE_CODES = {"AB","BC","MB","NB","NL","NS","NT","NU","ON","PE","QC","SK","YT"}

# Explicit foreign names are checked before state-only heuristics. This is not
# intended to geocode the world; it catches the common formats returned by ATSs
# and otherwise fails closed as ambiguous.
FOREIGN_COUNTRY_NAMES = {
    "canada","mexico","china","india","israel","ireland","united kingdom","england","scotland",
    "wales","germany","france","spain","italy","japan","south korea","korea","singapore",
    "taiwan","brazil","australia","new zealand","poland","sweden","switzerland","netherlands",
    "belgium","finland","denmark","norway","czech republic","czechia","romania","portugal",
    "austria","hungary","greece","turkey","türkiye","malaysia","philippines","vietnam",
    "thailand","indonesia","united arab emirates","uae","saudi arabia","south africa",
}


@dataclass(frozen=True)
class LocationDecision:
    status: str
    reason: str

    @property
    def is_us(self) -> bool:
        return self.status == US


def _norm_token(value: str) -> str:
    return re.sub(r"[^A-Za-z]", "", value or "").upper()


def _has_explicit_us(text: str) -> bool:
    low = text.lower()
    if "united states" in low:
        return True
    if re.search(r"\bU\.?S\.?A?\.?\b", text, re.I):
        return True
    # Explicit country-code token in comma/pipe/dash separated ATS text.
    return bool(re.search(r"(?:^|[,|;/\-]\s*)US(?:A)?(?:$|\s*[,|;/\-])", text, re.I))


def _has_foreign_country_name(text: str) -> str | None:
    low = re.sub(r"\s+", " ", text.lower())
    for name in FOREIGN_COUNTRY_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", low):
            return name
    return None


def _classify_one(location: str, source_type: str = "") -> LocationDecision:
    raw = (location or "").strip()
    if not raw:
        return LocationDecision(AMBIGUOUS, "location missing")

    if _has_explicit_us(raw):
        return LocationDecision(US, "explicit United States marker")

    foreign_name = _has_foreign_country_name(raw)
    if foreign_name:
        return LocationDecision(NON_US, f"explicit foreign country: {foreign_name}")

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    final = _norm_token(parts[-1]) if parts else ""

    # Eightfold standardizedLocations uses the final short token as the country
    # code (e.g. "Vancouver, BC, CA" and "San Diego, CA, US"). Treating that
    # token as a US state would turn Israel's IL or Canada's CA into false US
    # positives, so Eightfold is deliberately strict.
    if source_type == "eightfold":
        if final in {"US", "USA"}:
            return LocationDecision(US, "Eightfold country code is US")
        if re.fullmatch(r"[A-Z]{2,3}", final or ""):
            return LocationDecision(NON_US, f"Eightfold country code is {final}")
        return LocationDecision(AMBIGUOUS, "Eightfold location lacks an explicit country code")

    # Workday commonly returns "City, ST" for US roles. Accept those only after
    # explicit foreign markers have been ruled out. Canadian province codes are
    # rejected before the state-code check.
    if final in CANADIAN_PROVINCE_CODES:
        return LocationDecision(NON_US, f"Canadian province code: {final}")

    if source_type in {"workday", "greenhouse", "lever"}:
        if final in US_STATE_CODES:
            return LocationDecision(US, f"US state/territory code: {final}")
        low = raw.lower()
        if any(re.search(rf"\b{re.escape(state)}\b", low) for state in US_STATE_NAMES):
            return LocationDecision(US, "US state/territory name")

    return LocationDecision(AMBIGUOUS, "location is not confidently US")


def classify_us_location(location: str, source_type: str = "") -> LocationDecision:
    """Classify an ATS location without external API calls.

    Multiple locations are commonly joined with ``|``. A posting is US-eligible
    if at least one listed location is confidently US. If none is US and at
    least one is ambiguous, fail closed as ambiguous rather than spend credits.
    """
    chunks = [c.strip() for c in re.split(r"\s*[|;]\s*", location or "") if c.strip()]
    if not chunks:
        return LocationDecision(AMBIGUOUS, "location missing")

    decisions = [_classify_one(chunk, source_type) for chunk in chunks]
    for d in decisions:
        if d.status == US:
            return d
    if any(d.status == AMBIGUOUS for d in decisions):
        return LocationDecision(AMBIGUOUS, "no US location found; at least one location is ambiguous")
    return LocationDecision(NON_US, "all listed locations are outside the US")
