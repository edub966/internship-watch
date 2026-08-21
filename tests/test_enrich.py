import unittest

from src.enrich import _score, build_queries
from src.models import Job


class EnrichTests(unittest.TestCase):
    def setUp(self):
        self.job = Job(
            company="NVIDIA",
            external_id="JR12345",
            title="ASIC Design Intern",
            location="Santa Clara, CA",
            url="https://example.com/job",
            source="test",
            description="RTL verification internship",
        )

    def test_queries_include_req_recruiting_and_uf(self):
        queries = build_queries(self.job)
        self.assertEqual(len(queries), 3)
        self.assertIn("JR12345", queries[0])
        self.assertIn("recruiter", queries[1])
        self.assertIn("University of Florida", queries[2])

    def test_matching_post_scores_above_generic_profile(self):
        post = _score(
            self.job,
            "NVIDIA ASIC Design Intern JR12345 hiring",
            "Hiring interns for ASIC and RTL work",
            "https://www.linkedin.com/posts/example",
            0.9,
        )
        generic = _score(
            self.job,
            "Person at NVIDIA",
            "Engineer",
            "https://www.linkedin.com/in/example",
            0.5,
        )
        self.assertGreater(post, generic)


if __name__ == "__main__":
    unittest.main()
