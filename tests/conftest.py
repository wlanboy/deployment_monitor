import sys
import os

# Ensure the project root is on the path so test modules can import api, db, etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# api.py sets base_path only inside the try-block that loads config.yaml.
# If config.yaml is absent the except-branch runs and base_path stays undefined,
# which would cause a NameError in resolve_path.  Set a safe default here.
import api  # noqa: E402 – must come after sys.path setup

if not hasattr(api, "base_path"):
    api.base_path = ""
