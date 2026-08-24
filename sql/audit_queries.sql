-- Confidence/action distribution
SELECT decision, confidence, recommended_action, COUNT(*) AS papers
FROM work_decision
GROUP BY decision, confidence, recommended_action
ORDER BY decision, confidence, recommended_action;

-- Professors with the largest automatic-drop count
SELECT professor_token, subject, phd_year, debut_year, seed_source,
       paper_count, auto_drop_count, review_count
FROM professor_summary
ORDER BY auto_drop_count DESC, professor_token
LIMIT 100;

-- ORCID-only cases must never be silently kept by ORCID alone
SELECT professor_token, work_id, publication_year, title,
       decision, confidence, recommended_action, reasons_json
FROM work_decision
WHERE orcid_only = 1
ORDER BY professor_token, publication_year, work_id;

-- Review queue for one anonymized professor
SELECT publication_year, work_id, title, topic_name, field_name,
       topic_similarity, core_coauthor_connected, institution_connected,
       graph_path_connected, decision, confidence, reasons_json
FROM work_decision
WHERE professor_token = :professor_token
  AND recommended_action = 'review'
ORDER BY publication_year, work_id;

-- Use against final_identity_overlay.sqlite
SELECT final_action, final_confidence, COUNT(*) AS papers
FROM final_identity_decision
GROUP BY final_action, final_confidence
ORDER BY final_action, final_confidence;
