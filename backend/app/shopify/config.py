from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        extra='ignore',
    )

    app_name: str = 'Mia AI'
    environment: str = 'development'
    debug: bool = False

    # Load from .env in production. Do not hardcode production secrets.
    secret_key: str = ''

    # SQLite is intended only for local development. Production must use
    # PostgreSQL so application data survives container replacement and can
    # be backed up and migrated safely.
    database_url: str = 'sqlite+aiosqlite:///./dev.db'

    # Shopify
    shopify_api_key: str = ''
    shopify_api_secret: str = ''
    shopify_app_url: str = 'http://localhost:8000'
    shopify_scopes: str = (
        'read_products,'
        'write_products,'
        'read_orders,'
        'read_customers,'
        'read_inventory'
    )

    shopify_api_version: str = '2026-07'

    # Token encryption key is loaded from .env.
    token_encryption_key: str = ''

    # CORS
    cors_origins: str = (
        'http://localhost:5173,http://localhost:8000'
    )

    # JWT
    jwt_algorithm: str = 'HS256'
    access_token_expire_minutes: int = 60

    # Session cookie
    session_cookie_name: str = 'mia_session'
    session_cookie_domain: str = ''
    session_cookie_path: str = '/'
    session_cookie_max_age: int = 60 * 60 * 24 * 30
    session_cookie_httponly: bool = True
    session_cookie_secure: bool = False
    session_cookie_samesite: str = 'lax'

    @property
    def is_production(self) -> bool:
        return (self.environment or '').strip().lower() == 'production'

    @property
    def effective_cors_origins(self) -> list[str]:
        origins = [
            origin.strip()
            for origin in self.cors_origins.split(',')
            if origin.strip()
        ]
        return origins or ['http://localhost:5173', 'http://localhost:8000']

    def validate_production(self) -> list[str]:
        """Return list of blocking issues for production deployment."""
        issues: list[str] = []
        if self.environment == 'production':
            if not self.secret_key or self.secret_key == 'CHANGE_THIS_TO_A_LONG_RANDOM_VALUE':
                issues.append('SECRET_KEY not configured for production')
            if not self.shopify_api_key:
                issues.append('SHOPIFY_API_KEY missing')
            if not self.shopify_api_secret:
                issues.append('SHOPIFY_API_SECRET missing')
            if not self.token_encryption_key:
                issues.append('TOKEN_ENCRYPTION_KEY missing')
            if not self.shopify_app_url or self.shopify_app_url.startswith('http://localhost'):
                issues.append('SHOPIFY_APP_URL not set to production domain')
            if self.session_cookie_secure is False:
                issues.append('SESSION_COOKIE_SECURE must be True in production')
            if 'localhost' in self.cors_origins:
                issues.append('localhost in CORS_ORIGINS is not safe for production')
            if self.database_url.startswith('sqlite'):
                issues.append('DATABASE_URL must use PostgreSQL in production')
        return issues


settings = Settings()

if settings.is_production:
    _issues = settings.validate_production()
    if _issues:
        raise RuntimeError(
            'Production configuration missing: ' + '; '.join(_issues)
        )
