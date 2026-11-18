# Matcher evaluation: sentence-transformers/all-MiniLM-L6-v2, seed 7

| System | P@5 | NDCG@5 | Needed skill | 60+ shared minutes | Both | Shown per viewer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `semantic_only` | 0.0833 | 0.1117 | 0.4550 | 0.1633 | 0.0850 | 5.0000 |
| `rules_only` | 0.2317 | 0.3488 | 0.8817 | 0.4133 | 0.3533 | 5.0000 |
| `embeddings_plus_rules` | 0.2983 | 0.4609 | 0.9800 | 0.4483 | 0.4283 | 5.0000 |

- **Model:** `sentence-transformers/all-MiniLM-L6-v2` (encoder `sentence-transformers`)
- **Data:** synthetic: experiments/synthetic.py, 240 students in 6 hidden clusters (ml-research, web-apps, mobile, games, data-viz, security)
- **Labels:** `good-match-v1`: same hidden topic cluster, the candidate has at least one of the viewer's seeking skills, and at least 60 shared minutes in the viewer's reference week
- **Split:** 120 test viewers (reported), 120 dev viewers (reserved, not reported), 239 candidates per viewer
- **Good matches:** 2.6083 per test viewer on average; 92 of 120 test viewers have at least one, so a perfect ranker would score P@5 0.4700 and NDCG@5 0.7667
- **Reference now:** 2026-09-07T12:00:00+00:00
- **Weights:** semantic 0.35, complementary 0.3, availability 0.25, goal 0.1; availability saturates at 240 shared minutes

The data is synthetic and the labels are our own definition, so the absolute numbers mean little: compare the systems with each other.
