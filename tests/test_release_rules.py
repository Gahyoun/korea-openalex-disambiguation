import unittest
from collections import Counter

from koad.release_rules import (accept_candidate, allowed_fields, career_consistency, corroborated, name_matches,
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


class CorroborationTests(unittest.TestCase):
    """A statistical physicist's ID also held a food scientist's papers, one sharing a coauthor ID."""

    def test_identity_and_subject_both_required(self):
        segs = [("Sungkyunkwan University", 2011, 2016), ("Max Planck Institute for Evolutionary Biology", 2017, 2021),
                ("Inha University", 2022, 2026)]
        sp = dict(subfield="Statistical and Nonlinear Physics", domain="Physical Sciences")
        food = dict(subfield="Food Science", domain="Life Sciences")
        works = [
            dict(id="W1", year=2014, institutions=["Sungkyunkwan University"], coauthors=["A-kim"], **sp),
            dict(id="W2", year=2019, institutions=["Max Planck Institute for Evolutionary Biology"], coauthors=["A-t"],
                 subfield="Genetics", domain="Life Sciences"),
            dict(id="W3", year=2024, institutions=["Inha University"], coauthors=["A-kim"], **sp),
            dict(id="W4", year=2020, institutions=[], coauthors=["A-t"], subfield="Genetics", domain="Life Sciences"),
            dict(id="W5", year=2013, institutions=[], coauthors=[], **food),
            dict(id="W6", year=2015, institutions=[], coauthors=["A-kim"], **food),
            dict(id="W7", year=2016, institutions=[], coauthors=[], subfield="Pollution", domain="Physical Sciences"),
        ]
        self.assertEqual(corroborated(works, segs, "physics"),
                         {"W5": "uncorroborated", "W6": "uncorroborated", "W7": "uncorroborated"})


class CareerConflictTests(unittest.TestCase):
    """During the PhD at one university, a paper from another university under the same name is a
    namesake's; co-located institutes, the same institution family, moves and real collaborations
    are not."""

    def test_conflict(self):
        from koad.release_rules import career_conflict
        career = [dict(stage='doctoral', institution='Sungkyunkwan University', start_year=2011, end_year=2016, is_estimated=True),
                  dict(stage='postdoc', institution='Max Planck Institute for Evolutionary Biology', start_year=2017, end_year=2021),
                  dict(stage='faculty', institution='Inha University', start_year=2022, end_year=2026)]
        meta = {'I-skku': {'type': 'education', 'lineage': ['I-skku']}, 'I-inha': {'type': 'education', 'lineage': ['I-inha']},
                'I-cbnu': {'type': 'education', 'lineage': ['I-cbnu']}, 'I-pnu': {'type': 'education', 'lineage': ['I-pnu']},
                'I-ibs': {'type': 'facility', 'lineage': ['I-ibs']}, 'I-skkumed': {'type': 'healthcare', 'lineage': ['I-skkumed', 'I-skku']},
                'I-hosp': {'type': 'healthcare', 'lineage': ['I-hosp']}}
        sp = 'Statistical and Nonlinear Physics'

        def w(i, y, inst, iid, co=(), sub=sp):
            return dict(id=i, year=y, institutions=[inst], institution_ids=[iid], coauthors=list(co), subfield=sub)
        works = [w('A1', 2013, 'Sungkyunkwan University', 'I-skku', ['A-kim', 'A-lee']),
                 w('A2', 2014, 'Sungkyunkwan University', 'I-skku', ['A-kim']),
                 w('A3', 2023, 'Inha University', 'I-inha', ['A-kim', 'A-son']),
                 w('N1', 2015, 'Chungbuk National University', 'I-cbnu', [], 'Food Science'),   # PhD years, other university
                 w('K10', 2014, 'Pusan National University', 'I-pnu', ['A-x']),                # PhD years, same subfield
                 w('N2', 2024, 'Pusan National University', 'I-pnu', ['A-x']),                 # faculty years, no shared coauthor
                 w('N3', 2014, 'Samsung Hospital', 'I-hosp', ['A-kim'], 'Oncology'),           # one shared, other subfield
                 w('K1', 2014, 'Institute for Basic Science', 'I-ibs'),                          # co-located institute
                 w('K2', 2014, 'Sungkyunkwan University School of Medicine', 'I-skkumed'),      # same lineage
                 w('K3', 2016, 'Pusan National University', 'I-pnu'),                           # boundary year (PhD end)
                 w('K7', 2012, 'Pusan National University', 'I-pnu'),                           # 2nd year of the PhD
                 w('K8', 2023, 'Pusan National University', 'I-pnu'),                           # 2nd faculty year (publication lag)
                 w('K9', 2025, 'Pusan National University', 'I-pnu'),                           # year before a faculty move
                 w('K4', 2014, 'Pusan National University', 'I-pnu', ['A-kim', 'A-lee']),      # two shared coauthors
                 w('K5', 2019, 'Pusan National University', 'I-pnu'),                           # postdoc years: not whereabouts
                 w('K6', 2014, 'Pusan National University', 'I-pnu', ['A-kim'])]               # one shared, same subfield
        self.assertEqual(career_conflict(works, career, 'physics', meta),
                         {'N1': 'career_conflict', 'N2': 'career_conflict', 'N3': 'career_conflict'})

    def test_core_names(self):
        from koad.release_rules import same_institution
        self.assertTrue(same_institution('New York at Stony Brook', 'Stony Brook University'))
        self.assertTrue(same_institution('Stanford Medicine', 'Stanford University'))
        self.assertFalse(same_institution('Seoul National University', 'University of Seoul'))
        self.assertFalse(same_institution('Kunsan National University', 'Ulsan National Institute of Science and Technology'))


if __name__ == "__main__":
    unittest.main()
