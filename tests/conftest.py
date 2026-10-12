import os
import sys

# Modules under src/ import each other by bare name and read config/ relative to the
# working directory, so tests are expected to run from the repository root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Importing client/chat_completion builds real SDK objects, which need non-empty keys.
os.environ.setdefault('TELEGRAM_API_KEY', '123456:test-token')
os.environ.setdefault('OPENROUTER_API_KEY', 'test-key')
