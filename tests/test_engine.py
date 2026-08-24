from __future__ import annotations

import unittest

from koad.engine import (
    classify_professor, normalize_openalex_author_id, openalex_author_ids,
)
from koad.model import Work


CONFIG = {
    "doctoral_window_years": 5,
    "topic_similarity_keep": 0.18,
    "topic_similarity_far": 0.07,
    "graph_topic_similarity": 0.30,
    "near_duplicate_similarity": 0.72,
    "graph_max_topic_year_gap": 4,
    "graph_max_semantic_year_gap": 6,
    "graph_semantic_similarity": 0.08,
    "max_shared_entity_component": 80,
    "auto_drop_confidence": ["high"],
    "auto_keep_confidence": ["high", "medium"],
    "obvious_unrelated_title_phrases": ["construction site", "supreme court"],
    "semantic_families": {
        "network_science": {
            "primary": ["network", "graph"],
            "context": ["community", "complex", "core", "fungal", "mesoscale", "mutualistic", "periphery", "sample", "statistical"],
        },
        "mathematical_biology": {
            "primary": ["ecosystem", "fungal", "mutualistic", "population"],
            "context": ["dynamics", "model", "network"],
        },
    },
    "subject_fields": {
        "physics": ["physics and astronomy", "mathematics", "computer science"],
    },
}


def work(
    work_id: str,
    year: int,
    title: str,
    field_name: str,
    *,
    topic_id: str,
    topic_name: str,
    coauthors: set[str] | None = None,
    institutions: set[str] | None = None,
    units: set[str] | None = None,
    orcid: bool = False,
    prior: str = "keep",
) -> Work:
    return Work(
        professor_uid="prof:test", work_id=work_id, publication_year=year,
        publication_date=f"{year}-01-01", title=title, raw_orcid="x" if orcid else None,
        topic_id=topic_id, topic_name=topic_name, subfield_name=None,
        field_name=field_name, domain_name=field_name, prior_decision=prior,
        prior_score=0, anchor_flags={"orcid_exact": orcid},
        coauthors=coauthors or set(), institutions=institutions or set(),
        institution_names=set(), unit_labels=units or set(),
    )


class IdentityClassifierTests(unittest.TestCase):
    def test_openalex_id_normalization_accepts_id_and_url(self):
        self.assertEqual(normalize_openalex_author_id("a1234567890"), "A1234567890")
        self.assertEqual(
            normalize_openalex_author_id("https://openalex.org/authors/A1234567890"),
            "A1234567890",
        )

    def test_openalex_id_extraction_supports_merged_roster_fields(self):
        self.assertEqual(
            openalex_author_ids("A1234567890; https://openalex.org/A9876543210"),
            {"A1234567890", "A9876543210"},
        )

    def classify(self, works: list[Work], phd_unit: str | None = "kaist", phd_year: int = 2010):
        professor = {
            "professor_uid": "prof:test", "name": "테스트", "subject": "physics",
            "openalex_id": "A1", "phd_year": phd_year,
        }
        decisions, summary = classify_professor(
            professor, works, phd_unit, {"current"}, CONFIG
        )
        return {decision.work_id: decision for decision in decisions}, summary

    def seed(self) -> Work:
        return work(
            "seed", 2005, "Statistical properties of sampled networks",
            "Physics and Astronomy", topic_id="network", topic_name="Network science",
            coauthors={"C1"}, institutions={"kaist"}, units={"Department of Physics"},
        )

    def test_same_university_wrong_department_is_not_a_seed_and_is_dropped(self):
        mechanical = work(
            "mechanical", 2005, "Reliability analysis of mechanical structures",
            "Engineering", topic_id="reliability", topic_name="Structural reliability",
            institutions={"kaist"}, units={"Department of Mechanical Engineering"},
        )
        decisions, summary = self.classify([self.seed(), mechanical])
        self.assertEqual(summary["seed_work_ids"], ["seed"])
        self.assertEqual(decisions["mechanical"].decision, "namesake_paper")
        self.assertEqual(decisions["mechanical"].recommended_action, "drop")

    def test_unlinked_construction_site_work_is_dropped(self):
        construction = work(
            "construction", 2016,
            "Fast 3D terrain reconstruction of a construction site using stereo cameras",
            "Engineering", topic_id="terrain", topic_name="3D reconstruction",
        )
        decisions, _ = self.classify([self.seed(), construction])
        self.assertEqual(decisions["construction"].recommended_action, "drop")

    def test_interdisciplinary_network_work_survives_with_coauthor_path(self):
        network = work(
            "network", 2012, "Community detection in complex social networks",
            "Computer Science", topic_id="communities", topic_name="Complex networks",
            coauthors={"C1"}, institutions={"other"}, units={"Network Science Institute"},
        )
        decisions, _ = self.classify([self.seed(), network])
        self.assertEqual(decisions["network"].decision, "true_paper")
        self.assertEqual(decisions["network"].recommended_action, "keep")

    def test_mutualistic_network_is_connected_by_semantic_bridge(self):
        bridge = work(
            "bridge", 2010, "Mesoscale analyses of fungal networks",
            "Biological Sciences", topic_id="fungal", topic_name="Fungal networks",
            institutions={"other"}, units={"Institute for Complex Systems"},
        )
        ecosystem = work(
            "ecosystem", 2016, "Core-periphery structure in mutualistic networks",
            "Biological Sciences", topic_id="ecosystem", topic_name="Mutualistic ecosystems",
            units={"Department of Ecology"},
        )
        decisions, _ = self.classify([self.seed(), bridge, ecosystem])
        self.assertTrue(decisions["ecosystem"].semantic_bridge_connected)
        self.assertEqual(decisions["ecosystem"].recommended_action, "keep")

    def test_orcid_alone_does_not_rescue_unrelated_law_paper(self):
        law = work(
            "law", 2017, "Supreme Court doctrine and constitutional review",
            "Social Sciences", topic_id="law", topic_name="Constitutional law", orcid=True,
            units={"School of Law"},
        )
        decisions, _ = self.classify([self.seed(), law])
        self.assertTrue(decisions["law"].orcid_only)
        self.assertEqual(decisions["law"].recommended_action, "drop")

    def test_unverified_fallback_seed_cannot_auto_drop_post_debut_paper(self):
        law = work(
            "law", 2017, "Supreme Court doctrine and constitutional review",
            "Social Sciences", topic_id="law", topic_name="Constitutional law",
            units={"School of Law"},
        )
        decisions, summary = self.classify([self.seed(), law], phd_unit=None)
        self.assertNotEqual(summary["seed_source"], "doctoral_institution")
        self.assertEqual(decisions["law"].recommended_action, "review")

    def test_more_than_five_years_before_phd_is_a_hard_drop(self):
        old = work(
            "old", 2004, "Unrelated historical paper", "Social Sciences",
            topic_id="history", topic_name="History", units={"School of History"},
        )
        decisions, _ = self.classify([self.seed(), old])
        self.assertEqual(decisions["old"].confidence, "high")
        self.assertEqual(decisions["old"].recommended_action, "drop")


if __name__ == "__main__":
    unittest.main()
