"""Domain-level exceptions."""

from __future__ import annotations


class MarketToolError(Exception):
    """Base exception for MarketTool."""
    pass


# Data/Repository errors
class DataNotFoundError(MarketToolError):
    """Data requested was not found."""
    pass


class DataValidationError(MarketToolError):
    """Data validation failed."""
    pass


class CacheError(MarketToolError):
    """Cache operation failed."""
    pass


class StorageError(MarketToolError):
    """Storage operation failed."""
    pass


# API/External errors
class ExternalAPIError(MarketToolError):
    """External API call failed."""
    pass


class RateLimitError(ExternalAPIError):
    """Rate limit exceeded."""
    pass


class APITimeoutError(ExternalAPIError):
    """API request timed out."""
    pass


class PlanNotAllowed(ExternalAPIError):
    """API plan does not allow this operation."""
    pass


# Analysis/Signal errors
class AnalysisError(MarketToolError):
    """Analysis operation failed."""
    pass


class InsufficientDataError(AnalysisError):
    """Not enough data for analysis."""
    pass


# Configuration errors
class ConfigError(MarketToolError):
    """Configuration is invalid or missing."""
    pass


# Notification errors
class NotificationError(MarketToolError):
    """Notification failed."""
    pass


# Use case errors
class UseCaseError(MarketToolError):
    """Use case execution failed."""
    pass


class ValidationError(UseCaseError):
    """Input validation failed."""
    pass


class BusinessLogicError(UseCaseError):
    """Business rule violation."""
    pass
