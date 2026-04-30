"""
hatch_build.py — custom Hatchling build hook for gedcom-dna-finder.

Runs automatically during `python -m build dev/ --outdir dist/`.

What it does
------------
1. Copies every *.py file from src/ into gedcom_dna_finder/_scripts/ so the
   GUI and CLI entry-point shims can find them at runtime after pip install.
2. Copies docs/ and icons/ into the package so _resource_path() in the GUI
   resolves correctly (it looks two directories above __file__, which is
   gedcom_dna_finder/_scripts/, landing on gedcom_dna_finder/).
3. The static [tool.hatch.build.targets.wheel.force-include] entry in
   pyproject.toml then picks up the entire gedcom_dna_finder/ tree.
4. Cleans up the temporary copies after the wheel is written so the working
   tree stays tidy.

Note: this file lives in dev/ alongside pyproject.toml.  self.root is
therefore the absolute path to dev/, and the repo root is self.root's parent.
"""

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    # Top-level repo directories that are copied into the package.
    _ASSET_DIRS = ("docs", "icons")

    def initialize(self, version, build_data):
        repo = Path(self.root).parent          # repo root (one level above dev/)
        pkg = repo / "gedcom_dna_finder"

        # --- scripts -------------------------------------------------------
        scripts_dst = pkg / "_scripts"
        if scripts_dst.exists():
            shutil.rmtree(scripts_dst)
        scripts_dst.mkdir()
        for py in (repo / "src").glob("*.py"):
            shutil.copy2(py, scripts_dst / py.name)

        # --- assets (docs, icons) ------------------------------------------
        for name in self._ASSET_DIRS:
            src_dir = repo / name
            dst_dir = pkg / name
            if dst_dir.exists():
                shutil.rmtree(dst_dir)
            if src_dir.exists():
                shutil.copytree(src_dir, dst_dir)

        # No build_data["artifacts"] manipulation needed: the static
        # force-include in pyproject.toml covers the entire gedcom_dna_finder/
        # tree and is evaluated after this hook runs.

    def finalize(self, version, build_data, artifact_path):
        pkg = Path(self.root).parent / "gedcom_dna_finder"
        for name in ("_scripts", *self._ASSET_DIRS):
            target = pkg / name
            if target.exists():
                shutil.rmtree(target)
