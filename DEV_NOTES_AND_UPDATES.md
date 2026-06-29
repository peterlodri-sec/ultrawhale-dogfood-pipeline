### DEV_NOTES_AND_UPDATES
*Date: 2026-06-29 - peter*

#### Performance Benchmarking: Pre vs. Post Optimization

 ┌────────────────────────┬─────────────────────────────┬──────────────────────────┬────────────────────┐
 │ Metric                 │ Pre-Optimization (Baseline) │ Post-Optimization (BIS)  │ Improvement        │
 ├────────────────────────┼─────────────────────────────┼──────────────────────────┼────────────────────┤
 │ Generation Speed (t/s) │ ~4.24 t/s                   │ ~9.20 t/s                │ ~2.17x Faster      │
 ├────────────────────────┼─────────────────────────────┼──────────────────────────┼────────────────────┤
 │ Memory Bottleneck      │ High (frequent eviction)    │ Low (Unified Cache/q8_0) │ Improved Stability │
 ├────────────────────────┼─────────────────────────────┼──────────────────────────┼────────────────────┤
 │ Token Efficiency       │ Wasted on <think> blocks    │ Pure, direct Q&A         │ ~20% Higher Yield  │
 └────────────────────────┴─────────────────────────────┴──────────────────────────┴────────────────────┘
