# finlyt Engineering Case Study: The Cookbook Challenge

Nov 9, 2026 · @Fabian Heiß

## TL;DR

Build a small AI-powered cookbook pipeline that turns a scanned, handwritten German recipe from 1887 into a structured recipe you can scale and make vegan, and tell us how you would make it reliable.

- **Input:** `Recipe.pdf` (attached), two scanned pages of a handwritten Sauerbraten recipe.
- **Build:** four stages: ingest, extract, manipulate, output.
- **Must use:** an AI coding agent to write the code, and an LLM inside the app.
- **Hand in:** a public GitHub repository with code, a README that explains your thinking, and a log of how you worked with the agent.
- **Time:** 3 calendar days to submit. We expect about 6 to 10 hours of work; please don't spend more.

We care more about how you think through the problem than about a polished app. An unfinished stage is fine if your README explains what you would do and why.

## About finlyt

finlyt builds an agentic AI platform that automates financial statement analysis for German banks.

**The problem.** When a bank lends to a business, German banking law requires it to review the borrower's financial situation every year. Credit analysts receive three kinds of documents:

- **Jahresabschluss (JA):** the annual financial statement under German GAAP (HGB): balance sheet, income statement, notes.
- **BWA:** monthly management reports, usually exported from DATEV by the tax advisor.
- **EÜR:** simple cash-basis profit statements from freelancers and small firms.

These arrive as PDFs and scans, and every tax advisor lays them out differently. Today analysts type hundreds of numbers by hand into the bank's analysis system, apply bank-specific correction rules, calculate KPIs and write an assessment. That takes hours per customer.

**What we do.** finlyt reads these documents, extracts and validates the numbers, maps them to the bank's chart of positions, applies the correction rules, calculates the KPIs and drafts the analysis. An analyst reviews and approves the result before anything goes into the bank's systems.

## Why a cookbook?

A recipe is a financial statement in disguise: a messy document that has to become clean, structured, trustworthy data, which is then transformed by rules and turned into a result someone acts on. You don't need to know anything about accounting to solve this case, but every decision you make here is one we make daily.

| Cookbook stage | finlyt equivalent | The shared problem |
| --- | --- | --- |
| 1. Drop in any recipe | Upload a JA, BWA or EÜR | Unknown formats, scans, handwriting, multi-page documents, layout that carries meaning |
| 2. Extract steps, ingredients, tools | Extract line items and amounts | Reading numbers and units correctly, validating them, removing duplicates, knowing what is missing |
| 3. Scale servings, make it vegan | Unit conversion (EUR vs. TEUR), the bank's correction rules (KOMP), booking rules | Applying rules deterministically, side effects across the document, explaining every change |
| 4. Step-by-step output, adjusted ingredients | KPI calculation and written analysis | Output that is correct, traceable to the source and useful to a human reviewer |

The hardest part in both worlds is the same: an LLM will happily give you a confident, well-formatted answer that is wrong. Your job is to build something that notices.

## The scenario

You are building a cookbook app. Users drop in any recipe, in any format and language, and the app turns it into a clean recipe they can adapt and cook from.

Your test input is `Recipe.pdf`: a Sauerbraten (marinated pot roast) from *Großmutter Josefines handgeschriebenes Kochbuch, Anno 1887*, pages 188 and 189. It is German, handwritten, photographed, and decorated with drawings. We chose it on purpose: real documents look like this, not like the clean examples in tutorials.

Don't optimise for this one recipe. The app should handle *any* recipe, and in the follow-up interview we will run your pipeline on recipes you haven't seen. Test it on at least one more recipe of your own choice, ideally in a different format or language.

Please don't commit `Recipe.pdf` to your public repository; it is a page from a printed book. Add it to `.gitignore` and tell us in the README where to put it. (At finlyt, customer documents never go into git either.)

## The four tasks

1. **Ingest: drop in any recipe.** Accept at least PDFs (scanned or digital), images and plain text. Detect what came in and route it to the right reading strategy.
2. **Extract: turn the document into structured data.** At minimum:
   - **Ingredients:** name, quantity, unit, preparation note, optional or not
   - **Tools:** pots, pans, utensils, whether stated or implied
   - **Steps:** in order, with the ingredients and tools each step uses, and durations or conditions where given
   - **Servings:** stated, or inferred and clearly flagged as inferred

   Validate what you extract and remove duplicates. Every value should be traceable to where it came from, and anything the app guessed rather than read must be marked as a guess.
