# Matcher evaluation

NU Connect Hub's Discover page suggests up to five students a viewer might want to work with.
Each suggestion is scored by `score_pair` in `app/ml/scoring.py`, a weighted sum of four
components:

- **semantic:** how similar the two profiles read, from text embeddings, or from shared interests
  when an embedding is missing;
- **complementary:** how many of the skills the viewer is seeking the candidate has;
- **availability:** how much of the coming week they are both free;
- **goal:** how much their goals (studying, building a project, research, mentorship) overlap.

The weights are hypotheses. This directory holds one command that tests them: it builds labelled
synthetic students and compares three ways of ranking them, all scored by the production code,
so what is measured is exactly what students are shown.

## Running it

From `apps/api`, with the virtual environment active.

With the real model (what the committed reference report uses):

```sh
pip install -c requirements.txt -e ".[ml]"
python -m experiments.evaluate --seed 7 --encoder sentence-transformers
```

On Linux, add `--extra-index-url https://download.pytorch.org/whl/cpu` to the install for the
CPU-only PyTorch wheel. The first run downloads the model (about 90 MB) into the Hugging Face
cache. The model is the one production ships by default (`embedding_model_id` in
`app/core/config.py`, `sentence-transformers/all-MiniLM-L6-v2`); a local `.env` override doesn't
change it.

Without the `ml` extra:

```sh
python -m experiments.evaluate --encoder fake
```

