"""Exchange error hierarchy."""
class AthenaError(Exception):
    pass


class ConfigError(AthenaError):
    pass


class ExchangeError(AthenaError):
    """Base for exchange-side problems."""


class RateLimitError(ExchangeError):
    pass


class AuthError(ExchangeError):
    pass


class IpNotWhitelistedError(AuthError):
    """The Delta India API requires the caller IP to be whitelisted for the API key."""


class OrderRejected(ExchangeError):
    def __init__(self, message, code=None, context=None):
        super().__init__(message)
        self.code = code
        self.context = context


class OrderTimeout(ExchangeError):
    pass


class NetworkError(ExchangeError):
    pass


class DataError(AthenaError):
    pass

