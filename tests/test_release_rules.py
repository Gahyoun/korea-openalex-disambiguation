import unittest
from collections import Counter

from koad.release_rules import (accept_candidate, allowed_fields, career_consistency, name_matches,
                                name_signatures, norm_institution)

CAREER = ["Korea Advanced Institute of Science and Technology", "Umeå University",
          "Korea Institute for Advanced Study", "Gyeongsang National University"]


def work(wid, institutions=(), coauthors=(), subfield="Statistical and Nonlinear Physics",
         field="Physics and Astronomy", domain="Physical Sciences"):
    return {"id": wid, "institutions": list(institutions), "coauthors": list(coauthors),
            "subfield": subfield, "field": field, "domain": domain}


FOOD = dict(subfield="Food Science", field="Agricultural and Biological Sciences", domain="Life Sciences")


class CareerConsistencyTests(unittest.TestCase):
    """A network scientist's OpenAlex ID also held a namesake's food-science papers."""

    def setUp(self):
        self.works = [
            work("W1", ["Korea Advanced Institute of Science and Technology"], ["A-kim", "A-jeong"]),
            work("W2", ["Umea University"], ["A-holme", "A-porter"]),
            work("W3", ["Korea Institute for Advanced Study"], ["A-porter", "A-cucuringu"]),
            work("W4", ["Gyeongsang National University (Main Campus)"], ["A-son", "A-park"],
                 "Molecular Biology", "Biochemistry, Genetics and Molecular Biology", "Life Sciences"),
            work("W5", ["University of Oxford"], ["A-porter"]),                  # visit, home domain
            work("W6", [], ["A-food1", "A-food2"], **FOOD),                     # nobody shared, new subfield
            work("W7", ["Chungbuk National University"], ["A-park", "A-food3"], **FOOD),   # one shared (common name)
            work("W8", [], ["A-food4"], "Molecular Biology", "Biochemistry, Genetics and Molecular Biology", "Life Sciences"),
            work("W9", [], [], **FOOD),                                         # solo: protected
        ]

    def test_physicist(self):
        self.assertEqual(career_consistency(self.works, CAREER, "physics"),
                         {"W6": "career_subfield_mismatch", "W7": "career_field_mismatch"})

    def test_home_domain_never_judged(self):
        self.assertEqual(career_consistency(self.works, CAREER, "biology"), {})

    def test_needs_three_anchors(self):
        self.assertEqual(career_consistency(self.works[4:], CAREER, "physics"), {})

    def test_institution_normalisation(self):
        self.assertEqual(norm_institution("Umeå University"), norm_institution("UMEA university"))
        self.assertEqual(norm_institution("Hanyang University (ERICA Campus)"), norm_institution("Hanyang University"))


class CandidateTests(unittest.TestCase):
    def test_names(self):
        sigs = name_signatures("Sang-Kwon Lee")
        self.assertTrue(name_matches("Sang‐Kwon Lee".replace("‐", "-"), sigs))
        self.assertTrue(name_matches("S.-K. Lee", sigs))
        self.assertFalse(name_matches("Sangmin Lee", sigs))
        self.assertFalse(name_matches("Lee", sigs))

    def test_allowed_fields(self):
        self.assertEqual(allowed_fields(Counter({"Physics and Astronomy": 20, "Materials Science": 8, "Medicine": 1})),
                         {"Physics and Astronomy", "Materials Science"})

    def test_accept_candidate(self):
        segs = [("Inha University", 2018, 2020), ("Pusan National University", 2025, 2026)]
        sigs = name_signatures("Mi Jin Lee")
        ok = {"Physics and Astronomy"}
        w = {"publication_year": 2021, "type": "article", "primary_topic": {"field": {"display_name": "Physics and Astronomy"}}}
        a = {"raw_author_name": "Mi Jin Lee", "institutions": [{"display_name": "Inha University"}]}
        self.assertTrue(accept_candidate(w, a, segs, ok, sigs))                    # within segment + 1 year
        self.assertFalse(accept_candidate({**w, "publication_year": 2023}, a, segs, ok, sigs))
        self.assertFalse(accept_candidate(w, {**a, "raw_author_name": "Min Ji Lee"}, segs, ok, sigs))
        self.assertFalse(accept_candidate({**w, "primary_topic": {"field": {"display_name": "Medicine"}}}, a, segs, ok, sigs))


if __name__ == "__main__":
    unittest.main()
