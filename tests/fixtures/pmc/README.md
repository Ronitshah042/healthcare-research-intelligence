# PMC test fixtures

Trimmed copies of two real articles from the PMC Open Access subset, retrieved
from the PMC Cloud Service on 2026-11-17. They are used only by the unit
tests; the tests never contact PMC.

| Files | Article | Licence |
| --- | --- | --- |
| `PMC12826829.1.xml`, `PMC12826829.1.json` | Prevalence, correlates, and mental and physical health burden of cardiovascular disease in older U.S. military veterans. *PLOS Ment Health.* 2024. doi:10.1371/journal.pmen.0000192. PMCID PMC12826829. | CC0 1.0 (public domain dedication) |
| `PMC10009402.1.xml`, `PMC10009402.1.json` | Labour market marginalisation in young adults diagnosed with attention-deficit hyperactivity disorder (ADHD): a population-based longitudinal cohort study in Sweden. *Psychol Med.* 2023;53(4):1224–32. doi:10.1017/S0033291721002701. PMCID PMC10009402. | CC BY 4.0, <https://creativecommons.org/licenses/by/4.0/> |

**Changes made** (as CC BY requires us to indicate): affiliation,
correspondence, history, keyword, and funding metadata were removed (author
names were kept); the abstract was cut to
its first two paragraphs; each body section was cut to its first two
paragraphs and at most one table and one subsection; supplementary sections,
except one, were removed; back matter was cut to the acknowledgements and the
first two references. From the metadata JSON, `media_urls` and `pdf_url` were
removed. No text was otherwise altered.

Both fixtures keep tables, citation markers, and references on purpose, so the
tests can check that the parser removes them.
