Hermes cron lives on the **profile** (`%HERMES_HOME%/profiles/<name>/cron/jobs.json`), not as a second scheduler.

This folder is the intended job catalog for the agent. Keep `jobs: []` until a pipeline is proven by hand. Then register jobs with `hermes cron` on that profile — do not write a custom timer.
