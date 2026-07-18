# Speciedex Provider Modules

Every provider listed in `static/tools/providers.json` has a matching module in
`static/tools/providers/`.

The main wrapper dynamically imports:

```text
providers.<provider-name>.Provider
```

The provider module must export a `Provider` class derived from
`providers.common.BaseProvider`.

The currently direct-API modules are:

- `gbif.py`
- `itis.py`
- `worms.py`
- `wikispecies.py`
- `inaturalist.py`

Every other registered provider has its own module and currently derives from
`FileJSONLProvider`. These providers ingest normalized, licensed JSONL exports
until their current API contract or bulk-release integration is implemented.

Run:

```bash
python static/tools/stat-grabber.py providers
python static/tools/stat-grabber.py scan
python static/tools/stat-grabber.py scan --all-providers
python static/tools/stat-grabber.py verify
```
