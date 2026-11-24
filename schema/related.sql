-- Related-word suggestions for one query word, with the evidence for each.
-- Bind :word_id (resolved from the user's text via word.norm / word_form.norm / word_fts).
--
-- Two kinds of suggestion:
--   direct : an edge leaves the query word        (translation, synonym, antonym, ...)
--   pivot  : query -translation-> T -translation-> X   (X shares a translation with the query)
--            en 'bank'  -> ta 'கரை' -> en 'shore'      => 'shore' is a synonym candidate
--            ta 'கரை'   -> en 'bank' -> ta 'வங்கி'      => Tamil words that share an English meaning
--            Extends Tamil synonym coverage beyond Wiktionary's explicit ~27k synonym links.
--
-- GUARD against polysemy: bank -> {வங்கி (money), கரை (river)} must not make
-- வங்கி and கரை synonyms. A candidate supported ONLY by pivots needs >= 2 distinct
-- pivot words (HAVING below). Direct edges are always kept.
--
-- Score = sum over evidence of (source.trust * entry.quality * relation.weight * kind_factor)
-- so more independent sources and better sources rank higher. Tune factors in code, not here.

WITH direct AS (
    SELECT r.dst_word_id AS cand_id,
           r.rel         AS how,
           'direct'      AS kind,
           r.entry_id    AS entry1_id,
           NULL          AS entry2_id,
           NULL          AS via_word_id,
           r.weight * s.trust * e.quality
             * CASE r.method WHEN 'explicit'    THEN 1.0   -- source stated it
                             WHEN 'whole_gloss' THEN 0.9
                             WHEN 'marker'      THEN 0.8
                             WHEN 'short_gloss' THEN 0.7
                             ELSE 0.6 END                  -- list_item: heuristic split
           AS ev
    FROM relation r
    JOIN entry  e ON e.id = r.entry_id
    JOIN source s ON s.id = e.source_id
    WHERE r.src_word_id = :word_id
),
pivot AS (
    SELECT r2.dst_word_id AS cand_id,
           'pivot'        AS how,
           'pivot'        AS kind,
           r1.entry_id    AS entry1_id,
           r2.entry_id    AS entry2_id,
           r1.dst_word_id AS via_word_id,
           r1.weight * r2.weight * s1.trust * s2.trust * e1.quality * e2.quality * 0.5 AS ev
    FROM relation r1
    JOIN relation r2 ON r2.src_word_id = r1.dst_word_id AND r2.rel = 'translation'
    JOIN entry e1 ON e1.id = r1.entry_id  JOIN source s1 ON s1.id = e1.source_id
    JOIN entry e2 ON e2.id = r2.entry_id  JOIN source s2 ON s2.id = e2.source_id
    WHERE r1.src_word_id = :word_id AND r1.rel = 'translation'
      AND r2.dst_word_id <> :word_id
),
ev AS (SELECT * FROM direct UNION ALL SELECT * FROM pivot)
SELECT w.id, w.lang, w.text,
       group_concat(DISTINCT ev.how)          AS relations,
       SUM(ev.ev)                             AS score,
       COUNT(DISTINCT e1.source_id)           AS n_sources,
       COUNT(DISTINCT ev.via_word_id)         AS n_pivots,
       -- evidence rows for display: JSON array, one object per supporting entry
       json_group_array(json_object(
           'kind',    ev.kind,
           'rel',     ev.how,
           'via',     vw.text,
           'source',  s1.name,
           'license', s1.license,
           'url',     COALESCE(e1.source_url, s1.url),
           'ref',     e1.source_ref,
           'gloss',   e1.gloss,
           'context', e1.context,
           -- second leg of a pivot (NULL for direct)
           'via_source', s2.name,
           'via_gloss',  e2.gloss
       ))                                     AS evidence
FROM ev
JOIN word  w  ON w.id  = ev.cand_id
JOIN entry e1 ON e1.id = ev.entry1_id
JOIN source s1 ON s1.id = e1.source_id
LEFT JOIN word vw ON vw.id = ev.via_word_id
LEFT JOIN entry  e2 ON e2.id = ev.entry2_id
LEFT JOIN source s2 ON s2.id = e2.source_id
GROUP BY w.id
HAVING SUM(ev.kind = 'direct') > 0 OR COUNT(DISTINCT ev.via_word_id) >= 2
ORDER BY score DESC
LIMIT 20;
