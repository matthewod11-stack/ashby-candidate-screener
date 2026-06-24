# Ashby Candidate Pipeline — Weekly Slack Report

Run the Ashby candidate ranking pipeline and post results to Slack.

## Steps

1. Change to the pipeline directory:
   ```
   cd /path/to/ashby-candidate-screener
   ```

2. Activate the virtual environment and run the pipeline:
   ```
   source .venv/bin/activate && python run.py
   ```

3. Read the Slack summary file:
   ```
   cat data/results/slack_summary.json
   ```

4. Post the `message` field from the summary to Slack channel **#ashby-screener** (or the configured channel).

5. Attach the HTML report file (path is in `report_path` field) to the same Slack message.

6. If the pipeline failed or no slack_summary.json exists, post a message:
   "Ashby pipeline failed to run. Check logs."

## Notes

- This runs every Sunday night / Monday morning
- The pipeline handles its own caching — only new candidates get scored
- If no new candidates qualified, the message will say so (still post it)
- The HTML report is a self-contained file the recipient downloads and opens in a browser
