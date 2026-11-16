# The web page

A local page around the pipeline, so a person can upload a file, check what the models did, and change the recipe, without the command line. Start it with `python -m cookbook.web`.

## Why a server

The pipeline is Python and the API key must not reach a browser, so a small server (FastAPI) runs on this computer only (`127.0.0.1`) and the page (plain HTML, CSS and JavaScript, no build step) talks to it. There is no login: nothing is exposed beyond the machine.

## The server keeps no recipe

The page holds the transcript and the recipe and sends them back with every request. Each route is a plain call of a function the command line already uses:

| Route | Does |
|---|---|
| `POST /api/jobs` | saves the upload (type judged by its first bytes, 20 MB cap) and starts reading it in a thread |
| `GET /api/jobs/{id}` | the job's state; when done, the transcript, per-page agreement of the two readings, and the models used |
| `GET /api/jobs/{id}/page/{n}.jpg` | the original page, so text can be compared with it |
| `POST /api/structure` | the second model fills the recipe schema from the transcript |
| `POST /api/check` | checks an edited recipe against the transcript again (plain code, no model) |
| `POST /api/render` | the recipe, the open issues and the change log as Markdown for a cook (no model) |
| `POST /api/manipulate` | makes the recipe vegan (the primary model writes the new words, code checks them) and/or changes the servings |

Only reading is a job, because two models per page take seconds. Jobs and uploaded files live in memory and in a temp folder; the last five are kept and everything is removed when the server stops.

## The three steps

1. **Check the text.** The original page beside the transcript. Where the two readings differ, the word the first model read is underlined (red for numbers, amber for words). Clicking it shows both readings: keep the first, use the second, or type your own. A doubt whose word cannot be found in the line marks the whole line. A page whose readings mostly disagree is marked *unreliable*, with a button to say you checked the whole page. Settled doubts are removed from the transcript sent on, so they do not come back as warnings in the recipe; unsettled ones stay, and the recipe step shows them.
2. **Check the recipe.** Every ingredient, tool and step is editable and shows the quote and lines it came from. Items with issues are coloured by the worst severity and list the messages; a side panel lists all issues. *Re-check* runs the same checks on your edits, so a wrong amount is caught again. Servings that are only the model's estimate are labelled as such.
3. **Change the recipe.** Choose to make it vegan, to change the servings (and, if you know it, what the recipe makes now), or both. The order of manipulations is shown but fixed: rules run in one order whatever you ask, and a substitution keeps its ingredient's scaling class, so the result is the same either way. The result shows original and new side by side, then a log of what changed, what was left alone and what to check by hand. Warnings from the vegan rule carry a severity (high: a step or ingredient the program could not make vegan and left as written), and the changed ingredients and steps are highlighted. Vegan is the one option that calls a model, so it takes a few seconds. See [manipulate.md](manipulate.md).

## Checking warnings off

In both steps a warning goes away when the person says they checked it, and *Reset warnings* brings them all back so they can be checked again.
- **Text step:** a line with a doubt has a **✓ Check** button (editing or confirming the line, or choosing a reading, does the same). *Reset warnings* restores every doubt; the text the person edited stays as edited.
- **Recipe step:** each issue, in the side panel and under its item, has a **✓ Check** button. Checked issues are hidden and counted ("3 checked"). They are remembered by code, item and message, so *Re-check* does not bring back one that is still the same. Building the recipe again from the text starts clean.

Checking a warning only hides it on the page: the recipe and its issues are unchanged, and nothing is sent to the server.

## Preview and download

**Preview recipe** (steps 2 and 3, and after applying) opens a pop-up with the recipe as a cook would read it, with **Download .md**. In step 3 it shows the changed recipe once the changes are applied, and the edited recipe before that. The page sends the recipe, the issues you did not check off, and the change log to `POST /api/render`, which uses the same renderer as `python -m cookbook.output` and needs no model. The Markdown only uses headings, lists, bold and italic, so a few lines of JavaScript show it, and everything is put on the page as text, never as HTML. See [output.md](output.md).

## Safety and limits

Text from a model is only ever put on the page as text, never as HTML. Uploads are saved under a generated name, never the one the browser sent. A file that is not a PDF, an image or text is refused, as is one over 20 MB. The page opens without an API key; the error appears when a model is first needed. Reading shows stages, not per-page progress, because `transcribe_pdf` returns when all pages are done.

## Tests

`tests/test_web.py` runs the app in-process with fake readers: no network, no key. It covers upload, the job, both readings of a doubtful word, the page image, refused files, structuring with edited lines, re-checking, scaling, and the vegan rule (with a fake model, including an unreadable answer and a missing key).
