# Local Qwen setup for Config B/C

Config B and Config C use the same OpenAI-compatible chat client. The repository does not commit
an API key or require a team deployment. The local development default uses Ollama with Qwen3 8B,
which is small enough for a demo and can be replaced after the team compares candidate models.

There are three ways to reach a model. Only the first reproduces the project's evaluation.

| Access | `VOYAGER_BASE_URL` | API key | Model |
|---|---|---|---|
| [Local Ollama](#setup) (recommended) | `http://localhost:11434/v1` | none | pinned `qwen3:8b` |
| [ASU Voyager](#asu-voyager) | `https://openai.rc.asu.edu/v1` | your Voyager key | an ID that Voyager lists |
| [Another hosted provider](#another-hosted-provider) | the provider's `/v1` URL | the provider's key | an ID that provider serves |

## Setup

Run every command from the repository root. Install Ollama separately; the desktop app starts the
server, otherwise leave `ollama serve` running in a second terminal.

Bash (macOS/Linux):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,nlp,kg]"
ollama pull qwen3:8b
ollama list
cp -n .env.example .env
set -a
source .env
set +a
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev,nlp,kg]"
ollama pull qwen3:8b
ollama list
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$env:VOYAGER_BASE_URL = "http://localhost:11434/v1"
Remove-Item Env:VOYAGER_API_KEY -ErrorAction SilentlyContinue  # local Ollama needs no key
```

`nlp` installs Sentence-BERT for Config B/C and `kg` installs rdflib for Config C. The `qwen3:8b`
line of `ollama list` must show ID `500a1f067a9f`.

Local Ollama must be running, through the desktop application or `ollama serve`.

The app does not read `.env` by itself. In Bash, `set -a`/`source` exports its values; in
PowerShell, set them with `$env:` as above. Neither copy command overwrites an existing `.env`.
Do not commit it.

The local values are:

```text
VOYAGER_BASE_URL=http://localhost:11434/v1
VOYAGER_API_KEY=
```

The pinned demo model is `qwen3:8b` in both Config B and Config C, using Ollama digest `500a1f067a9f` with Q4_K_M quantization and thinking disabled. The configs set the model name, so `VOYAGER_MODEL` is not needed.

## ASU Voyager

Voyager is ASU Research Computing's OpenAI-compatible LLM gateway. Official pages:
[API access](https://docs.rc.asu.edu/ai/api/) and
[requesting an account](https://docs.rc.asu.edu/voyager-request-account/).

1. Request an LLM API account in the [Voyager portal](https://voyager.rc.asu.edu). It needs an
   ASURITE account. The portal requires the ASU SSLVPN (the account page says on campus too); API
   calls themselves work without the VPN.
2. In the portal open your profile, then **Keys**, then **Create API key**. Copy the key right
   away: it is shown only once.
3. Set the base URL, including `/v1`, and enter the key at a prompt so it stays out of your shell
   history. Never commit it.
4. List the model IDs. Do not assume Voyager's ID for a model matches Ollama's `qwen3:8b`.

Bash (macOS/Linux):

```bash
export VOYAGER_BASE_URL="https://openai.rc.asu.edu/v1"
printf "Voyager API key: "; read -rs VOYAGER_API_KEY; echo
export VOYAGER_API_KEY
curl -s "$VOYAGER_BASE_URL/models" -H "Authorization: Bearer $VOYAGER_API_KEY"
```

Windows PowerShell:

```powershell
$env:VOYAGER_BASE_URL = "https://openai.rc.asu.edu/v1"
$secure = Read-Host "Voyager API key" -AsSecureString
$env:VOYAGER_API_KEY = [System.Net.NetworkCredential]::new("", $secure).Password
Invoke-RestMethod -Uri "$env:VOYAGER_BASE_URL/models" -Headers @{ Authorization = "Bearer $env:VOYAGER_API_KEY" }
```

## Another hosted provider

`VOYAGER_API_KEY` is a generic bearer-token variable despite its name: it can hold a key from any
OpenAI-compatible provider. The endpoint must support `/v1/chat/completions`. A non-local endpoint
without a key is refused. Set the two variables as in the Voyager commands above.

A standard OpenAI key is not a drop-in replacement. Configs B and C pin Ollama's `qwen3:8b`, so a
provider that does not serve that ID needs `llm.model` changed in the config.

**Results from Voyager or another provider are not comparable with the frozen project
evaluation**, which uses the pinned local model. Keep any config change local, and use the same
model for B and C. Neither hosted route has been tested with this repository's client.

## Warm up

This section and the clean-up commands are for local Ollama.

Check that the server answers, then load the model and keep it loaded for two hours. The first
request after a cold start can take minutes.

Bash (macOS/Linux):

```bash
curl -s http://localhost:11434/api/tags
curl -s http://localhost:11434/api/generate -d '{"model": "qwen3:8b", "prompt": "OK", "keep_alive": "2h", "think": false, "stream": false, "options": {"num_predict": 1}}'
```

Windows PowerShell (`curl` is an alias for `Invoke-WebRequest` there, so call `curl.exe`; the POST
uses `Invoke-RestMethod` to avoid quoting JSON for a native program):

```powershell
curl.exe -s http://localhost:11434/api/tags
$body = @{ model = "qwen3:8b"; prompt = "OK"; keep_alive = "2h"; think = $false; stream = $false; options = @{ num_predict = 1 } } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:11434/api/generate -ContentType "application/json" -Body $body
```

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

After setup, in the same terminal. Use `--config C` to check Config C.

Bash (macOS/Linux):

```bash
.venv/bin/python -m clickless plan "From Phoenix to Flagstaff in my Spark LR, I have 65% battery and want lunch" --config B --no-input
```

Windows PowerShell:

```powershell
.venv\Scripts\python.exe -m clickless plan "From Phoenix to Flagstaff in my Spark LR, I have 65% battery and want lunch" --config B --no-input
```

## Clean up

Unload the model and clear the variables from the current terminal. `.env` stays on disk.

Bash (macOS/Linux):

```bash
ollama stop qwen3:8b
unset VOYAGER_BASE_URL VOYAGER_API_KEY VOYAGER_MODEL NREL_API_KEY OSRM_BASE_URL CLICKLESS_DATA_SOURCE
```

Windows PowerShell:

```powershell
ollama stop qwen3:8b
Remove-Item Env:VOYAGER_BASE_URL, Env:VOYAGER_API_KEY, Env:VOYAGER_MODEL, Env:CLICKLESS_DATA_SOURCE -ErrorAction SilentlyContinue
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
