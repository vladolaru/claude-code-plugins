# imgbench

Dev tooling for the image-optimizer plugin, not shipped behavior: it builds a benchmark corpus of real and deterministic synthetic images, runs `imgopt` over it, and reports what the ladder, floors and encoders actually do, so changes to them can be judged on evidence. It imports `imgopt_lib` from `../scripts/` and needs only Python 3 and Pillow.

The six commands, in the order you use them:

- `select`: pick candidate images from the allowed sources (Wikimedia Commons, the Kodak set, WooCommerce, Gutenberg and Jetpack assets) and write them to `sources.json` with their license, author and checksum.
- `fetch`: download the originals listed in `sources.json`, verify their checksums and keep them in the shared downloads folder.
- `build`: assemble the corpus from the fetched originals, generate the synthetic files from fixed seeds and derive the variants, then write `corpus.json` and print a table of files and size per category. `build --only icon,edge` rebuilds just those categories and keeps the rest (naming any photo category rebuilds all six together).
- `run`: run `imgopt` over the corpus and record every measurement under a new run folder.
- `report`: summarize a run into tables of savings, quality scores and gate outcomes per category.
- `review`: build the side-by-side review sheets of a run for a human to judge, and record the verdicts.

Everything it produces lives under the `imgopt workdir` cache root, in its `bench/` folder (`corpus-v<N>/`, `downloads/`, `runs/`), never in the repository. Committed here: this code, `sources.json` and `templates/`.

License rule: real images come only from `CC0`, `Public domain`, `CC BY*` and `CC BY-SA*` sources on Wikimedia, the Kodak set, and GPL-2.0-or-later assets from WooCommerce, Gutenberg and Jetpack at pinned commits, and every file carries its license, author or origin, and checksum.
