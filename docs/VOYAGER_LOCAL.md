# Local Qwen setup for Config B/C

Config B and Config C use the same OpenAI-compatible chat client. The repository does not commit
an API key or require a team deployment. The local development default uses Ollama with Qwen3 8B,
which is small enough for a demo and can be replaced after the team compares candidate models.

## Setup

Install Ollama separately, then pull the model:

```bash
ollama pull qwen3:8b
```

Copy the environment template and keep the file local:

```bash
cp .env.example .env
set -a
source .env
set +a
```

The `set -a`/`source` step exports the local values to the Python process; `.env` is intentionally not loaded or committed automatically.

The local values are:

```text
VOYAGER_BASE_URL=http://localhost:11434/v1
VOYAGER_API_KEY=
```

The pinned demo model is `qwen3:8b` in both Config B and Config C. A hosted OpenAI-compatible endpoint can replace the local URL, but the same model should be used for both configurations.

## Compare candidates before pinning

Use the same development requests, prompt version, temperature (`0`), and timeout for each model.
Record:

- valid JSON rate;
- exact extraction of origin, destination, vehicle, and starting battery;
- datastore grounding success;
- repair/fallback rate; and
- latency.

Do not use the frozen test split to choose a model. Do not commit `.env` or API keys.

## Smoke test

Bash/macOS:

```bash
set -a
source .env
set +a
.venv/bin/python -m clickless plan "From Phoenix to Flagstaff in my Spark LR, I have 65% battery and want lunch" --config B --no-input
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
$env:VOYAGER_BASE_URL = "http://localhost:11434/v1"
$env:VOYAGER_API_KEY = ""
$env:VOYAGER_MODEL = "qwen3:8b"
.venv\Scripts\python.exe -m clickless plan "From Phoenix to Flagstaff in my Spark LR, I have 65% battery and want lunch" --config B --no-input
```

## Reproducible local comparison

The initial comparison used the same prompt, temperature 0, local Ollama endpoint, and six requests
on November 14, 2026. It used these exact model tags and Ollama IDs:

| Model tag | Ollama model ID | Valid JSON | Complete grounded requests | Average latency | Median latency |
|---|---|---:|---:|---:|---:|
| `qwen3:8b` | `500a1f067a9f` | 6/6 | 5/6 | 18.4 s | 18.5 s |
| `qwen3:14b` | `bdbd181c33f2` | 6/6 | 5/6 | 40.3 s | 41.4 s |

Requests, in order:

1. Complete Phoenix → Flagstaff request with Spark LR, 65% battery, arrival reserve, lunch, and accessible restroom.
2. Complete Phoenix → Flagstaff request with Spark LR, 65% battery, and lunch.
3. Phoenix → Flagstaff request with lunch but no starting battery.
4. Phoenix → Flagstaff request with vague battery wording: “about half full”.
5. Phoenix → Flagstaff request with 65% battery and a CCS-only constraint.
6. Phoenix → Flagstaff request with “I’m in a hurry” and 65% battery.

The one incomplete/grounding case in each model was request 4. That result is expected: vague battery
language must remain unresolved and trigger clarification rather than being converted into a guessed
percentage. The comparison is a smoke comparison, not a frozen-test evaluation; run the development
split before finalizing the model choice.
