ALTER TABLE corpus_releases ADD COLUMN csv_format TEXT;

-- Releases created before this migration stored their CSV layout in note.
UPDATE corpus_releases
SET csv_format = json_extract(note, '$.csv_format'),
    note = NULLIF(json_remove(note, '$.csv_format'), '{}')
WHERE CASE WHEN json_valid(note) THEN
    json_type(note, '$.csv_format.law') = 'object'
    AND json_type(note, '$.csv_format.combined') = 'object'
ELSE 0 END;
