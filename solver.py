#!/usr/bin/env python3
"""
LeetCode solver bot: fetches problems, generates solutions with AI, and submits.

Modes (use one): --daily (today's challenge), --chall N (single problem by number),
  --range LO [HI] (LO to last challenge if HI omitted, else [LO, HI]).
Options: --diff easy|medium|hard (with --range), --lang (e.g. python3, cpp, mysql, pandas),
  --model gemini|hf|nvidia|groq. Premium and already-AC problems are skipped.
  Challenges that don't support the chosen language are skipped; commented starter code is used when present.
"""
import os
import re
import sys
import time
import json
import argparse
import requests
import datetime
from dotenv import load_dotenv
from google import genai

load_dotenv()

# --- CONFIG ---
LEETCODE_SESSION = os.environ.get("LEETCODE_SESSION")
LEETCODE_CSRF = os.environ.get("LEETCODE_CSRF")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
HUGGINGFACE_API_KEY = os.environ.get("HUGGINGFACE_API_KEY")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BASE_URL = "https://leetcode.com"
GRAPHQL_URL = f"{BASE_URL}/graphql"
MAX_RETRIES = 5
SUBMISSION_RETRIES = 2  # retry with judge feedback (runtime/compile/wrong answer)
SUBMISSION_POLL_INTERVAL = 2

GEMINI_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.7-flash"
]

# Hugging Face Inference API (chat completions)
HF_MODELS = [
    "Qwen/Qwen2.5-Coder-32B-Instruct",
    "Qwen/Qwen2.5-Coder-7B-Instruct",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "codellama/CodeLlama-34b-Instruct-hf",
]

# Nvidia NIM (integrate.api.nvidia.com) – code-capable models
NVIDIA_MODELS = [
    "meta/llama-3.1-70b-instruct",
    "meta/llama-3.1-8b-instruct",
    "z-ai/glm5",
    "z-ai/glm4.7",
    "nvidia/llama-nemotron-embed-vl-1-v2",
    "moonshotai/kimi-k2.5",
    "minimaxai/minimax-m2.1",
    "deepseek-ai/deepseek-v3.2",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
]

# GROQ (api.groq.com) – OpenAI-compatible chat completions
GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "moonshotai/kimi-k2-instruct-0905",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

# LeetCode language slugs (--lang values)
LANG_SLUGS = frozenset(
    {
        "python3",
        "python",
        "c",
        "cpp",
        "csharp",
        "java",
        "javascript",
        "typescript",
        "go",
        "rust",
        "ruby",
        "swift",
        "kotlin",
        "scala",
        "php",
        "r",
        "erlang",
        "elixir",
        # SQL / database (LeetCode uses pythondata, oraclesql in API)
        "pandas",
        "pythondata",
        "mysql",
        "mssql",
        "oracle",
        "oraclesql",
        "postgresql",
    }
)

# User-facing --lang to LeetCode API langSlug (for submit/template lookup)
LEETCODE_LANG_ALIASES = {"pandas": "pythondata", "oracle": "oraclesql"}


def normalize_leetcode_lang(lang_slug: str) -> str:
    """Return the langSlug LeetCode API expects (e.g. pandas -> pythondata, oracle -> oraclesql)."""
    return LEETCODE_LANG_ALIASES.get(lang_slug.strip().lower(), lang_slug.strip())

DAILY_QUERY = """
query dailyChallenge {
  activeDailyCodingChallengeQuestion {
    date
    link
    question {
      questionId
      questionFrontendId
      title
      titleSlug
      content
      difficulty
      exampleTestcases
      codeSnippets {
        lang
        langSlug
        code
      }
      metaData
    }
  }
}
"""

PROBLEMSET_QUERY = """
query problemsetQuestionList($categorySlug: String, $limit: Int!, $skip: Int!, $filters: QuestionListFilterInput) {
  problemsetQuestionList: questionList(
    categorySlug: $categorySlug
    limit: $limit
    skip: $skip
    filters: $filters
  ) {
    total: totalNum
    questions: data {
      frontendQuestionId: questionFrontendId
      titleSlug
      paidOnly: isPaidOnly
      status
      title
      difficulty
    }
  }
}
"""

