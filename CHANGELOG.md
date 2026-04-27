# Changelog

## [0.0.4] - 2026-04-27

### Added

- **ZIP file support** — both the CLI and GUI now accept `.zip` files as input. The first `.ged` or `.gedcom` entry found inside the archive (preferring top-level files over subdirectory entries) is extracted automatically and used for parsing.
- **Alternate name matching** — GEDCOM records can contain multiple `NAME` lines for the same individual (e.g., a birth name and a married name). All names are now collected and searched, so a query matching any of a person's recorded names will find them. Previously only the first `NAME` line was considered.
- **Fuzzy name search** — an optional fuzzy matching mode (CLI: `--fuzzy` / `--fuzzy-threshold`; GUI: "Fuzzy" checkbox) tolerates typos and spelling variants using `difflib.SequenceMatcher`. In the GUI the fuzzy filter also applies to the people list.

### Changed

- CLI `--help` and inline usage examples updated to document the new options.
- HELP.md and README.md updated to reflect ZIP support, fuzzy matching, and alternate name matching.
- GUI file browser now includes `*.zip` in the GEDCOM file filter.
- GUI status bar briefly shows the name of the `.ged` file extracted from a ZIP before loading completes.
