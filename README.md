# Leetcode-Solver

A script that solves LeetCode problems using AI (Gemini, Hugging Face, Nvidia NIM, or Groq), then submits solutions to your account. Supports the daily challenge, a single problem by number, or a range of problems with optional difficulty filter. Premium problems are always skipped.

## Features

- **Daily challenge** – Fetch and solve today’s LeetCode daily, then submit.
- **Single problem** – Solve one problem by its frontend number (e.g. `--chall 3637`).
- **Range mode** – Solve many problems in a numeric range (e.g. `--range 2000 3000`), with optional **difficulty filter** (`--diff easy` / `medium` / `hard`).
- **Multiple AI providers** – Gemini (Google), Hugging Face Inference API, Nvidia NIM, or Groq.
- **Multiple languages** – Submit in Python 3, C, C++, Java, and other LeetCode-supported languages.
- **No premium** – Premium problems are never attempted; already-accepted problems are skipped in range mode.

## Requirements

- Python 3.10+
- A LeetCode account (session cookie for submitting)
- At least one AI API key (Gemini, Hugging Face, Nvidia, or Groq)

## Installation

```bash
cd Leetcode-Solver
python -m venv .venv
source env/bin/activate   # Windows: .\env\Scripts\activate
pip install -r requirements.txt
```

## Configuration

1. Copy the example env file and edit it:

   ```bash
   cp .env.example .env
   ```

2. Set these in `.env`:

   | Variable | Required | Description |
   |----------|----------|-------------|
   | `LEETCODE_SESSION` | Yes | Session cookie from leetcode.com (see below). |
   | `LEETCODE_CSRF` | If submit fails with CSRF | `csrftoken` cookie from leetcode.com. |
   | `GEMINI_API_KEY` | For `--model gemini` | From [Google AI Studio](https://aistudio.google.com/apikey). |
   | `HUGGINGFACE_API_KEY` | For `--model hf` | From [Hugging Face → Settings → Access Tokens](https://huggingface.co/settings/tokens). |
   | `NVIDIA_API_KEY` | For `--model nvidia` | From [Nvidia NIM / build.nvidia.com](https://build.nvidia.com). |
   | `GROQ_API_KEY` | For `--model groq` | From [Groq Console](https://console.groq.com/keys). |

### Getting LeetCode cookies

1. Log in at [leetcode.com](https://leetcode.com).
2. Open DevTools (F12) → **Application** (or Storage) → **Cookies** → `https://leetcode.com`.
3. Copy the value of **`LEETCODE_SESSION`** into `.env`.
4. If submissions fail with “CSRF verification failed”, also copy **`csrftoken`** into `LEETCODE_CSRF`.

## Usage

Use **exactly one** of: `--daily`, `--chall N`, or `--range LO HI`. Combine with `--lang` and `--model` as needed. For `--range`, you can add `--diff` to restrict by difficulty.

### Daily challenge

Solve and submit today’s daily problem (default language: Python 3, default model: Gemini):

```bash
python bot.py --daily
python bot.py --daily --lang cpp --model gemini
```

### Single problem by number

Solve one problem by its frontend number (e.g. 3637). Premium is skipped.

```bash
python solver.py --chall 3637
python solver.py --chall 2024 --lang python3 --model hf
```

### Range of problems

Solve all non-premium, not-yet-accepted problems in a numeric range. Problems are processed in order by problem number.

```bash
# All problems from 2000 to 3000 (any difficulty)
python solver.py --range 2000 3000 --lang python3

# Only Easy problems in that range
python solver.py --range 2000 3000 --diff easy --lang python3

# Only Medium, using Hugging Face
python solver.py --range 1000 1500 --diff medium --model hf

# Only Hard, using Nvidia
python solver.py --range 1 500 --diff hard --model nvidia
```

If you don’t pass `--diff`, all difficulties in the range are considered. With `--diff`, only **Easy**, **Medium**, or **Hard** (as returned by LeetCode) are included.

### Options summary

| Option | Description |
|--------|-------------|
| `--daily` | Solve and submit the daily challenge. |
| `--chall N` | Solve a single problem by number; premium skipped. |
| `--range LO HI` | Solve all non-premium, unsolved problems with number in [LO, HI]. |
| `--diff {easy,medium,hard}` | With `--range` only: restrict to this difficulty. |
| `--lang LANG` | Submission language (default: `python3`). Examples: `python3`, `c`, `cpp`, `java`, `javascript`, `go`, `rust`. |
| `--model {gemini,hf,nvidia,groq}` | AI provider (default: `gemini`). |

## How it works

1. **Authentication** – Uses `LEETCODE_SESSION` (and optionally `LEETCODE_CSRF`) to talk to LeetCode. For submit, a CSRF token is obtained from the problem page when needed.
2. **Problem data** – Daily uses the GraphQL “daily challenge” query; single/range use the problemset list and per-problem “question by slug” query. Difficulty and premium status come from the list or question payload.
3. **AI solution** – The chosen provider (Gemini, Hugging Face, Nvidia, or Groq) gets the problem text, examples, and starter code and returns code in the requested language. The bot retries a few times if the model doesn’t return valid code.
4. **Submit** – Solution is POSTed to LeetCode’s submit endpoint; then the bot polls the submission until it’s judged (Accepted, Wrong Answer, Runtime Error, etc.).
5. **Range + difficulty** – In range mode, the problemset is filtered by problem number, premium, and (if set) `--diff`; only unsolved problems in that set are fetched and solved.

## Project layout

```bash
Leetcode-Solver/
├── solver.py        # Main script (CLI, LeetCode API, AI calls, submit)
├── find_models.py   # List available models from AI providers
├── requirements.txt # requests, google-genai, python-dotenv
├── .env.example     # Template for LEETCODE_* and API keys
├── .env             # Your secrets (create from .env.example, do not commit)
├── flake.nix        # Nix flake (if used)
└── README.md        # This file
```

## Listing available models

Use `find_models.py` to see which models are available from each provider:

```bash
python3 find_models.py              # list all providers
python3 find_models.py --gemini     # Gemini only
python3 find_models.py --hf         # HuggingFace only
python3 find_models.py --nvidia     # Nvidia NIM only
python3 find_models.py --groq       # Groq only
python3 find_models.py --gemini --groq  # multiple providers
```

## Troubleshooting

- **“CSRF verification failed”** – Set `LEETCODE_CSRF` in `.env` to your current `csrftoken` cookie from leetcode.com.
- **“LEETCODE_SESSION is not set”** – Add your session cookie to `.env` (see “Getting LeetCode cookies” above).
- **“GEMINI_API_KEY is not set”** – Required for `--model gemini`. Set it in `.env` or use `--model hf` / `--model nvidia` with the right key.
- **AttributeError about `genai.configure`** – Use the `google-genai` package (see `requirements.txt`), not `google-generativeai`. Run `pip install -r requirements.txt`.
- **No problems in range** – Check that the range [LO, HI] has non-premium problems and that you haven’t already accepted all of them. Use `--diff easy` (or medium/hard) to restrict by difficulty.

## License

Use and modify as you like. Submitting to LeetCode is subject to LeetCode’s terms of service and rate limits.
