"""Names and limits shared by the Space and the scoring job. Secrets are referenced by environment variable name only."""
import os

ORG = "newmean-ai"
SPACE = os.environ.get("PII_LB_SPACE", f"{ORG}/pii-leaderboard")
REQUESTS = os.environ.get("PII_LB_REQUESTS", f"{ORG}/pii-leaderboard-requests")
RESULTS = os.environ.get("PII_LB_RESULTS", f"{ORG}/pii-leaderboard-results")
PUBLIC_TEST = os.environ.get("PII_LB_PUBLIC_TEST", f"{ORG}/pii-dataset")
PRIVATE_TEST = os.environ.get("PII_LB_PRIVATE_TEST", f"{ORG}/pii-multilingual-test-private")
TEST_FILE = "data/test.jsonl"   # same layout in both test datasets (dataset card release/hf/pii-dataset/README.md, Fields)

# Environment variable names (values live in Space / Job secrets, never in the repo)
TOKEN_ENV = "HF_TOKEN"          # Space: write on the requests dataset, read on results. Job: read both test sets, write both datasets.

# The 13 labels of the newmean v1 models; submissions map their labels to these, to OTHER (masked, no matching label) or ignore.
LABELS = ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "URL_L2",
          "DATE_OF_BIRTH", "SECRET"]
MAP_TARGETS = LABELS + ["OTHER", "ignore"]
# Gold labels of the test sets (PERSON_L2 / URL_L2 are expressed as level-2 subtypes; IP_ADDRESS is gold-only, see README)
GOLD_LABELS = ["PERSON", "ID_NUMBER", "PHONE", "FINANCIAL", "EMAIL", "ADDRESS", "ORG", "SECRET", "USERNAME", "DATE_OF_BIRTH",
               "URL", "IP_ADDRESS"]

MAX_PER_WEEK = int(os.environ.get("PII_LB_MAX_PER_WEEK", 2))          # submissions per account per rolling 7 days
MAX_QUEUE = int(os.environ.get("PII_LB_MAX_QUEUE", 30))               # pending requests accepted in total
MAX_MODEL_GB = float(os.environ.get("PII_LB_MAX_MODEL_GB", 3.0))      # weights on the Hub (safetensors) per submission
MAX_PER_RUN = int(os.environ.get("PII_LB_MAX_PER_RUN", 12))           # models scored by one job run (the rest wait a week)
RUN_BUDGET_MIN = float(os.environ.get("PII_LB_RUN_BUDGET_MIN", 150))  # stop starting new models after this many minutes

STATUSES = ("pending", "running", "done", "failed", "rejected")
