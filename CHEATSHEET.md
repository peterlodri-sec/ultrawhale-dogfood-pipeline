# AGENT CHEATSHEET: Ultrawhale

## Command Reference
| Goal | Command |
| :--- | :--- |
| **Start System** | `task run` |
| **Stop System** | `task stop` |
| **Live Pretty Logs** | `task logs:pretty` |
| **Status Check** | `task status` |

## Performance Knobs (`llm-server.sh`)
- `-c 8192`: Context size
- `-np 2`: Inference slots
- `--cache-type-k q8_0`: Quantized KV cache
- `--reasoning off`: Disabled thinking

## Data Flow
1. **Workers** -> `dogfeed_parallel/` (Raw JSONL)
2. **Compression** -> `*_kompressed.jsonl` (Kompress-v8)
3. **Aggregator** -> `dogfeed_*_aggregated.jsonl`
4. **Upload** -> `PeetPedro/ultrawhale-dogfood` (HuggingFace)

## Troubleshooting
- **HF Auth:** Ensure `.env` contains `HF_TOKEN`.
- **Worker Hangs:** Check `/tmp/ralph.log` & `/tmp/workerX.log`.
- **System Throttling:** If `ResourceManager` is pausing, check `ralph_parallel.py` limits (50/75).
- **Log Source:** All logs reside in `/tmp/`.
