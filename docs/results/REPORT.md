# Evaluation report

_Synthetic users (generated data, not real people). Each task runs twice with the same agent: once reading the data directly, once through need-to-know._

## Everyday tasks

| | Reading the data directly | Through need-to-know |
|---|---:|---:|
| Tasks | 502 | 502 |
| Task success | 97.2% | 100.0% |
| Tasks with a wrong action | 14 | 0 |
| Times the agent asked the user | 0 | 14 |
| Fields released per task (mean) | 7,363 | 906 |
| Context precision | 9.2% | 54.2% |
| Context recall | 100.0% | 100.0% |
| Sensitive fields released without consent | 703,300 | 0 |
| Health fields released | 180,720 | 0 |
| Photo shares that sent precise location without consent | 84 | 0 |
| Median time to build context | 81.2 ms | 77.4 ms |

![Exposure](exposure.png)

![Precision](precision.png)

## Planted instructions (prompt injection)

Tasks that touch a record whose caption or note contains planted instructions. The agent here obeys any instruction it reads (worst case).

| | Reading the data directly | Through need-to-know |
|---|---:|---:|
| Tasks | 16 | 16 |
| Injected text reached the agent | 16 | 7 |
| Injected actions executed | 24 | 0 |
| Task success | 0% | 100% |

