# Token usage report

> **Eff. input** = billed-equivalent input at base rate: `input − cache_read×0.90 + cache_write×0.25` (cache reads cost 0.10×, cache writes cost 1.25×).

| Agent | API calls | Input tokens | Output tokens | Total tokens | Cache read | Cache write | Eff. input | Peak ctx tokens | Peak ctx % |
|-------|----------:|-------------:|--------------:|-------------:|-----------:|------------:|-----------:|----------------:|-----------:|
| Designer | 2 | 19,782 | 5,672 | 25,454 | 7,273 | 0 | 13,236 | 12,506 | 6.3% |
| Coder | 53 | 1,382,116 | 17,226 | 1,399,342 | 1,291,417 | 0 | 219,841 | 33,649 | 16.8% |
| Code reviewer | 15 | 193,336 | 9,645 | 202,981 | 148,440 | 0 | 59,740 | 20,446 | 10.2% |
| Tester | 45 | 1,089,054 | 22,008 | 1,111,062 | 1,002,464 | 0 | 186,836 | 40,820 | 20.4% |
| QA engineer | 10 | 176,223 | 6,582 | 182,805 | 127,474 | 0 | 61,496 | 26,214 | 13.1% |
| **TOTAL** | **125** | **2,860,511** | **61,133** | **2,921,644** | **2,577,068** | **0** | **541,150** | — | — |
