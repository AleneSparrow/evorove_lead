# Labelled evaluation data

No manually labelled real-world dataset was provided. This directory intentionally
does not contain a fabricated 300-case dataset. Use `evorove_lead.evaluation --schema`
to obtain the strict JSONL case schema.

Required: 300 cases across six SMB profiles, B2B and B2C, 180 development and 120
holdout. Human labels must include source validity and material-claim support.
Company and document identities must not overlap splits. Record source snapshots,
retrieval timestamps and hashes, label author, frozen clock and baseline version.

`tests/intelligence_helpers.py` supplies explicitly synthetic scripted fixtures for
software tests only. They are not evidence of live search quality, measured lead
precision, or real LLM expenditure.
