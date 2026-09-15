// Paste into DevTools Console (F12) on www.udemy.com while logged in.
// Downloads all courses from the account to udemy_courses.json.
// Archived: set ARCHIVED to true and run again (downloads udemy_courses_archived.json).
(async () => {
  const ARCHIVED = false;
  const fields = 'id,title,url,headline,content_info,estimated_content_length,num_lectures,primary_category,primary_subcategory,visible_instructors,locale,completion_ratio,enrollment_time,is_practice_test_course';
  let page = 1, all = [];
  while (true) {
    const r = await fetch(`/api-2.0/users/me/subscribed-courses/?page=${page}&page_size=100&is_archived=${ARCHIVED}&fields[course]=${fields}`, { credentials: 'include' });
    if (!r.ok) { console.error('Error', r.status, await r.text()); return; }
    const j = await r.json();
    all.push(...j.results);
    console.log(`page ${page}: ${all.length}/${j.count}`);
    if (!j.next) break;
    page++;
  }
  const blob = new Blob([JSON.stringify(all, null, 2)], { type: 'application/json' });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: ARCHIVED ? 'udemy_courses_archived.json' : 'udemy_courses.json' });
  a.click();
  console.log(`Downloaded ${all.length} courses`);
})();
