# Example candidates

Synthetic résumés so you can run the screener with **no ATS and no real data**.

```bash
# Zero keys at all — just see the report:
python run.py --demo

# Real scoring on these résumés (needs ANTHROPIC_API_KEY in .env):
python run.py --role staff-backend-engineer --source local --input-dir examples/sample-candidates
```

`candidates.csv` columns: `name,email,application_date,resume_file`. Drop your own
`.md` / `.txt` / `.pdf` résumés in a folder, point `--input-dir` at it, and go.
All names here are fictional.
