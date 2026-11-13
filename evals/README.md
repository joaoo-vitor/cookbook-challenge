# Transcription stress test

Scripts used to compare vision models on degraded copies of the recipe scan.
Everything they read and write lives under `data/experiments/`, which is gitignored.

1. `python evals/make_degraded.py` builds image-only PDFs from `Recipe.pdf`
   (low resolution, heavy JPEG compression, rotated, tiny) in `data/experiments/degraded/`.
2. Transcribe each copy with each model:
   `python -m cookbook.extract data/experiments/degraded/Recipe_tiny.pdf --pages 1,2 --model <model> --out data/experiments/stress`
3. `python evals/score.py` compares each transcript with a reference transcript
   (word similarity, key ingredient words, numbers, unreadable-word markers, new words).

The reference is a model transcript that was checked by eye against the clean pages,
so it is not a hand-made ground truth. Model names and variants are set at the top of `score.py`.
