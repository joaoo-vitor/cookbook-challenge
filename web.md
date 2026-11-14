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
| `POST /api/manipulate` | changes the servings; the vegan rule answers "not available yet" |

Only reading is a job, because two models per page take seconds. Jobs and uploaded files live in memory and in a temp folder; the last five are kept and everything is removed when the server stops.

## The three steps

1. **Check the text.** The original page beside the transcript. Where the two readings differ, the word the first model read is underlined (red for numbers, amber for words). Clicking it shows both readings: keep the first, use the second, or type your own. A doubt whose word cannot be found in the line marks the whole line. A page whose readings mostly disagree is marked *unreliable*, with a button to say you checked the whole page. Settled doubts are removed from the transcript sent on, so they do not come back as warnings in the recipe; unsettled ones stay, and the recipe step shows them.
2. **Check the recipe.** Every ingredient, tool and step is editable and shows the quote and lines it came from. Items with issues are coloured by the worst severity and list the messages; a side panel lists all issues. *Re-check* runs the same checks on your edits, so a wrong amount is caught again. Servings that are only the model's estimate are labelled as such.
3. **Change the recipe.** Choose the servings (and, if you know it, what the recipe makes now). The order of manipulations is shown but fixed: rules run in one order whatever you ask, and a substitution keeps its ingredient's scaling class, so the result is the same either way. The result shows original and new side by side, then a log of what changed, what was left alone and what to check by hand. The vegan option is shown disabled until that rule exists.

## Safety and limits

Text from a model is only ever put on the page as text, never as HTML. Uploads are saved under a generated name, never the one the browser sent. A file that is not a PDF, an image or text is refused, as is one over 20 MB. The page opens without an API key; the error appears when a model is first needed. Reading shows stages, not per-page progress, because `transcribe_pdf` returns when all pages are done.

## Tests

`tests/test_web.py` runs the app in-process with fake readers: no network, no key. It covers upload, the job, both readings of a doubtful word, the page image, refused files, structuring with edited lines, re-checking, scaling and the "not available" vegan answer.
