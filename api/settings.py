"""Settings for the calls API. Defaults target docker-compose."""

import os
import tomllib
from pathlib import Path

SECRET_KEY = 'insecure-key-for-local-development-only'
DEBUG = True
ALLOWED_HOSTS = ['*']
ROOT_URLCONF = 'calls.urls'
INSTALLED_APPS = ['calls']
MIDDLEWARE = ['calls.middleware.SimulatedNetworkMiddleware']
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'ringr',
        'USER': 'postgres',
        'HOST': os.environ.get('DATABASE_HOST', 'localhost'),
        'PORT': os.environ.get('DATABASE_PORT', '55432'),
        'CONN_MAX_AGE': 60,
    }
}

# Network conditions from fakes.toml, in real time.
with (Path(__file__).resolve().parent.parent / 'fakes.toml').open('rb') as fakes_file:
    _fakes = tomllib.load(fakes_file)
SIMULATED_NETWORK = {
    'seed': _fakes['seed'],
    'latency_ms': _fakes['api_latency_ms'],
    'client_timeout_ms': _fakes['client_timeout_ms'],
    'lost_response_rate': _fakes['lost_response_rate'],
}
