import json
from stroj import db

rows = db.query("""
SELECT p.slug, p.title, p.points, p.visible,
       (SELECT group_concat(pt.type, ', ')
          FROM problem_types pt
         WHERE pt.problem_id = p.id) AS types,
       substr(replace(p.statement, char(10), ' '), 1, 700) AS gist
  FROM problems p
 ORDER BY p.points, p.slug
""")
for row in rows:
    print(json.dumps(dict(row), ensure_ascii=False))
