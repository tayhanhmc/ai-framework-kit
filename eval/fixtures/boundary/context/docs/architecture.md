# Package layering

- `app/domain` must not import `app/storage`, directly or indirectly. The nightly batch
  pricer loads `app/domain` on workers with no database access; a storage import there
  breaks it, and no test covers the batch pricer.
- `app/api` composes the layers: it reads from `app/storage` and passes plain values
  (for example a discount percentage) into `app/domain` functions.
