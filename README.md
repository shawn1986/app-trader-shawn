# trader-shawn

Bootstrap for Trader Shawn project settings and configuration.

## Development

- Python 3.12+
- Run tests with `py -3.12 -m pytest tests/unit/test_settings.py -v`

## Configuration

Sample configuration lives under `config/`.
Environment overrides supported by `trader_shawn.settings.load_settings`:

- `TRADER_SHAWN_MODE`
- `TRADER_SHAWN_LIVE_ENABLED`
- `TRADER_SHAWN_IBKR_HOST`
- `TRADER_SHAWN_IBKR_PORT`
- `TRADER_SHAWN_IBKR_CLIENT_ID`
