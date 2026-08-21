import unittest

from src.models import Job
from src.filtering import relevance_score


class FilteringTests(unittest.TestCase):
    def test_ce_intern_scores_high(self):
        j = Job(
            company="ChipCo",
            external_id="1",
            title="ASIC Design Intern",
            location="Austin, TX",
            url="https://example.com/1",
            source="test",
            description="SystemVerilog RTL computer architecture student internship",
        )
        self.assertGreaterEqual(relevance_score(j), 20)

    def test_senior_role_scores_low(self):
        j = Job(
            company="ChipCo",
            external_id="2",
            title="Senior Staff ASIC Engineer",
            location="Austin, TX",
            url="https://example.com/2",
            source="test",
            description="10+ years required",
        )
        self.assertLess(relevance_score(j), 8)


if __name__ == "__main__":
    unittest.main()
