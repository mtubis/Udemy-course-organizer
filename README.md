Udemy Course Organizer

Purpose
-------
This small toolkit helps you turn a Udemy export (a JSON file of your enrolled courses) into a searchable spreadsheet and a lightweight HTML view. The script extracts course metadata, optionally classifies courses into a human-editable taxonomy using an LLM (Anthropic or OpenAI), and writes two convenient outputs:

- `udemy_courses.xlsx` — an Excel workbook with a `Courses` sheet (one row per course) and a `Summary` sheet with aggregated stats.
- `udemy_courses.html` — a browser-searchable interface with filters for quick browsing.

Why this is not fully automatic
--------------------------------
Udemy actively protects its site from automated scraping. This project does not attempt to bypass those protections. Instead, you manually export your courses from your account (a single, explicit action) and then run the local script to process the export. This keeps the workflow simple, safer for your account, and uses an unofficial internal endpoint from your own logged-in browser session; may stop working if Udemy changes it; use at your own discretion.

Expected workflow and results
-----------------------------
1. Open Udemy in your browser and export your data using the small console snippet: run the code in [export_courses.js](export_courses.js) from the DevTools Console to save `udemy_courses.json` locally.
2. Install Python dependencies and set API keys for the LLM provider you wish to use (optional — classification can be skipped):

    - For Anthropic (default):

    ```bash
    pip install anthropic openpyxl
    export ANTHROPIC_API_KEY=...
    ```

    - Or for OpenAI:

    ```bash
    pip install openai openpyxl
    export OPENAI_API_KEY=...
    # optional: select a preferred OpenAI model
    export OPENAI_MODEL=gpt-5.6-luna
    ```

    You can also set the preferred provider via the `UD_PROVIDER` environment variable (`anthropic` or `openai`) or pass `--provider` on the command line.

3. Run the organizer against the exported JSON:

```bash
# default (Anthropic)
python udemy_organize.py udemy_courses.json

# explicitly use OpenAI
python udemy_organize.py udemy_courses.json --provider openai
```

The script will produce `udemy_courses.xlsx` and `udemy_courses.html`. If LLM classification is enabled it may also create `taxonomy.json` (editable categories) and `ai_cache.json` (to avoid re-classifying unchanged courses).

Key options and files
----------------------
- `--no-ai` — skip LLM classification and use Udemy's own course metadata.
- `--batch-size` — control classification batch size when using an LLM.
- `--model` — override the model used for classification; if using `--provider openai` and you supplied an Anthropic-style model name by accident, set `OPENAI_MODEL` to an appropriate OpenAI model.
- `taxonomy.json` — auto-generated on first run; edit this file to tune categories and re-run with `--reclassify`.
- `ai_cache.json` — stores per-course classification results to speed up subsequent runs.

Notes and troubleshooting
-------------------------
- Some LLM providers and models require specific account access (for example, Astra/GPT-6 variants). If a model is unavailable you will see an API error — try setting `OPENAI_MODEL` (for OpenAI) or use the `anthropic` provider.
- The script sanitizes text before writing to Excel to avoid `openpyxl` IllegalCharacterError when course titles or descriptions contain control characters.
- If you only want to merge multiple exports (for example, active + archived), combine them first and pass the combined file: `jq -s 'add' udemy_courses.json udemy_courses_archived.json > all.json`.

Files of interest
------------------
- [export_courses.js](export_courses.js) — small browser snippet to export your Udemy courses JSON.
- [udemy_organize.py](udemy_organize.py) — main script that parses the export, optionally classifies with an LLM, and writes outputs.
