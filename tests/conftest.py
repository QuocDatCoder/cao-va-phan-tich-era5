import os


# Tests use deterministic local providers and exercise API routes without credentials.
os.environ["AUTH_ENABLED"] = "false"
os.environ["CLIMATE_PROVIDER"] = "mock"