3. **Manipulate: apply changes as rules.** Implement two:
   - **Change the number of servings**, for example to 6 people.
   - **Make it vegan.** Substitutions must carry through to the steps, not just the ingredient list.

   Manipulations should be combinable (vegan *and* 6 people), reproducible, and explainable: the user should see what changed and why.
4. **Output: produce something a person can cook from.** Step-by-step instructions and an ingredient list that reflect the manipulations. CLI, simple web page, Markdown or JSON: your choice. Showing what changed against the original is a plus.

**Stack:** any language and framework. We use Python and LangGraph, so that is a plus, but not required. A UI is optional; we would rather see a solid pipeline with tests than a pretty front end.

## Think it through: this matters more than the code

For each of the four stages, you should answer three questions: what makes this stage hard, what you chose to do about it, and what you considered and rejected. Be specific to the actual recipe; "OCR can be inaccurate" is not an insight, naming the word your pipeline misread is.

Some questions to get you started. We have deliberately not listed the answers, and there are more challenges in the recipe than these:

- **Ingest:** What do you do when a PDF has no text layer? Do the drawings matter? What happens when a sentence runs across a page break?
- **Extract:** What is the quantity of an ingredient the recipe never quantifies? How do you tell an ingredient from a tool, or an optional ingredient from a required one? What if the same ingredient appears twice? How many people does this recipe serve, and how would you know?
- **Manipulate:** Does everything scale linearly: salt, cooking time, the pot? What should "make it vegan" do when the main ingredient is the meat? What if vegan and scaling are applied in a different order?
- **Output:** How does the cook know which parts of the recipe the app read and which it guessed? What do you show when the app is not sure?

### Challenges that come from using AI

You must use an LLM in the app, even where plain code would do the job. Then tell us, honestly, where it helps and where it doesn't. Address at least these:

- **Hallucination:** how do you stop the model from inventing quantities, ingredients or steps that are not in the source, and how would you detect it if it did?
- **Non-determinism:** the same input can give different outputs. How do you get reproducible results, and how do you test something that isn't deterministic?
- **Validation without ground truth:** how do you know your extraction is correct? What would a small evaluation set look like?
- **LLM or code:** which parts did you give to the LLM, which to plain code, and which would you move in production?
- **Cost and latency:** what does one run cost, and how long does it take?
- **Trust:** how does a reviewer see the model's confidence and the source of each value?

## Rules

**1. Using an AI coding agent is mandatory.** Write the code with Claude Code, Codex, Cursor or OpenCode. OpenCode with a low-cost model is the cheapest option and good enough; we care about how you steer the agent, not which one you pick.

Treat the agent like a fast, eager intern who is often wrong: it writes code quickly, misreads instructions, invents APIs and declares victory too early. You are responsible for every line it produces.&#32;

**2. Using an LLM inside the app is mandatory.** Any provider and model. Keep costs low: small and cheap models are fine, and choosing one well is part of the task. Never commit API keys; provide a `.env.example` instead.

**3. All work goes to GitHub.** Use a public repository and commit as you go. We want to see your history, not one squashed commit at the end.

## What to hand in

Send us the link to your repository once you're done. It should contain:

- [ ] **Code** for all four stages, runnable with no more than three setup commands
- [ ] **README** covering: how to run it, an architecture overview, the challenges and decisions per stage, the AI challenges, and what you would do with another week
- [ ] **Example outputs** in an `examples/` folder for the Sauerbraten: the raw extraction, 6 servings, vegan, and vegan for 6 servings
- [ ] **At least one more recipe** of your choice, run through the same pipeline
- [ ] **Tests**, at least for the deterministic parts such as scaling and unit conversion

**After you submit:** we schedule a 60-minute call. You walk us through your repo for 20 minutes, then we run it on a recipe you haven't seen and ask you to make a small change live, with your coding agent.

## How we evaluate

Problem thinking and AI engineering together make up more than half of the assessment; the code itself is less than a quarter.

| Area | Weight | What we look for |
| --- | --- | --- |
| Problem thinking | 30% | You found the real challenges in the recipe, named them specifically and made deliberate trade-offs |
| AI engineering | 25% | Sensible split between LLM and code, guards against hallucination, a way to measure quality, cost awareness |
| Code and tests | 20% | Readable structure, tests that matter, runs on first try |
| Working with the agent | 15% | Clear instructions, critical review of its output, honest `AI_LOG.md` |
| Communication | 10% | A README a teammate could pick up tomorrow |

**What we don't score:** UI polish, number of features, or getting every word of 1887 handwriting right. A pipeline that knows when it is unsure beats one that is confidently wrong.

Questions during the case are welcome, and asking good ones counts in your favour.