The fake encoder turns a hash of each text into a vector, so similar profiles do *not* get similar
vectors. It exists to check the pipeline end to end (the tests and CI run it) and its numbers mean
nothing. Asking for the real encoder without the extra installed stops with the install command
above, not a traceback.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--seed` | `7` | Seeds the data and the split |
| `--encoder` | `sentence-transformers` | `sentence-transformers` (the real model) or `fake` |
| `--k` | `5` | How many top results are scored: Discover shows five |
| `--out` | `experiments/reports` | Where the report is written |
| `--n` | `240` | How many synthetic students to generate |

It writes `<out>/<model>-seed<seed>-n<n>-k<k>.json` and a `.md` with the same name (for example
`all-minilm-l6-v2-seed7-n240-k5.json`), and prints the Markdown. The name carries every input that
changes the numbers, so a run with another seed, size or cut lands beside the reference report
instead of overwriting it. Running it twice with the same flags gives a byte-identical JSON file:
the report records no time of its own, keys are sorted and every number is rounded to 4 decimal
places. (With the real model that holds on the same machine and library versions; a different
PyTorch build could in principle nudge a score enough to swap two near-tied candidates.)

Git ignores everything in `experiments/reports/` except the committed reference pair, so trying
other seeds, sizes or the fake encoder there leaves nothing to commit by accident. To publish a new
reference, change the two exceptions in the repository's `.gitignore` to match.

## The data

`synthetic.py` generates the students; nothing comes from real accounts. We don't have real
outcomes to learn from (nobody can yet say which suggested pairs went on to work well together),
and synthetic students let the right answer be known exactly. The cost is that the generator's
assumptions are built into every number.

Each of the 240 students belongs to one of six hidden topic clusters, 40 to a cluster:
`ml-research`, `web-apps`, `mobile`, `games`, `data-viz` and `security`. The cluster shapes, with
noise:

- **The headline and bio.** 80% of the time each is a paraphrased template written for the
  cluster ("Gameplay programmer and game jam regular"); otherwise it's generic wording that says
  nothing about the topic.
- **The skills a student has (2 to 4) and seeks (1 to 3).** Each comes from the cluster's skill
  list 80% of the time, otherwise from any skill. The lists overlap (Python appears in three
  clusters, C and Rust in two), so a skill alone doesn't give a cluster away. A student never
  seeks a skill they already have.
- **Interests (1 to 4).** Each comes from the cluster's interest list 70% of the time.

Independent of the cluster: goals (each chosen with even odds, at least one), a timezone (mostly
`America/New_York`, some `America/Chicago`, `America/Los_Angeles`, `Europe/London` and
`Asia/Kolkata`), and 1 to 5 weekly availability slots between 09:00 and 23:00 local time, which
never overlap on the same day (the rule the profile API enforces).

Skills and interests use the real slugs and names seeded by migration `0003`, and the text handed
to the encoder is built by the production `profile_text`, so the encoder sees exactly what it
would see for a real profile. The cluster itself is never given to any ranker.

## What a good match is

The label (`labels.py`, version `good-match-v1`) says a candidate is a good match for a viewer
when **all three** of these hold:

1. **They are in the same hidden cluster.** They would want to work on the same kind of thing.
   This is the clause no ranker can read off a profile field: it has to be inferred from how the
   profile reads, which is what the semantic component is for. Without it, the label would only
   reward what the rules already compute, and the comparison would be circular.
2. **The candidate has at least one skill the viewer is seeking.** Discover promises teammates
   who bring something you lack. Someone on the same topic with nothing you need is a peer, not a
   teammate.
3. **They share at least 60 minutes in the viewer's coming week.** A match you can never meet is
   no use, and an hour is about the least time in which two people can hold a real working
   session. The minutes come from the production availability code, across time zones, for the
   week starting at a fixed reference time (`2026-09-07T12:00:00+00:00`, a Monday) rather than the
   clock, so the label never changes with the day the command is run.

Shared interests and goals are deliberately not in the label: the systems use them as evidence,
and making them part of the answer would grade the rules on their own inputs.

This is a definition we chose, not an observation of how real students behave. Changing it means
changing `LABEL_VERSION` too, so reports made under different definitions are never compared.

## The split

The students are shuffled with a seeded random stream and split in half: 120 **test** viewers and
120 **dev** viewers. Only the test viewers are reported. The dev viewers are held back for anyone
tuning the weights or the generator, so that tuning never happens on the numbers being reported.
The split is over who is asking: every test viewer is ranked against all 239 other students, dev
viewers included.

## The three systems

- **`semantic_only`:** the cosine similarity of the two profiles' embeddings, and nothing else.
- **`rules_only`:** `score_pair` with the interest overlap (Jaccard similarity) as the semantic
  component: what production does when an embedding is missing.
- **`embeddings_plus_rules`:** `score_pair` with the embedding similarity as the semantic
  component: what production does when both embeddings are fresh.

For the two `score_pair` systems the needed skills are the viewer's seeking skills, and a candidate
with no reason to show (no needed skill, shared interest, shared time or shared goal) is dropped,
exactly as the ranker drops them. Every system sorts by score, highest first, breaking ties by
user id.

## The metrics

All are computed on each system's top k (5) for each test viewer.

- **P@5 (precision at 5):** the share of the five slots holding a good match, averaged over test
  viewers. A system that shows fewer than five is still divided by five: an empty slot helps no
  one.
- **NDCG@5 (normalised discounted cumulative gain at 5):** rewards good matches near the top. A
  good match at rank r is worth 1 / log2(r + 1) (1 at rank 1, 0.63 at rank 2, 0.5 at rank 3, ...).
  The sum is divided by the best possible sum for that viewer, with all their good matches at the
  top, counting every good match in the pool, including ones the system never surfaced. 1 is a
  perfect ordering; a viewer with no good match scores 0. Averaged over test viewers.
- **Constraint rates:** of every recommendation shown in a top five, across all test viewers, the
  share where the candidate has a needed skill, the share with 60 or more shared minutes, and the
  share with both. These are the two clauses of the label a ranker can see. A system with high
  constraint rates but low precision is finding practical matches on the wrong topic.
- **Shown per viewer:** how many results a system put in the top five, on average. Below 5 means
  it ran out of candidates it could explain.

The report also gives the number of good matches per test viewer and **the best possible P@5 and
NDCG@5**, what a perfect ranker would score. Many viewers have fewer than five good matches and
some have none, so the ceiling is well below 1. Read each system's numbers against it.

## Reading the reference report

The committed report is `reports/all-minilm-l6-v2-seed7-n240-k5.md` (and `.json` with the same
numbers), made with the real model and the default flags. Its table has one row per system and
one column per metric; below it is everything needed to reproduce it: the model, the data, the
label definition, the split, the reference time and the weights.

The question it answers is in the comparison between rows:

- **`embeddings_plus_rules` against `rules_only`:** does embedding similarity rank good matches
  higher than the interest fallback? This is the row pair that matters for production.
- **`semantic_only`:** what text similarity does with none of the practical constraints. Low
  constraint rates here show why the rules are there.
- **The same seed with `--encoder fake`:** a useful baseline for the row pair above. Its semantic
  component is near-zero noise, so `embeddings_plus_rules` under the fake encoder is roughly the
  rules with the interest fallback taken out. The gap between it and the real model's
  `embeddings_plus_rules` is what the meaning of the text contributes; the gap between it and
  `rules_only` is what the interest fallback costs or earns.

Keep in mind what it can't say. The data is synthetic and the label is ours, and
the generator decided how strongly a profile's text and interests reveal its cluster. So the
absolute numbers say little about how real students will be matched, and even the size of a gap
between systems reflects those choices. What it is good for: checking that the production scorer,
end to end, uses each signal in the direction intended, and catching a change (to the weights, the
scorer or the text sent to the encoder) that makes it worse. It's also one seed: before trusting
a small difference, run a few more (`--seed 8`, `--seed 9`, ...) and see whether it holds. Each
seed writes its own file (`all-minilm-l6-v2-seed8-n240-k5.md`, ...), which git ignores.

## Files

- `synthetic.py`: the generator, `generate(seed, n)`.
- `labels.py`: the good-match definition, `is_good_match`.
- `metrics.py`: `precision_at_k`, `ndcg_at_k` and `constraint_rates`.
- `evaluate.py`: the command.
- `reports/`: the committed reference report, `all-minilm-l6-v2-seed7-n240-k5.{json,md}`; any
  other report written here stays local.

The tests are in `tests/test_evaluation.py`. They use only the fake encoder and need no database.
