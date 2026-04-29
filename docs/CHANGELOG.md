# Changelog

## [0.1.0] - 2026-04-29

### Added

- **Preferences dialog** — a new "Preferences…" entry at the top of the Menu opens a Preferences window with application settings. The first setting is **Font size**, with three choices: Small (9 pt), Medium (10 pt), and Large (13 pt UI / 12 pt monospace). Clicking a radio button applies the change immediately as a live preview; Cancel reverts to the previous size and OK saves the choice. The preference is persisted to `settings.json` and restored on next launch.
- **Universal font scaling** — changing the font size in Preferences updates every part of the application simultaneously: the people list, results pane, all open Show Person windows, the tag definitions viewer, and all other popup windows. The Treeview row height adjusts automatically to match the new font metrics.
- **Show Person window remembers position and size** — when the Show Person window is moved or resized, its geometry is saved to `settings.json`. The next time a Show Person window is opened it appears at the same location and with the same dimensions as when it was last closed. The position is saved with a short debounce so ordinary repositioning does not cause excessive disk writes.

### Improved

- **Windows resize automatically on font change** — after a font size change, all open windows (main window and any Toplevel popups) are measured against their minimum required size and expanded if the current geometry is too small to display all controls. The main window's minimum resizable width is also updated to reflect the new font. Windows never shrink automatically, preserving the user's chosen layout when switching to a smaller font.

## [0.0.9] - 2026-04-29

### Added

- **Family section in results pane** — whenever DNA match results are displayed, a new "Family" section now appears immediately before "Path to Home Person", listing the selected person's parents, siblings, and children with name, lifespan, and GEDCOM ID.
- **Family section in Show Person window** — the "Show Person" popup now opens with the same family summary (parents, siblings, children) at the top, above the raw GEDCOM record, under a "── GEDCOM Record ──" divider.
- **Sortable column headings** — clicking any column heading in the people list (Name, Years, DNA?, ID) sorts the list by that field. Clicking the same heading again reverses the order. The active sort column is indicated by ▲ (ascending) or ▼ (descending). Years sorts by birth year with unknown dates last; DNA? groups flagged rows together.
- **Clickable name links** — every person name displayed in the results pane and the Show Person window is rendered as a blue underlined hyperlink. Clicking a name in the results pane selects that person in the list (clearing search filters if needed) and runs "Find Nearest DNA Matches" for them. Clicking a name in the Show Person window navigates to that person within the same window rather than opening a new one. The cursor changes to a hand pointer on hover.

## [0.0.8] - 2026-04-28

### Added

- **Filter field** — a new "Filter:" text entry below the "Find:" box narrows the people list by searching the complete raw GEDCOM record for each person, not just the name. Type any text that appears in a GEDCOM entry — a location, source, event type, or any other field — to restrict results to only those individuals whose records contain that text. `Ctrl+I` jumps to the Filter box and selects all text.
- **Enter key moves focus to list** — pressing Enter in either the Find or Filter box immediately moves keyboard focus to the people list, so you can navigate straight to a result without reaching for the mouse.
- **Escape clears results** — the Escape key now triggers the same "clear results" action as `Ctrl+L`, providing a quick way to reset the results pane from anywhere in the window.

### Improved

- **Tab order** — Tab now follows a logical left-to-right, top-to-bottom sequence through the main controls: Find → Filter → people list → results pane → Top N → Max Depth → Set Home → Show Person → Find Nearest DNA Matches. Shift+Tab traverses the same chain in reverse. The vertical scrollbar on the people list is excluded from tab traversal.
- **Focus on list entry** — when focus moves to the people list (via Enter from a search box, `Ctrl+L`, or Tab), the first row is automatically selected if no row is already focused, so arrow-key navigation works immediately without an extra keypress.
- **Clear results behavior** — `Ctrl+L` (and Escape) now also clears the Find box and resets the last result state, then returns focus to the Find box for a clean start.

## [0.0.7] - 2026-04-28

### Added

