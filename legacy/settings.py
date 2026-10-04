# Intended central tuning file. In practice nothing imports it: every module
# keeps its own copy of these constants, and this file has drifted.
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
CACHE_TTL = 300
BATCH_SIZE = 100
FLUSH_INTERVAL = 60
ALERT_THRESHOLD = 0.90  # drifted: live copies use 0.95
