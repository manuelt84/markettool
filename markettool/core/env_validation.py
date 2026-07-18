"""Environment validation for production readiness."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EnvVarConfig:
    """Configuration for an environment variable."""
    name: str
    required: bool = True
    default: Optional[str] = None
    description: str = ""
    
    def validate(self) -> tuple[bool, str]:
        """
        Validate this environment variable.
        
        Returns:
            (is_valid, message)
        """
        value = os.environ.get(self.name)
        
        if value is None:
            if self.required and self.default is None:
                return False, f"❌ Required environment variable '{self.name}' is missing"
            elif self.default is not None:
                os.environ[self.name] = self.default
                return True, f"⚠️  Using default value for '{self.name}': {self.default}"
            else:
                return True, f"ℹ️  Optional variable '{self.name}' not set"
        
        return True, f"✅ '{self.name}' = {value[:20]}... (set)"
    
    def get_value(self) -> Optional[str]:
        """Get the value of this environment variable."""
        return os.environ.get(self.name, self.default)


class EnvironmentValidator:
    """Validates required environment variables for production."""
    
    # Core service configuration
    CORE_VARS = [
        EnvVarConfig("PORT", required=False, default="8080", description="Server port"),
        EnvVarConfig("ENVIRONMENT", required=False, default="production", description="Environment name"),
        EnvVarConfig("WORKER_ID", required=False, default="A", description="Worker identifier"),
        EnvVarConfig("ENABLE_TELEGRAM_BOT", required=False, default="false", description="Enable Telegram bot initialization"),
    ]
    
    # Telegram configuration
    TELEGRAM_VARS = [
        EnvVarConfig("TELEGRAM_BOT_TOKEN", required=True, description="Telegram bot token"),
        EnvVarConfig("WEBHOOK_URL", required=False, description="Telegram webhook URL (for webhook mode)"),
    ]
    
    # Google Cloud Platform
    GCP_VARS = [
        EnvVarConfig("GOOGLE_APPLICATION_CREDENTIALS", required=True, description="GCP service account key path"),
        EnvVarConfig("FIRESTORE_PROJECT_ID", required=False, description="Firestore project ID"),
    ]
    
    # API Keys
    API_VARS = [
        EnvVarConfig("FMP_API_KEY", required=True, description="Financial Modeling Prep API key"),
    ]
    
    # Optional performance settings
    OPTIONAL_VARS = [
        EnvVarConfig("CACHE_WARMUP_CONCURRENCY", required=False, default="16", description="Cache warmup concurrency"),
        EnvVarConfig("ANALYSIS_PER_SYMBOL_CONCURRENCY", required=False, default="8", description="Analysis concurrency"),
        EnvVarConfig("INDICATORS_CACHE_ENABLED", required=False, default="true", description="Enable indicators cache"),
        EnvVarConfig("INDICATORS_CACHE_TTL_HOURS", required=False, default="4", description="Cache TTL hours"),
    ]
    
    def __init__(self) -> None:
        """Initialize environment validator."""
        telegram_enabled = str(os.environ.get("ENABLE_TELEGRAM_BOT", "false")).strip().lower() in {
            "1", "true", "yes", "y", "on"
        }

        self.all_vars = (
            self.CORE_VARS +
            (self.TELEGRAM_VARS if telegram_enabled else []) +
            self.GCP_VARS +
            self.API_VARS +
            self.OPTIONAL_VARS
        )
    
    def validate_all(self, fail_fast: bool = True) -> bool:
        """
        Validate all environment variables.
        
        Args:
            fail_fast: If True, exit on first critical error
        
        Returns:
            True if all required variables are valid
        """
        logger.info("=" * 80)
        logger.info("🔍 VALIDATING ENVIRONMENT VARIABLES")
        logger.info("=" * 80)
        
        failures: List[str] = []
        warnings: List[str] = []
        success: List[str] = []
        
        for var_config in self.all_vars:
            is_valid, message = var_config.validate()
            
            if not is_valid:
                failures.append(message)
                logger.error(message)
                if fail_fast:
                    logger.error("💥 CRITICAL: Application cannot start without required environment variables")
                    sys.exit(1)
            elif message.startswith("⚠️"):
                warnings.append(message)
                logger.warning(message)
            else:
                success.append(message)
                logger.info(message)
        
        # Summary
        logger.info("=" * 80)
        logger.info("📊 VALIDATION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"✅ Success: {len(success)}")
        logger.info(f"⚠️  Warnings: {len(warnings)}")
        logger.info(f"❌ Failures: {len(failures)}")
        logger.info("=" * 80)
        
        if failures:
            logger.error("💥 ENVIRONMENT VALIDATION FAILED")
            logger.error("Please set the following required environment variables:")
            for failure in failures:
                logger.error(f"  {failure}")
            return False
        
        logger.info("✅ ENVIRONMENT VALIDATION PASSED")
        return True
    
    def get_config_summary(self) -> dict:
        """Get a summary of current configuration."""
        return {
            var_config.name: {
                "value": var_config.get_value(),
                "required": var_config.required,
                "description": var_config.description,
            }
            for var_config in self.all_vars
        }


def validate_environment(fail_fast: bool = True) -> bool:
    """
    Validate environment variables for production readiness.
    
    This should be called early in the application startup.
    
    Args:
        fail_fast: If True, exit immediately on critical errors
    
    Returns:
        True if validation passed
    """
    validator = EnvironmentValidator()
    return validator.validate_all(fail_fast=fail_fast)


def validate_file_exists(filepath: str, description: str) -> bool:
    """
    Validate that a required file exists.
    
    Args:
        filepath: Path to the file
        description: Human-readable description
    
    Returns:
        True if file exists
    """
    if not os.path.exists(filepath):
        logger.error(f"❌ Required file missing: {description}")
        logger.error(f"   Path: {filepath}")
        return False
    
    logger.info(f"✅ File exists: {description} ({filepath})")
    return True


def validate_production_readiness() -> bool:
    """
    Comprehensive production readiness check.
    
    Validates:
    - Environment variables
    - Required files (models, credentials)
    - GCP credentials
    
    Returns:
        True if all checks pass
    """
    logger.info("🚀 STARTING PRODUCTION READINESS CHECKS")
    logger.info("=" * 80)
    
    checks_passed = []
    
    # 1. Environment variables
    env_valid = validate_environment(fail_fast=False)
    checks_passed.append(("Environment Variables", env_valid))
    
    # 2. Model files
    patrones_exists = validate_file_exists("/app/patrones.pt", "Patrones model")
    ruido_exists = validate_file_exists("/app/ruido.pt", "Ruido model")
    checks_passed.append(("Model Files", patrones_exists and ruido_exists))
    
    # 3. GCP credentials
    gcp_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if gcp_creds:
        gcp_valid = validate_file_exists(gcp_creds, "GCP Service Account")
        checks_passed.append(("GCP Credentials", gcp_valid))
    else:
        logger.warning("⚠️  GOOGLE_APPLICATION_CREDENTIALS not set, skipping validation")
        checks_passed.append(("GCP Credentials", True))
    
    # Summary
    logger.info("=" * 80)
    logger.info("📋 PRODUCTION READINESS SUMMARY")
    logger.info("=" * 80)
    
    all_passed = True
    for check_name, passed in checks_passed:
        status = "✅ PASSED" if passed else "❌ FAILED"
        logger.info(f"{check_name}: {status}")
        if not passed:
            all_passed = False
    
    logger.info("=" * 80)
    
    if all_passed:
        logger.info("🎉 ALL PRODUCTION READINESS CHECKS PASSED")
        return True
    else:
        logger.error("💥 PRODUCTION READINESS CHECKS FAILED")
        logger.error("Application may not function correctly. Please fix the issues above.")
        return False
