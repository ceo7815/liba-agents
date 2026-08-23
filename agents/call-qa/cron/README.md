Not enabled.

Daily Drive pull will be a Hermes cron job on the **call-qa profile**, after manual runs look right.

Do not start a Windows Task Scheduler / extra Python daemon. Use `call-qa cron` (Hermes) when we get there.

Intended later (do not register yet):

- once a day: list new files from Drive shared folder → fetch → `run_single_call.py` per file → mock/OS report
