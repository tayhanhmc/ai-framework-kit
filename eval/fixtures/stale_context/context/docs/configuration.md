# Configuration

- Settings live in `repo/settings.ini` under `[server]` and are parsed with
  `configparser` in `app/config.py`.
- To add a setting, add its default to `DEFAULTS` and its key to `settings.ini`.
