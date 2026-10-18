class ClimateProviderError(Exception):
    """Base exception for climate provider failures safe to expose generically."""


class ClimateConfigurationError(ClimateProviderError):
    """Raised when provider configuration is missing or invalid."""


class UnsupportedVariableError(ClimateProviderError):
    """Raised when a provider cannot serve the requested climate variable."""


class ClimateDataError(ClimateProviderError):
    """Raised when provider data is missing, malformed, or empty."""
