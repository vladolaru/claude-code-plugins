# imgbench

Dev tooling for the image-optimizer plugin, not shipped behavior: it builds a benchmark corpus of real and deterministic synthetic images, runs `imgopt` over it, and reports what the ladder, floors and encoders actually do, so changes to them can be judged on evidence. It imports `imgopt_lib` from `../scripts/` and needs only Python 3 and Pillow.

The seven commands, in the order you use them:

- `select`: pick candidate images from the allowed sources (Wikimedia Commons, the Kodak set, WooCommerce, Gutenberg and Jetpack assets) and write them to `sources.json` with their license, author and checksum.
- `fetch`: download the originals listed in `sources.json`, verify their checksums and keep them in the shared downloads folder.
- `build`: assemble the corpus from the fetched originals, generate the synthetic files from fixed seeds and derive the variants, then write `corpus.json` and print a table of files and size per category. `build --only icon,edge` rebuilds just those categories and keeps the rest (naming any photo category rebuilds all six together).
- `run`: run `imgopt` over the corpus job by job (`recompress-high`, `recompress-medium`, `lossless`, `prepare-catalog`, `convert-webp`, `convert-jpeg`, `edge-high`) into a new `runs/<UTC date>-<commit>/` folder, one row per file in `rows.jsonl` and the tool versions in `timing.json`; candidate files other than picks are pruned after each category is measured, because the full ladder does not fit on a laptop disk. `--only-job a,b` and `--only-category x,y` narrow it for smoke runs and re-runs; `--jobs-parallel N` sets how many files run at once (default 2). The full run takes hours. Put `cjpegli` on `PATH` first: imgopt treats it as optional and skips its rungs when it is missing, so `run` refuses to start without it unless `--allow-missing cjpegli` says to measure the smaller ladder; `timing.json` and the report header name every optional tool a job ran without.
- `report <run folder>`: print and save `report.md`: per job and category, the savings, pick families, encoder ablations (the saving with jpegli, guetzli, jpegoptim, zopfli or pngquant removed), how many picks sit at a floor, Evidence SSIM against gate SSIM, banding of lossy picks, and every skip reason. Never one number across categories.
- `review <run folder>`: sample the lossy picks closest to a floor in each job and category (3 by default, plus one random pick) and build one 1:1 comparison page per job under `review/<run>/<job>/`; the human ticks the acceptable picks and pastes the page's command back. Never run that command: `apply` would write the picks over the corpus files.
- `review-record <review folder> --approve <list or pasted command>`: record the ticked picks as `verdicts.json` next to the page (every sampled file, `acceptable` true or false).

Everything it produces lives under the `imgopt workdir` cache root, in its `bench/` folder (`corpus-v<N>/`, `downloads/`, `runs/`, `review/`), never in the repository. Committed here: this code, `sources.json` and `templates/`.

License rule: real images come only from `CC0`, `Public domain`, `CC BY*` and `CC BY-SA*` sources on Wikimedia, the Kodak set, and GPL-2.0-or-later assets from WooCommerce, Gutenberg and Jetpack at pinned commits, and every file carries its license, author or origin, and checksum.
