import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH = os.getenv("DATABASE_PATH", "jobs.db")

# ── Scheduler ─────────────────────────────────────────────────────────────────
SCRAPE_INTERVAL_HOURS = int(os.getenv("SCRAPE_INTERVAL_HOURS", "2"))

# ── Scraping ──────────────────────────────────────────────────────────────────
HEADLESS           = os.getenv("HEADLESS", "true").lower() == "true"
MAX_PAGES          = int(os.getenv("MAX_PAGES", "3"))
REQUEST_DELAY      = float(os.getenv("REQUEST_DELAY", "2.5"))
MAX_RETRIES        = int(os.getenv("MAX_RETRIES", "3"))

# ── Location ──────────────────────────────────────────────────────────────────
TARGET_CITY = "Pune"
TARGET_LOCATIONS = [
    "pune", "pimpri", "chinchwad", "hinjewadi",
    "kharadi", "hadapsar", "wakad", "baner",
    "viman nagar", "magarpatta", "kalyani nagar",
]

# ── Experience limits ─────────────────────────────────────────────────────────
MAX_EXPERIENCE_YEARS = 2

# ── Target roles (substring match, case-insensitive) ──────────────────────────
TARGET_ROLES = [
    "software engineer",
    "software developer",
    "full stack developer",
    "full stack engineer",
    "frontend developer",
    "frontend engineer",
    "backend developer",
    "backend engineer",
    "graduate engineer trainee",
    "get",
    "associate engineer",
    "associate software engineer",
    "associate system engineer",
    "junior developer",
    "junior engineer",
    "junior software",
    "trainee engineer",
    "trainee software",
    "python developer",
    "java developer",
    "react developer",
    "angular developer",
    "node.js developer",
    "web developer",
    "qa engineer",
    "test engineer",
    "data analyst",
    "business analyst",
    "systems engineer",
]

# ── Excluded title keywords ───────────────────────────────────────────────────
EXCLUDED_TITLE_KEYWORDS = [
    "senior", "sr.", "sr ", "lead", "tech lead", "team lead",
    "manager", "director", "head of", "principal", "architect",
    "vp ", "vice president", "staff engineer", "engineering manager",
    "10+", "8+", "7+", "6+", "5+", "4+", "3+",
]

# ── Walk-in keywords ──────────────────────────────────────────────────────────
WALKIN_KEYWORDS = [
    "walk-in", "walk in", "walkin", "walk-in drive", "walk in drive",
    "direct interview", "spot selection", "spot offer",
    "no appointment", "open interview", "mass hiring",
    "mega drive", "hiring drive", "offline interview",
    "drop cv", "drop resume", "immediate joining",
    "face to face", "face-to-face",
]

# ── Naukri search queries ─────────────────────────────────────────────────────
NAUKRI_QUERIES = [
    "fresher software engineer",
    "graduate engineer trainee",
    "junior developer fresher",
    "associate software engineer",
    "entry level developer",
]

# ── Google search queries (walk-in focus) ─────────────────────────────────────
GOOGLE_QUERIES = [
    "walk-in drive Pune freshers software engineer 2024",
    "walk in interview Pune IT fresher this week",
    "Pune fresher jobs 0-2 years software developer",
    "hiring drive Pune graduate engineer trainee",
    "mega drive Pune fresher developer",
]
