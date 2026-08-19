class GNSSGoError(Exception):
    """Base class for user-facing GNSSGo errors."""


class ProviderError(GNSSGoError):
    """Raised when a data provider cannot resolve or access data."""


class ProviderProtocolError(ProviderError):
    """Raised when a provider API returns an unexpected schema."""


class AuthenticationError(ProviderError):
    """Raised when provider authentication fails or credentials are missing."""


class RemoteFileNotFound(ProviderError):
    """Raised when a requested remote file is unavailable."""


class DownloadError(GNSSGoError):
    """Raised when a download fails."""


class InvalidRemoteContent(DownloadError):
    """Raised when a remote response body is not the expected data file."""


class RemoteLoginPage(AuthenticationError):
    """Raised when a provider returns an authentication web page instead of data."""


class ValidationError(GNSSGoError):
    """Raised when a downloaded file fails validation."""


class PostProcessError(GNSSGoError):
    """Raised when decompression or RINEX restoration fails."""


class ConfigurationError(GNSSGoError):
    """Raised when configuration is invalid."""