QUESTION_BY_SLUG_QUERY = """
query questionBySlug($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionId
    questionFrontendId
    title
    titleSlug
    content
    difficulty
    exampleTestcases
    codeSnippets {
      lang
      langSlug
      code
    }
    metaData
    isPaidOnly
  }
}
"""


def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()


def get_csrf() -> str:
    """Get CSRF token: from env or by fetching leetcode.com with session cookie."""
    if LEETCODE_CSRF:
        return LEETCODE_CSRF
    if not LEETCODE_SESSION:
        return ""
    try:
        r = requests.get(
            f"{BASE_URL}/problems/",
            cookies={"LEETCODE_SESSION": LEETCODE_SESSION},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        for c in r.cookies:
            if c.name == "csrftoken":
                return c.value or ""
    except Exception as e:
        log(f"Could not fetch CSRF: {e}")
    return ""


def get_csrf_for_problem(title_slug: str) -> str:
    """Fetch the problem page to get a CSRF token valid for submit (same-origin)."""
    if LEETCODE_CSRF:
        return LEETCODE_CSRF
    if not LEETCODE_SESSION:
        return ""
    try:
        session = requests.Session()
        session.cookies.set(
            "LEETCODE_SESSION", LEETCODE_SESSION, domain=".leetcode.com"
        )
        session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0"
        )
        session.headers["Referer"] = f"{BASE_URL}/"
        r = session.get(f"{BASE_URL}/problems/{title_slug}/", timeout=10)
        # Token from Set-Cookie (session now holds it)
        token = session.cookies.get("csrftoken")
        if token:
            return token
        # Else from response body
        match = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', r.text)
        if match:
            return match.group(1)
        match = re.search(r"csrftoken=([^;'\s]+)", r.text)
        if match:
            return match.group(1)
    except Exception as e:
        log(f"Could not fetch CSRF for problem: {e}")
    return get_csrf()