- **Keyboard shortcuts** — twelve Ctrl-key shortcuts are now active throughout the application: Ctrl+F (jump to Search), Ctrl+D (toggle DNA-flagged filter), Ctrl+U (toggle Fuzzy search), Ctrl+O (Browse file), Ctrl+N (Find Nearest DNA Matches), Ctrl+S (Show Person), Ctrl+H (Set Home), Ctrl+P (Find Relationship Path), Ctrl+T (View tag definitions), Ctrl+C (Copy results), Ctrl+L (Clear results). Ctrl+C defers to the text widget's own copy behavior when the results pane has keyboard focus.
- **Button mnemonics** — the shortcut letter is underlined on seven buttons: Find (F), Copy (C), Clear (l), Show Person (S), Set Home (H), Find Nearest DNA Matches (N), and View tag definitions… (t).
- **Keyboard shortcuts help page** — a new "Keyboard shortcuts" entry in the Menu opens a formatted reference listing all shortcuts (`docs/KEYBOARD_SHORTCUTS.md`).
- **GEDCOM parse cache** — parsed GEDCOM data is now cached on disk as a binary pickle file (stored in the application's config directory under `cache/`). On subsequent opens the cache is loaded instead of re-parsing the file, making large GEDCOM files open almost instantly. The cache is invalidated automatically when the source file's modification time changes or when the Tag keyword or Page marker settings differ from the values used to build the cache.

## [0.0.6] - 2026-04-28

### Added

- **Compact ancestor/descendant labels** — `describe_relationship` now uses ordinal-prefixed "Nth-great" notation for deep ancestors and descendants. Ancestors four or more generations up are labelled "2nd-great-grandfather", "3rd-great-grandfather", etc. instead of "great-great-grandfather", "great-great-great-grandfather", and so on. The same convention applies to grandchildren and to great-aunts/uncles.
- **Smarter relationship descriptions for indirect paths** — when "Find Relationship Path" returns alternate routes that navigate through a spouse node to reach a niece, cousin, or similar relative, the function now recognizes the relationship correctly instead of falling back to a possessive chain like "brother's wife's daughter". Interior spouse edges (representing navigation within a family unit) are stripped before classification. A trailing sibling edge at the end of a descent path is also handled: the sibling of an Nth cousin once removed is still an Nth cousin once removed.
- **Auto-reopen last file on startup** — the application now automatically reopens the most recently loaded GEDCOM file when launched, provided the file still exists at its previous path.
- **Home person** — a new "Set Home" button in the action bar designates the selected person as the *home person* for the currently loaded GEDCOM file. The choice is persisted in the settings file and restored automatically when the same file is reopened. Whenever DNA match results are displayed, a "Path to Home Person" section is appended showing the relationship label and edge-by-edge path from the selected person to the home person.
- **Bold match headers** — the name-and-distance header line for each DNA match result (e.g. `#1: John Smith … (distance: 3 edges)`) is now rendered in bold, making it easier to scan multiple results at a glance.
- **Auto-sized initial window** — after building the UI, the application measures the minimum width Tk requires to display all controls and widens the window to that size if the default `1100 px` would clip any button. The minimum resizable width is updated to match.

### Fixed

- **Home person lost across sessions** — `_save_history` previously wrote `{"recent_files": […]}` as the entire settings file, silently erasing the `home_persons` map every time a file was opened. It now merges the updated list into the existing settings rather than replacing the file.

## [0.0.5] - 2026-04-27

### Added

- **Show Person window** — a new "Show Person" button (to the left of "Find Nearest DNA Matches") opens a popup displaying the complete raw GEDCOM record for the selected individual, with all fields and sub-records shown in standard GEDCOM line format.
- **Multi-path relationship finder** — the "Find Relationship Path" feature now pre-computes the biological ancestor and descendant sets for the starting person before labelling each discovered path.

### Fixed

- **Spurious "step-" labels on alternate paths** — when "Find Relationship Path" returned multiple routes to the same person, paths that reached a biological ancestor or descendant via an intermediate spouse edge (e.g. `me → mother → grandmother → grandfather`) were incorrectly labelled "step-grandfather" instead of "grandfather". The relationship labeller now checks whether the target is a known biological ancestor or descendant and uses the direct term regardless of which route the path took.

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
