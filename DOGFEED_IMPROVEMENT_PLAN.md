# Ultrawhale Dogfeed Improvement Plan

## Current State
- **Generation**: Local Ollama (Qwen3.6-27B) generates ~20-30 Q&A pairs/hour
- **Pipeline**: ralph loop with ~90s retry interval
- **Data**: ~8.8MB across 32 files, minimal quality filtering
- **Output**: JSONL with 16 metadata fields

---

## Phase 1: Quality Scoring + Filtering

### 1.1 Add Quality Metrics
- **Length check**: Filter questions <10 tokens, answers >5000 tokens
- **Coherence**: LLM-based scoring (0-1) via local model or HF inference
- **Diversity**: Avoid regenerating similar Q&A pairs (embedding-based dedup)
- **Topic relevance**: Verify generated Q aligns with requested topic

### 1.2 Implement per-sample scoring
```python
score = (coherence × 0.4) + (length_ok × 0.3) + (diversity × 0.3)
save_only_if(score > 0.65)
```

**Output**: Only ~70-80% of generations saved → higher quality dataset

---

## Phase 2: Multi-Model Ensemble (HuggingFace Inference)

### 2.1 Leverage HF Inference API
Replace local-only generation with hybrid approach:
- **Fast questions**: Local Qwen (11434)
- **Complex questions**: HF Inference API (better reasoning models)
- **Validation**: Cross-check answers with 2nd model

Models to try:
- `meta-llama/Llama-3-70b-chat-hf` (strong reasoning)
- `mistralai/Mixtral-8x7B-Instruct-v0.1` (MoE efficiency)
- `NousResearch/Nous-Hermes-2-Mixtral-8x7B-DPO` (specialized)

### 2.2 Implementation
```python
def generate_qa_pair_hybrid(topic, question_type):
    # Fast path: local Qwen
    question = qwen_generate(topic, question_type)
    answer = qwen_answer(question)
    score_local = score(question, answer)
    
    if score_local < 0.6:  # Low confidence
        # Fallback: HF Inference (slower but better)
        answer_hf = hf_inference(question, model="70b")
        if score(question, answer_hf) > score_local:
            answer = answer_hf  # Use better response
    
    return {question, answer, score}
```

**Cost**: ~$0.01-0.05 per HF API call (negotiate bulk pricing)
**Benefit**: 20-30% quality improvement, diversity of outputs

---

## Phase 3: Parallel Generation + Adaptive Scheduling

### 3.1 Parallel ralph loops
- Run N parallel generation processes (one per topic category)
- Each with independent retry logic
- Aggregate into single HF dataset

### 3.2 Adaptive timing
- Monitor generation success rate
- Increase interval if model is slow/overloaded
- Decrease if generation is fast (HF inference available)

```python
success_rate = successful_generations / total_attempts
if success_rate > 0.8:
    interval = min(interval * 0.9, 30)  # Speed up
elif success_rate < 0.5:
    interval = min(interval * 1.2, 300)  # Slow down
```

---

## Phase 4: Active Learning + Difficulty Sampling

### 4.1 Difficulty-aware question generation
- Easy: Fundamentals, definitions (→ broad coverage)
- Medium: Applications, practical coding
- Hard: Theory, research, edge cases

Sample distribution:
```
- 40% easy (breadth)
- 40% medium (depth)
- 20% hard (frontier)
```

### 4.2 User feedback loop
If you start using these Q&A pairs:
- Track which are actually helpful
- Adjust generation toward high-feedback topics
- Deprioritize low-engagement topics

---

## Implementation Roadmap

| Phase | Effort | Impact | Timeline |
|-------|--------|--------|----------|
| 1: Quality scoring | 2-3h | +15-20% quality | Week 1 |
| 2: HF Inference | 3-4h | +20-30% quality | Week 1-2 |
| 3: Parallel ralph | 1-2h | 3-5x throughput | Week 2 |
| 4: Active learning | 2-3h | Adaptive diversity | Week 2-3 |

---

## Next Steps

1. **Immediate (today)**:
   - Upload current dogfeed to HF dataset
   - Add quality scoring to `generate_dogfeed.py`

2. **Short-term (this week)**:
   - Set up HF Inference API token
   - Implement hybrid generation function
   - Test on small batch (20 samples)

3. **Medium-term (next week)**:
   - Launch parallel ralph loops (3-5 processes)
   - Monitor quality/throughput metrics
   - Adjust sampling distribution based on results

4. **Long-term**:
   - Integrate user feedback if using for training
   - Fine-tune model selection per topic
   - Consider self-supervised filtering (cross-model agreement)

---

## Metrics to Track

- **Throughput**: Samples/hour (target: 50-100/hr with parallel)
- **Quality score**: Distribution (target: mean >0.70)
- **Uniqueness**: % new samples (target: >85%)
- **Topic coverage**: Balanced across N topics (target: Gini <0.15)
- **Coherence**: Manual spot-check (target: >90% "reasonable")
