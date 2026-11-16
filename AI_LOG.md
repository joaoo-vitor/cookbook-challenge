# AI log: how the code was written, and where the agent was wrong

The code was written with Claude Code (the desktop app) over four days (13 to 16 November 2026). This is a record of how it was steered and, more useful, of where the agent was wrong and how that was found. Commit hashes point at the fixes. Where a mistake had no commit of its own, it says where it was caught. The models *inside* the app have their own list of failures in [extract.md](extract.md); this file is about the coding agent.

## How I worked with it

- **I started from nothing and asked for insights, not answers.** The first thing the agent produced was a full architecture. I asked for it to be removed and for hints for each part instead, because I wanted to understand each choice and make it myself.
- **A plan before each stage, and my decision on every real choice.** The agent explored the code, wrote a plan, and asked me the open questions in plain terms (which provider, which rule for gray areas, what a failed rewrite should do, what language the cook's labels use). I answered; it did not choose for me. Examples of mine: Gemini's free tier, two *different* models for the reading, Portuguese support, keep the original step text with a warning when a vegan rewrite fails, wine and sugar left alone, a fixed order for the rules, the warnings the cook sees.
- **"Why" before "do".** I asked the agent to explain what a magic byte is, what an OCR layer is, what a fixture is, why 200 dpi, how word counting differs from line matching, before I let it write the code that used them.
- **Small commits, and I push.** Each step is a commit with a message that says what and why; the agent only pushes when I say "push". The history is the real history, including the fixes.
- **I ran the pipeline on documents the agent had not tuned on.** The agent wrote and tuned against the Sauerbraten; I fed it other recipes (scanned English magazine pages in columns, a Portuguese strogonoff, a mixed PDF). Most of the bugs below came from those runs, not from the agent noticing.
- **I challenged claims that sounded too sure** ("is this right? the assignment asks about the other order", "it got it right, but that was luck, right?", "isn't 'PDF only' wrong?").

## Where the agent was wrong

| What happened | How it was found | Fix |
|---|---|---|
| **Rendered every page at a fixed 200 dpi.** The phone-made PDF stretched into a 41 MB PNG, 15 to 75 seconds per call. | I compared with Gemini's website, which took seconds, and asked why the API was slow. The agent measured. | Render at the photo's resolution, as a JPEG: 1 MB, 3 to 6 s (`36623a0`) |
| **One strategy per page.** The first design could not say "typed page with a handwritten image on it". | I asked whether a page can have both text and an image. Then I pushed back that a typed page should have an empty task list. | A list of extra tasks, only for images beyond the page's strategy |
| **Trusted the model's `[?word?]` marker as the confidence signal** in the plan. | The stress test on degraded and illegible copies: on readable pages no model ever wrote one, on illegible ones one model wrote 25 invented lines with no marker. I asked whether to worry. | Two different models read the page; their disagreement is the signal (`df23885`) |
| **A word count to compare the two readings.** Columns read in a different order looked like 22 high issues. | My test of a two-column recipe. I asked whether line matching would be better than counting words. | Compare by blocks of intact words, so a moved block is not a disagreement (`5499872`) |
| **The column rule in the prompt was recorded as working.** It had worked on one image. | Running the other scanned recipes: on a pastry recipe the first model read across the columns again. | The comparison no longer depends on the prompt (the block comparison above) |
| **The model cited the wrong lines** for a quote that runs over two lines, and the agent first treated a correct citation in one run as fixed. | I asked whether the correct run was luck. It was: the same input had given wrong ids before. | Citations are repaired in code, and the correction is reported (`5cc48f7`) |
| **Checks that raised false alarms.** Numbers of a numbered list as "dropped" (`1ed2a77`); a quote with a hyphenated line break as invented, and "9-ounce can" as an unknown unit (`3dec2ab`); an empty model answer scored as 0.00 agreement and an unreliable page (`2040d53`); `¾` against `3/4` (`f7d6471`); a quote citing one line of two (`5cc48f7`); a Portuguese amount in words called wrong (`df25918`); a line id the model copied into a quote (`fded746`); "Anno 1887" as a medium warning (`5531bbc`); the program's own `[from image]` marker (`8d7adf6`). | Running real documents, and reading the issue list against the page | One fix and one test each |
| **Normalised text with NFKD,** which turns `½` into `1⁄2`, and **read "um pouco" as the number 1** (a vague phrase that starts with a number word). | The tests written for Portuguese; the second bug was present in German and English too | NFD only; a vague phrase wins over its own number word |
| **A check that was blind to a lost step.** Counting numbers in the model's quote hid two steps with different times ("nach dem 1. Tag", "nach 8 Tagen") merged into one without them. | Reading the structured recipe against the transcript | Count only the structured fields; prompt rule not to merge steps with different conditions |
| **Tests that proved little.** An image drawn smaller than its rectangle, missing imports, weak assertions. | Failing tests, and reviewing what a test actually asserted | Fixed; later tests plant a fault and assert that exactly that check rejects it |
| **"Vegan and scaling give the same recipe in either order"**, said as a fact. It was true only because the engine always runs one order. | I read the assignment's question and asked. | Corrected in the docs: equal by construction, not shown by a test |
| **The model replaced "Bratenfond" with "Bratensud",** and the retry message ("still mentions Braten") was too vague for it to fix. | The first real vegan run on the Sauerbraten | The prompt and the message say a new word must not contain the old name (`77e1b16`) |
| **The Close button of the preview was unreadable in dark mode.** The agent had only looked at the light theme. | I looked at it | Distinct button border, explicit colours, `color-scheme` (`adbc308`) |
| **Stale documentation.** The README said ingest was "PDF only" long after images and text were accepted; the old guides were plans, not descriptions. | I read the README | Fixed (`f39bd2a`); both plans rewritten as what the system does |
| **A scripted edit put a CLI line into the wrong commit,** so the history of one commit is slightly off. | The agent noticed after committing | Left as it is rather than rewriting history |

## What the agent could not check

- **The Claude reader** (`--provider claude`) is built and tested only against a stub of the SDK. I had no key with credits, so it has never run on a real page. It is the code most at risk of an invented API.
- **Docker.** The `Dockerfile` and `docker-compose.yml` were written on a machine without Docker. What was checked: the project installs as a non-editable package in a clean environment, the page and its files are served from it, and an upload is accepted from another folder. The image build and `docker compose up` themselves were not run.
- **The free-tier limits.** The README says a run is far from them. That is my own check of the key in Google AI Studio; the agent could not see it. The agent first wrote a per-token price into the cost section, from third-party listings it had not checked, as if it applied to a free tier with no price, and I corrected it.
- **The numbers** from the experiments come from two pages of one recipe, five scanned English pages and a few degraded copies. They are a signal, not an evaluation: there is no hand-made reference yet.

## What worked

- **Planting faults in tests.** Each check was shown to reject its own deliberate fault (a dropped quantity, a changed number, an invented ingredient, a rewrite that keeps "Braten"), which also caught some of the agent's own bugs.
- **Measuring before choosing.** The stress tests on degraded scans decided the two-reader design and showed the marker was useless; the run totals gave the cost and time numbers.
- **Never trusting the model, and not the agent either.** The same stance as in the app: a value needs evidence, a claim needs a check, and a fix needs a test.
- **A short loop with real documents.** The best bug reports came from my running a recipe the agent had not seen.