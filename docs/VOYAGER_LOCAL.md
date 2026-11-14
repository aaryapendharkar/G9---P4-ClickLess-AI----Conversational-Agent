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
VOYAGER_MODEL=qwen3:8b
```

The parser reads `VOYAGER_MODEL` when `configs/config_b.yaml` or `configs/config_c.yaml` still has
`model: CHANGE_ME`. This keeps the model choice outside source code while allowing the team to pin
one exact model for both configurations. A hosted OpenAI-compatible endpoint can replace the local
URL without changing the parser.

## Compare candidates before pinning

Use the same development requests, prompt version, temperature (`0`), and timeout for each model.
Record:

- valid JSON rate;
- exact extraction of origin, destination, vehicle, and starting battery;
- datastore grounding success;
- repair/fallback rate; and
- latency.

Do not use the frozen test split to choose a model. Do not commit `.env` or API keys.
