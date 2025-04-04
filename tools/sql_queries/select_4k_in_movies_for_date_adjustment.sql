-- Select all of the movies which exist in 4k library, and adjust their dates to match the original release date
select title, added_at, originally_available_at from metadata_items
where id IN (
select m.id
from metadata_items m
join metadata_items f on
m.title = f.title
and f.library_section_id = '50'
and m.library_section_id = '1'
and m.added_at != m.originally_available_at
)
;
