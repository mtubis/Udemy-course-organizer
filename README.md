Udemy course organizer

1. Log in to udemy.com, open Developer Tools (F12) → Console, paste `export_courses.js` → this will download `udemy_courses.json`.
2. Install dependencies and set API keys:

    - For Anthropic (default):
       ```bash
       pip install anthropic openpyxl
       export ANTHROPIC_API_KEY=...
       ```

    - Or for OpenAI:
       ```bash
       pip install openai openpyxl
       export OPENAI_API_KEY=...
       ```

    You can also set the preferred provider via `UD_PROVIDER` env var (values: `anthropic` or `openai`).

3. Run `python udemy_organize.py udemy_courses.json` (optionally specify provider):

    ```bash
    # default (Anthropic)
    python udemy_organize.py udemy_courses.json

    # explicitly use OpenAI
    python udemy_organize.py udemy_courses.json --provider openai
    ```
   → produces `udemy_courses.xlsx` (sheets: Courses + Summary) and `udemy_courses.html` (search + filters).

Working files:
- `taxonomy.json` – categories generated on first run; you can edit them manually (add/remove/rename), then use `--reclassify`.
- `ai_cache.json` – classification per course id; subsequent runs after new purchases will classify only new courses.

Options: `--no-ai` (use Udemy categories, without LLM), `--batch-size 30`, `--model claude-sonnet-5`,
`--skip-practice-tests`, `--out name`.

You can merge several exports (active + archived): `jq -s 'add' udemy_courses.json udemy_courses_archived.json > all.json`.