def graphql(
    query: str, variables: dict | None = None, operation_name: str | None = None
) -> dict:
    """Send GraphQL request with auth."""
    csrf = get_csrf()
    cookies = {"LEETCODE_SESSION": LEETCODE_SESSION} if LEETCODE_SESSION else {}
    if csrf:
        cookies["csrftoken"] = csrf
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0",
        "Referer": f"{BASE_URL}/",
    }
    if csrf:
        headers["x-csrftoken"] = csrf
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    if operation_name:
        payload["operationName"] = operation_name
    r = requests.post(
        GRAPHQL_URL, json=payload, cookies=cookies, headers=headers, timeout=15
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data and data["errors"]:
        raise RuntimeError(data["errors"])
    return data


def get_daily_problem() -> dict | None:
    """Fetch today's daily challenge with full question and code snippets."""
    log("Fetching daily challenge...")
    data = graphql(DAILY_QUERY)
    try:
        block = data["data"]["activeDailyCodingChallengeQuestion"]
        if not block:
            log("No active daily challenge.")
            return None
        return block
    except KeyError:
        log("Unexpected response: no activeDailyCodingChallengeQuestion")
        return None


def get_problemset_page(skip: int, limit: int = 500) -> tuple[list[dict], int]:
    """Fetch a page of problems. Returns (questions, total)."""
    data = graphql(
        PROBLEMSET_QUERY,
        variables={"categorySlug": "", "skip": skip, "limit": limit, "filters": {}},
        operation_name="problemsetQuestionList",
    )
    pl = data["data"]["problemsetQuestionList"]
    return pl.get("questions") or [], pl.get("total") or 0


def get_max_frontend_id() -> int:
    """Fetch problemset pages and return the maximum frontend question ID."""
    max_id = 0
    skip = 0
    limit = 500
    while True:
        questions, total = get_problemset_page(skip, limit)
        if not questions:
            break
        for q in questions:
            try:
                fid = int(q.get("frontendQuestionId") or 0)
                max_id = max(max_id, fid)
            except (TypeError, ValueError):
                continue
        skip += len(questions)
        if skip >= total:
            break
        time.sleep(0.3)
    return max_id


def get_problems_in_range(
    lo: int,
    hi: int,
    skip_solved: bool = True,
    difficulty: str | None = None,
) -> list[dict]:
    """Fetch all problems with frontend id in [lo, hi], excluding premium. Optionally skip already AC and filter by difficulty (Easy/Medium/Hard)."""
    diff_norm = difficulty.strip().lower() if difficulty else None
    if diff_norm and diff_norm not in ("easy", "medium", "hard"):
        diff_norm = None
    out = []
    skip = 0
    limit = 500
    total = None
    while True:
        questions, total = get_problemset_page(skip, limit)
        if not questions:
            break
        for q in questions:
            try:
                fid = int(q.get("frontendQuestionId") or 0)
            except (TypeError, ValueError):
                continue
            if fid < lo or fid > hi:
                continue
            if q.get("paidOnly"):
                continue
            if skip_solved and (q.get("status") or "").upper() == "AC":
                continue
            if diff_norm:
                q_diff = (q.get("difficulty") or "").strip().lower()
                if q_diff != diff_norm:
                    continue
            out.append(
                {
                    "frontendQuestionId": fid,
                    "titleSlug": q.get("titleSlug") or "",
                    "title": q.get("title") or "",
                    "difficulty": q.get("difficulty") or "",
                }
            )
        skip += len(questions)
        if skip >= total:
            break
        time.sleep(0.3)
    out.sort(key=lambda x: x["frontendQuestionId"])
    return out


def get_question_by_slug(title_slug: str) -> dict | None:
    """Fetch full question by titleSlug. Returns block in same shape as daily (question wrapped)."""
    data = graphql(
        QUESTION_BY_SLUG_QUERY,
        variables={"titleSlug": title_slug},
        operation_name="questionBySlug",
    )
    try:
        q = data["data"]["question"]
        if not q:
            return None
        return {"question": q}
    except KeyError:
        return None


def get_available_languages(question: dict) -> set[str]:
    """Return set of language slugs supported by this challenge (from codeSnippets)."""
    snippets = (question.get("question") or {}).get("codeSnippets") or []
    return {(s.get("langSlug") or "").strip().lower() for s in snippets if s.get("langSlug")}


def check_lang_supported(question: dict, lang_slug: str) -> bool:
    """Return True if this challenge supports the requested language."""
    available = get_available_languages(question)
    lang_lower = lang_slug.strip().lower()
    leet_slug = normalize_leetcode_lang(lang_slug)
    if lang_lower in available or leet_slug in available:
        return True
    # Treat python3/python as interchangeable
    if lang_lower in ("python3", "python"):
        return "python3" in available or "python" in available
    return False


def get_first_available_lang(question: dict) -> str | None:
    """Return the first language slug offered by this challenge (from codeSnippets order), or None."""
    snippets = (question.get("question") or {}).get("codeSnippets") or []
    for s in snippets:
        slug = (s.get("langSlug") or "").strip()
        if slug:
            return slug.lower()
    return None


def extract_commented_code(template: str) -> str:
    """Extract commented-out code from starter template for the AI to use if useful.
    Returns non-empty string only if there are comment lines that look like code."""
    if not (template or template.strip()):
        return ""
    lines = template.splitlines()
    out = []
    for line in lines:
        s = line.strip()
        # Single-line: // ... or # ... (but skip pure # or // with no content)
        if s.startswith("//") and len(s) > 2 and s[2:3].strip():
            out.append(line)
            continue
        if s.startswith("#") and len(s) > 1 and s[1:2].strip() and not s.startswith("#!"):
            out.append(line)
            continue
        # Block comment line
        if s.startswith("*") and s.endswith("*/"):
            out.append(line)
            continue
        if s.startswith("/*"):
            out.append(line)
            continue
        if "*/" in s and not s.startswith("*/"):
            out.append(line)
            continue
    if not out:
        return ""
    return "\n".join(out).strip()


def get_code_template(question: dict, lang_slug: str) -> str | None:
    """Get the starter code for the given language from question codeSnippets."""
    leet_slug = normalize_leetcode_lang(lang_slug)
    snippets = (question.get("question") or {}).get("codeSnippets") or []
    for s in snippets:
        if (s.get("langSlug") or "").lower() == leet_slug.lower():
            return s.get("code") or ""
    return None


def _code_looks_like_lang(code: str, lang_slug: str) -> bool:
    """Heuristic: code content matches requested language (avoid accepting wrong lang)."""
    code_lower = code.strip().lower()
    if lang_slug in ("python3", "python"):
        return "def " in code_lower or "class " in code_lower
    if lang_slug in ("pandas", "pythondata"):
        return (
            "import pandas" in code_lower
            or "pd." in code_lower
            or ("def " in code_lower and "dataframe" in code_lower)
        )
    if lang_slug in ("javascript", "typescript"):
        return "function " in code_lower or "=>" in code_lower or "const " in code_lower
    if lang_slug == "cpp":
        return "class " in code_lower or "int " in code_lower or "void " in code_lower
    if lang_slug == "java":
        return "public " in code_lower or "class " in code_lower
    if lang_slug == "go":
        return "func " in code_lower
    if lang_slug == "rust":
        return "fn " in code_lower or "impl " in code_lower
    # SQL-like languages
    if lang_slug in ("mysql", "mssql", "oracle", "oraclesql", "postgresql"):
        return (
            "select " in code_lower
            or " from " in code_lower
            or " where " in code_lower
            or "insert " in code_lower
            or "update " in code_lower
            or "delete " in code_lower
            or "create " in code_lower
        )
    return True  # unknown lang, accept


def extract_code(text: str, lang_slug: str) -> str | None:
    """Extract code from AI response (markdown code blocks or raw). Only returns code that matches the requested language."""
    lang_aliases = {
        "python3": "python",
        "cpp": "cpp",
        "csharp": "csharp",
        "javascript": "javascript",
        "typescript": "typescript",
        "pandas": "python",
        "pythondata": "python",
        "mysql": "sql",
        "mssql": "sql",
        "oracle": "sql",
        "oraclesql": "sql",
        "postgresql": "sql",
    }
    # Prefer fenced block with matching language tag
    for name in [lang_slug, lang_aliases.get(lang_slug, "")]:
        if not name:
            continue
        m = re.search(rf"```(?:{re.escape(name)})?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            code = m.group(1).strip()
            if _code_looks_like_lang(code, lang_slug):
                return code
    # Fallback: any unnamed block only if content looks like requested language
    m = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if m:
        code = m.group(1).strip()
        if _code_looks_like_lang(code, lang_slug):
            return code
    # No block: use whole text only if it looks like code in the right language
    has_expected_structure = (
        "class Solution" in text
        or "def " in text
        or "function " in text
        or "public " in text
        or (lang_slug in ("mysql", "mssql", "oracle", "oraclesql", "postgresql") and "select " in text.lower())
        or (lang_slug in ("pandas", "pythondata") and ("import pandas" in text.lower() or "def " in text))
    )
    if _code_looks_like_lang(text, lang_slug) and has_expected_structure:
        return text.strip()
    return None


def submit_solution(
    title_slug: str, question_id: str, lang_slug: str, typed_code: str
) -> dict:
    """Submit code via REST. Returns JSON with submission_id or error."""
    # Use CSRF obtained from the problem page so submit passes verification
    csrf = get_csrf_for_problem(title_slug)
    if not csrf:
        return {
            "error": "Could not obtain CSRF token. Set LEETCODE_CSRF in .env from browser cookies."
        }
    cookies = {
        "LEETCODE_SESSION": LEETCODE_SESSION,
        "csrftoken": csrf,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0",
        "Referer": f"{BASE_URL}/problems/{title_slug}/",
        "Origin": BASE_URL,
        "x-csrftoken": csrf,
    }
    url = f"{BASE_URL}/problems/{title_slug}/submit/"
    body = {
        "lang": normalize_leetcode_lang(lang_slug),
        "question_id": str(question_id),
        "typed_code": typed_code,
    }
    r = requests.post(url, json=body, cookies=cookies, headers=headers, timeout=15)
    try:
        return r.json()
    except Exception:
        return {"error": r.text or str(r.status_code)}


def poll_submission(submission_id: str, title_slug: str) -> dict:
    """Poll submission status until not PENDING. Returns check response."""
    csrf = get_csrf()
    cookies = {"LEETCODE_SESSION": LEETCODE_SESSION}
    if csrf:
        cookies["csrftoken"] = csrf
    headers = {
        "Referer": f"{BASE_URL}/problems/{title_slug}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0",
    }
    if csrf:
        headers["x-csrftoken"] = csrf
    url = f"{BASE_URL}/submissions/detail/{submission_id}/check/"
    while True:
        r = requests.get(url, cookies=cookies, headers=headers, timeout=15)
        try:
            data = r.json()
        except Exception:
            log(f"Poll response not JSON: {r.text[:200]}")
            time.sleep(SUBMISSION_POLL_INTERVAL)
            continue
        state = data.get("state") or data.get("status")
        if state and state.upper() != "PENDING" and state.upper() != "STARTED":
            return data
        time.sleep(SUBMISSION_POLL_INTERVAL)


# --- AI PROVIDERS ---
def _history_to_messages(history: list, prompt: str) -> list[dict]:
    """Convert Gemini-style history + new prompt to OpenAI-style messages for HF/Nvidia."""
    messages = []
    for content in history:
        role = "user" if content.role == "user" else "assistant"
        if getattr(content, "parts", None):
            text = content.parts[0].text if content.parts else ""
        else:
            text = getattr(content, "text", str(content))
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": prompt})
    return messages


def send_message_gemini(history: list, prompt: str) -> tuple[str, list]:
    client = genai.Client(api_key=GEMINI_API_KEY)
    for model_name in GEMINI_MODELS:
        try:
            chat = client.chats.create(model=model_name)
            response = chat.send_message(prompt)
            if hasattr(response, "text") and isinstance(getattr(response, "text"), str):
                text = response.text
            elif hasattr(response, "text") and callable(response.text):
                text = response.text()
            elif getattr(response, "candidates", None) and response.candidates:
                part = (
                    response.candidates[0].content.parts[0]
                    if response.candidates[0].content.parts
                    else None
                )
                text = part.text if part else ""
            else:
                text = str(response)
            return text, history
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                log(f"Quota exceeded on {model_name}, trying next...")
                continue
            log(f"Error on {model_name}: {e}")
            raise
    raise RuntimeError("All Gemini models failed (quota or error)")


def query_huggingface(history: list, prompt: str) -> str | None:
    if not HUGGINGFACE_API_KEY:
        return None
    messages = _history_to_messages(history, prompt)
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
    for model_id in HF_MODELS:
        log(f"Trying HF: {model_id}")
        url = f"https://api-inference.huggingface.co/models/{model_id}/v1/chat/completions"
        payload = {
            "model": model_id,
            "messages": messages,
            "max_tokens": 8192,
            "temperature": 0.1,
            "stream": False,
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code in (404, 410):
                continue
            if r.status_code == 429:
                log(f"HF rate limited: {model_id}")
                continue
            if r.status_code == 503:
                time.sleep(20)
                r = requests.post(url, headers=headers, json=payload, timeout=60)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            log(f"HF {model_id}: {e}")
            continue
    return None


def query_nvidia(history: list, prompt: str) -> str | None:
    if not NVIDIA_API_KEY:
        return None
    messages = _history_to_messages(history, prompt)
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    for model_id in NVIDIA_MODELS:
        log(f"Trying Nvidia: {model_id}")
        payload = {
            "model": model_id,
            "messages": messages,
            "max_tokens": 8192,
            "temperature": 0.1,
            "stream": False,
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=120)
            if r.status_code == 402:
                log(f"Nvidia payment required for {model_id}")
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            log(f"Nvidia {model_id}: {e}")
            continue
    return None


def query_groq(history: list, prompt: str) -> str | None:
    if not GROQ_API_KEY:
        return None
    messages = _history_to_messages(history, prompt)
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    for model_id in GROQ_MODELS:
        log(f"Trying GROQ: {model_id}")
        payload = {
            "model": model_id,
            "messages": messages,
            "max_completion_tokens": 8192,
            "temperature": 0.1,
            "stream": False,
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=120)
            if r.status_code == 429:
                log(f"GROQ rate limited: {model_id}")
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            log(f"GROQ {model_id}: {e}")
            continue
    return None


def send_message(history: list, prompt: str, model_choice: str) -> tuple[str, list]:
    """Dispatch to the selected provider. If all models for that provider fail, try the other providers in order."""
    # Fallback order: try chosen provider first, then the others
    order = ["gemini", "hf", "nvidia", "groq"]
    try:
        idx = order.index(model_choice)
    except ValueError:
        idx = 0
    providers_to_try = [order[idx]] + [p for p in order if p != order[idx]]

    last_error = None
    for provider in providers_to_try:
        if provider == "gemini":
            try:
                return send_message_gemini(history, prompt)
            except RuntimeError as e:
                last_error = e
                log(f"All Gemini models failed. Falling back to next provider...")
                continue
        if provider == "hf":
            text = query_huggingface(history, prompt)
            if text:
                return text, history
            log(f"All Hugging Face models failed. Falling back to next provider...")
            continue
        if provider == "nvidia":
            text = query_nvidia(history, prompt)
            if text:
                return text, history
            log(f"All Nvidia models failed. Falling back to next provider...")
            continue
        if provider == "groq":
            text = query_groq(history, prompt)
            if text:
                return text, history
            log(f"All GROQ models failed. Falling back to next provider...")
            continue
    raise last_error or RuntimeError(
        "All AI providers (Gemini, HF, Nvidia, GROQ) failed."
    )


def solve_with_ai(
    question: dict,
    lang_slug: str,
    model_choice: str = "gemini",
    submission_feedback: str | None = None,
) -> str | None:
    """Use the selected AI provider to generate solution code. Returns typed_code or None.
    If submission_feedback is provided (from a previous failed run), the AI is asked to fix the code."""
    q = question.get("question") or {}
    title = q.get("title") or "Unknown"
    title_slug = q.get("titleSlug") or ""
    content = q.get("content") or ""
    difficulty = q.get("difficulty") or ""
    example_testcases = (q.get("exampleTestcases") or "").strip()
    meta_data = q.get("metaData") or ""

    template = get_code_template(question, lang_slug)
    template_note = (
        f"\n\nStarter code for {lang_slug}:\n```\n{template}\n```" if template else ""
    )
    commented_code = extract_commented_code(template or "")
    commented_note = (
        f"\n\nCommented code in the editor (use or adapt if useful):\n```\n{commented_code}\n```"
        if commented_code
        else ""
    )

    lang_requirement = (
        "python3"
        if lang_slug in ("python3", "python")
        else lang_slug
    )
    feedback_block = ""
    if submission_feedback:
        feedback_block = f"""Your previous submission failed. Fix the code and return the corrected solution.

Judge feedback:
{submission_feedback}

"""
    prompt = f"""You are an expert competitive programmer. Solve this LeetCode problem and return only the code.
{feedback_block}Problem: {title} ({difficulty})
Link: https://leetcode.com/problems/{title_slug}/

Description:
{content}
{template_note}
{commented_note}

Example test cases (input/expected):
{example_testcases}

Metadata (signature): {meta_data}

Instructions:
1. You MUST write the solution in {lang_requirement} only. The code block must be labeled for this language (e.g. ```{lang_requirement}).
2. Return the complete solution code that can be submitted to LeetCode (same signature as the starter if given).
3. Do not include any explanation—only the code inside a single markdown code block.
4. Use the exact class/function name required by the problem (usually Solution with the given method).
"""

    history = []
    for attempt in range(MAX_RETRIES):
        log(f"Attempt {attempt + 1}/{MAX_RETRIES} ({model_choice})...")
        try:
            response_text, history = send_message(history, prompt, model_choice)
            code = extract_code(response_text, lang_slug)
            if code:
                return code
            prompt = f"Your previous response did not contain valid {lang_requirement} code. Return only the complete solution in {lang_requirement} inside a single ```{lang_requirement} code block, no explanation."
        except Exception as e:
            log(f"AI error: {e}")
            if attempt == MAX_RETRIES - 1:
                return None
            time.sleep(2)
    return None


def _solve_and_submit_one(block: dict, lang_slug: str, model_choice: str) -> bool:
    """Solve and submit one problem. Returns True if accepted. Retries with judge feedback on failure."""
    question = block.get("question") or {}
    question_id = question.get("questionId")
    title_slug = question.get("titleSlug")
    title = question.get("title") or "?"
    if not title_slug or not question_id:
        log("Missing questionId or titleSlug.")
        return False
    if not check_lang_supported(block, lang_slug):
        fallback = get_first_available_lang(block)
        if not fallback:
            log("Skip: challenge has no supported languages.")
            return False
        log(
            f"Challenge does not support --lang '{lang_slug}', using first available: '{fallback}'."
        )
        lang_slug = fallback
    log(f"Solving: {title} (#{question.get('questionFrontendId', '?')}) [lang={lang_slug}]")
    feedback: str | None = None
    for submit_attempt in range(SUBMISSION_RETRIES):
        if submit_attempt > 0:
            log(f"Retry {submit_attempt}/{SUBMISSION_RETRIES - 1} with judge feedback...")
        typed_code = solve_with_ai(
            block, lang_slug, model_choice=model_choice, submission_feedback=feedback
        )
        if not typed_code:
            log("Failed to generate solution.")
            return False
        log("Submitting...")
        result = submit_solution(title_slug, question_id, lang_slug, typed_code)
        if "error" in result:
            log(f"Submit error: {result.get('error', result)[:200]}")
            return False
        submission_id = result.get("submission_id") or result.get("submissionId")
        if not submission_id:
            log(f"Submit response: {result}")
            return False
        check = poll_submission(str(submission_id), title_slug)
        status = (check.get("status_msg") or check.get("state") or "").upper()
        if status == "ACCEPTED":
            log("Accepted!")
            return True
        log(f"Result: {status}")
        # Build feedback for next attempt
        parts = [f"Status: {status}"]
        if check.get("full_runtime_error"):
            err = check["full_runtime_error"]
            log(f"Runtime error: {err[:300]}")
            parts.append(f"Runtime error:\n{err}")
        if check.get("full_compile_error"):
            err = check["full_compile_error"]
            log(f"Compile error: {err[:300]}")
            parts.append(f"Compile error:\n{err}")
        if check.get("wrong_question_id") or check.get("expected_code_output"):
            parts.append(str(check))
        feedback = "\n".join(parts)
        if submit_attempt >= SUBMISSION_RETRIES - 1:
            return False
        time.sleep(1)
    return False


def _ensure_model_and_session(model_choice: str) -> None:
    if not LEETCODE_SESSION:
        log("Error: LEETCODE_SESSION is not set. Set it in .env or environment.")
        sys.exit(1)
    if model_choice == "gemini" and not GEMINI_API_KEY:
        log("Error: GEMINI_API_KEY is not set (required for --model gemini).")
        sys.exit(1)
    if model_choice == "hf" and not HUGGINGFACE_API_KEY:
        log("Error: HUGGINGFACE_API_KEY is not set (required for --model hf).")
        sys.exit(1)
    if model_choice == "nvidia" and not NVIDIA_API_KEY:
        log("Error: NVIDIA_API_KEY is not set (required for --model nvidia).")
        sys.exit(1)
    if model_choice == "groq" and not GROQ_API_KEY:
        log("Error: GROQ_API_KEY is not set (required for --model groq).")
        sys.exit(1)
    if model_choice == "gemini":
        pass  # client created in send_message_gemini


def run_daily(lang_slug: str, model_choice: str) -> None:
    """Fetch daily problem, generate solution, submit, and report."""
    _ensure_model_and_session(model_choice)
    if lang_slug not in LANG_SLUGS:
        log(
            f"Warning: unknown --lang '{lang_slug}'. Using as-is. Known: {sorted(LANG_SLUGS)}"
        )
    block = get_daily_problem()
    if not block:
        sys.exit(1)
    question = block.get("question") or {}
    if not question.get("titleSlug") or not question.get("questionId"):
        log("Daily challenge missing questionId or titleSlug.")
        sys.exit(1)
    ok = _solve_and_submit_one(block, lang_slug, model_choice)
    sys.exit(0 if ok else 1)


def run_chall(num: int, lang_slug: str, model_choice: str) -> None:
    """Solve a single problem by its frontend challenge number (e.g. 2024). Skips premium."""
    _ensure_model_and_session(model_choice)
    log(f"Resolving challenge #{num}...")
    skip = 0
    limit = 500
    title_slug = None
    is_premium = False
    while True:
        questions, total = get_problemset_page(skip, limit)
        for q in questions:
            try:
                fid = int(q.get("frontendQuestionId") or 0)
            except (TypeError, ValueError):
                continue
            if fid == num:
                if q.get("paidOnly"):
                    log(f"Challenge #{num} is premium. Skipping.")
                    sys.exit(0)
                title_slug = q.get("titleSlug") or ""
                break
        if title_slug is not None:
            break
        skip += len(questions)
        if skip >= total:
            log(f"No challenge with number {num} found (or premium).")
            sys.exit(1)
        time.sleep(0.3)
    block = get_question_by_slug(title_slug)
    if not block:
        log(f"Could not load question {title_slug}.")
        sys.exit(1)
    ok = _solve_and_submit_one(block, lang_slug, model_choice)
    sys.exit(0 if ok else 1)


def run_range(
    lo: int,
    hi: int,
    lang_slug: str,
    model_choice: str,
    difficulty: str | None = None,
) -> None:
    """Solve all non-premium, not-yet-solved problems with frontend id in [lo, hi], optionally only given difficulty."""
    _ensure_model_and_session(model_choice)
    diff_msg = f", difficulty={difficulty}" if difficulty else ""
    log(
        f"Fetching problems in range [{lo}, {hi}] (excluding premium and already AC{diff_msg})..."
    )
    problems = get_problems_in_range(lo, hi, skip_solved=True, difficulty=difficulty)
    if not problems:
        log("No problems to solve in that range.")
        return
    log(f"Found {len(problems)} problem(s) to solve.")
    accepted = 0
    for i, p in enumerate(problems):
        fid = p["frontendQuestionId"]
        title_slug = p["titleSlug"]
        log(f"[{i + 1}/{len(problems)}] #{fid} {p.get('title', title_slug)}")
        block = get_question_by_slug(title_slug)
        if not block:
            log(f"  Skip: could not load question.")
            continue
        if block.get("question", {}).get("isPaidOnly"):
            log(f"  Skip: premium.")
            continue
        if _solve_and_submit_one(block, lang_slug, model_choice):
            accepted += 1
        time.sleep(1)
    log(f"Done. Accepted: {accepted}/{len(problems)}")


def main():
    parser = argparse.ArgumentParser(
        description="LeetCode solver bot: solve and submit with AI. Use one of --daily, --chall, or --range."
    )
    parser.add_argument(
        "--daily", action="store_true", help="Solve and submit the daily challenge"
    )
    parser.add_argument(
        "--chall",
        type=int,
        metavar="N",
        help="Solve a single problem by its number (e.g. 2024). Premium problems are skipped.",
    )
    parser.add_argument(
        "--range",
        type=int,
        nargs="+",
        metavar=("LO", "HI"),
        help="Solve problems in [LO, HI]. One value (e.g. --range 2000) means from that number to the last challenge; two values (e.g. --range 2000 3000) set the range explicitly.",
    )
    parser.add_argument(
        "--diff",
        type=str,
        choices=["easy", "medium", "hard"],
        metavar="DIFF",
        help="With --range: only solve problems of this difficulty (e.g. --range 2000 3000 --diff easy)",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default="python3",
        help="Submission language (e.g. python3, c, cpp, java)",
    )
    parser.add_argument(
        "--model",
        type=str,
        choices=["gemini", "hf", "nvidia", "groq"],
        default="gemini",
        help="AI provider: gemini, hf (Hugging Face), nvidia (NIM), or groq",
    )
    args = parser.parse_args()

    lang = args.lang.strip().lower()
    model = args.model

    if args.daily:
        run_daily(lang, model_choice=model)
        return
    if args.chall is not None:
        run_chall(args.chall, lang, model)
        return
    if args.range is not None:
        if len(args.range) == 1:
            lo = args.range[0]
            log("Fetching max problem ID from problemset...")
            hi = get_max_frontend_id()
            log(f"Range: from {lo} to last challenge (ID {hi}).")
        else:
            lo, hi = args.range[0], args.range[1]
        if lo > hi:
            lo, hi = hi, lo
        run_range(lo, hi, lang, model, difficulty=args.diff)
        return
    parser.print_help()
    log("Use --daily, --chall N, or --range LO [HI] to run.")


if __name__ == "__main__":
    main()
