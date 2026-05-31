"""
scraper.py  —  Production version
Uses Playwright with cookie-warming so sites don't block the bot.
Sources: Naukri, Indeed, LinkedIn, TimesJobs, Shine
"""

import asyncio
import json
import logging
import random
import re
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def _ua() -> str:
    return random.choice(USER_AGENTS)


def _normalize(
    title="", company="", location="", experience="",
    link="", description="", salary="", posted_date="", source=""
) -> dict:
    return {
        "title":       title.strip(),
        "company":     company.strip(),
        "location":    location.strip(),
        "experience":  experience.strip(),
        "link":        link.strip(),
        "description": description.strip()[:2000],
        "salary":      salary.strip(),
        "posted_date": posted_date.strip(),
        "source":      source,
        "is_walkin":   False,
    }


# ── Playwright browser factory ────────────────────────────────────────────────

async def _make_page(playwright):
    """Launch Chromium with stealth settings to avoid bot detection."""
    browser = await playwright.chromium.launch(
        headless=config.HEADLESS,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1280,900",
        ],
    )
    ctx = await browser.new_context(
        user_agent=_ua(),
        viewport={"width": 1280, "height": 900},
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        extra_http_headers={
            "Accept-Language": "en-IN,en;q=0.9",
        },
    )
    await ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        window.chrome = { runtime: {} };
    """)
    ctx.set_default_timeout(30_000)
    page = await ctx.new_page()
    return browser, ctx, page


async def _warm_cookies(page, url: str):
    """Visit homepage first to collect real cookies before hitting search URLs."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        await asyncio.sleep(random.uniform(2, 3))
        await page.evaluate("window.scrollTo(0, 300)")
        await asyncio.sleep(1)
    except Exception as e:
        logger.debug("Cookie warm failed for %s: %s", url, e)


# ══════════════════════════════════════════════════════════════════════════════
# NAUKRI
# ══════════════════════════════════════════════════════════════════════════════

NAUKRI_SEARCHES = [
    "fresher-software-engineer",
    "graduate-engineer-trainee",
    "junior-developer",
    "associate-software-engineer",
    "entry-level-developer",
]


async def scrape_naukri() -> list[dict]:
    jobs = []
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser, ctx, page = await _make_page(pw)
            try:
                await _warm_cookies(page, "https://www.naukri.com/")
                for query_slug in NAUKRI_SEARCHES:
                    for page_num in range(1, config.MAX_PAGES + 1):
                        url = (
                            f"https://www.naukri.com/{query_slug}-jobs-in-pune"
                            if page_num == 1
                            else f"https://www.naukri.com/{query_slug}-jobs-in-pune-{page_num}"
                        )
                        found = await _scrape_naukri_page(page, url, query_slug, page_num)
                        jobs.extend(found)
                        if len(found) < 5:
                            break
                        await asyncio.sleep(random.uniform(2, 3.5))
            finally:
                await ctx.close()
                await browser.close()
    except ImportError:
        logger.warning("Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
    except Exception as e:
        logger.error("Naukri scraper failed: %s", e, exc_info=True)
    logger.info("Naukri total: %d", len(jobs))
    return jobs


async def _scrape_naukri_page(page, url, query, page_num) -> list[dict]:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        await asyncio.sleep(random.uniform(1.5, 2.5))

        # Try internal API via fetch() — uses browser cookies so it works
        api_jobs = await _naukri_api_via_browser(page, query.replace("-", " "), page_num)
        if api_jobs:
            logger.info("Naukri API [%s p%d]: %d jobs", query, page_num, len(api_jobs))
            return api_jobs

        # Fallback: parse rendered HTML
        try:
            await page.wait_for_selector(
                "article.jobTuple, [class*='jobTuple'], [class*='srp-jobtuple']",
                timeout=8_000,
            )
        except Exception:
            logger.debug("Naukri: no cards at %s", url)
            return []

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await asyncio.sleep(1)
        html = await page.content()
        jobs = _parse_naukri_html(html)
        logger.info("Naukri HTML [%s p%d]: %d jobs", query, page_num, len(jobs))
        return jobs
    except Exception as e:
        logger.error("Naukri page error [%s p%d]: %s", query, page_num, e)
        return []


async def _naukri_api_via_browser(page, keyword, page_num) -> list[dict]:
    """Call Naukri JSON API using the browser's cookie jar (avoids 403)."""
    jobs = []
    try:
        result = await page.evaluate("""
            async (keyword, pageNum) => {
                const params = new URLSearchParams({
                    noOfResults: 20, urlType: 'search_by_key_loc',
                    searchType: 'adv', keyword: keyword,
                    location: 'pune', experience: 0, experienceDD: 2,
                    jobAge: 7, start: (pageNum-1)*20, pageNo: pageNum,
                });
                try {
                    const r = await fetch('https://www.naukri.com/jobapi/v3/search?' + params, {
                        headers: { Accept: 'application/json', appid: '109', systemid: 'Naukri' },
                        credentials: 'include',
                    });
                    if (!r.ok) return null;
                    return await r.json();
                } catch(e) { return null; }
            }
        """, keyword, page_num)

        if result and isinstance(result.get("jobDetails"), list):
            for jd in result["jobDetails"]:
                ph  = jd.get("placeholders", [])
                loc = ph[0].get("label", "Pune") if len(ph) > 0 else "Pune"
                exp = ph[1].get("label", "")    if len(ph) > 1 else ""
                sal = ph[2].get("label", "")    if len(ph) > 2 else ""
                jd_url = jd.get("jdURL", "")
                if jd_url and not jd_url.startswith("http"):
                    jd_url = "https://www.naukri.com" + jd_url
                jobs.append(_normalize(
                    title=jd.get("title", ""), company=jd.get("companyName", ""),
                    location=loc, experience=exp, salary=sal,
                    link=jd_url, posted_date=jd.get("footerPlaceholderLabel", ""),
                    source="naukri",
                ))
    except Exception as e:
        logger.debug("Naukri API via browser failed: %s", e)
    return jobs


def _parse_naukri_html(html) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jobs = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data  = json.loads(tag.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                j = _parse_jsonld(item, "naukri")
                if j: jobs.append(j)
        except Exception:
            pass
    if jobs:
        return jobs
    for card in soup.select("article.jobTuple, article[class*='jobTuple']"):
        try:
            te = card.select_one("a.title, a[class*='title'], h2 a")
            if not te: continue
            link = te.get("href", "")
            if link and not link.startswith("http"):
                link = "https://www.naukri.com" + link
            ce = card.select_one(".companyInfo a, [class*='companyName']")
            le = card.select_one(".locWdth, [class*='location']")
            ee = card.select_one("[class*='experience'], .expwdth")
            se = card.select_one("[class*='salary'], .salwdth")
            jobs.append(_normalize(
                title=te.get_text(strip=True),
                company=ce.get_text(strip=True) if ce else "",
                location=le.get_text(strip=True) if le else "Pune",
                experience=ee.get_text(strip=True) if ee else "",
                salary=se.get_text(strip=True) if se else "",
                link=link, source="naukri",
            ))
        except Exception:
            pass
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
# INDEED
# ══════════════════════════════════════════════════════════════════════════════

INDEED_QUERIES = [
    "fresher software engineer",
    "graduate engineer trainee",
    "junior developer fresher",
    "associate engineer",
    "walk-in fresher developer",
]


async def scrape_indeed() -> list[dict]:
    jobs = []
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser, ctx, page = await _make_page(pw)
            try:
                await _warm_cookies(page, "https://in.indeed.com/")
                for query in INDEED_QUERIES:
                    for page_num in range(config.MAX_PAGES):
                        params = {"q": query, "l": "Pune, Maharashtra",
                                  "radius": "25", "fromage": "7", "start": page_num * 10}
                        url   = "https://in.indeed.com/jobs?" + urlencode(params)
                        found = await _scrape_indeed_page(page, url, query, page_num + 1)
                        jobs.extend(found)
                        if len(found) < 5: break
                        await asyncio.sleep(random.uniform(2.5, 4))
            finally:
                await ctx.close()
                await browser.close()
    except ImportError:
        pass
    except Exception as e:
        logger.error("Indeed scraper failed: %s", e, exc_info=True)
    logger.info("Indeed total: %d", len(jobs))
    return jobs


async def _scrape_indeed_page(page, url, query, page_num) -> list[dict]:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        await asyncio.sleep(random.uniform(2, 3))
        for sel in ['button[id*="close"]', "button:has-text('Continue')"]:
            try:
                btn = await page.query_selector(sel)
                if btn: await btn.click()
            except Exception:
                pass
        try:
            await page.wait_for_selector(".job_seen_beacon, .tapItem", timeout=8_000)
        except Exception:
            return []
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data  = json.loads(tag.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    j = _parse_jsonld(item, "indeed")
                    if j: jobs.append(j)
            except Exception:
                pass
        if not jobs:
            for card in soup.select(".job_seen_beacon, .tapItem"):
                try:
                    te = card.select_one("h2.jobTitle a, [class*='jobTitle'] a")
                    if not te: continue
                    href = te.get("href", "")
                    link = "https://in.indeed.com" + href if href.startswith("/") else href
                    ce = card.select_one('[data-testid="company-name"], .companyName')
                    le = card.select_one('[data-testid="text-location"], .companyLocation')
                    jobs.append(_normalize(
                        title=te.get_text(strip=True),
                        company=ce.get_text(strip=True) if ce else "",
                        location=le.get_text(strip=True) if le else "Pune",
                        link=link, source="indeed",
                    ))
                except Exception:
                    pass
        logger.info("Indeed [%s p%d]: %d jobs", query, page_num, len(jobs))
        return jobs
    except Exception as e:
        logger.error("Indeed page error [%s p%d]: %s", query, page_num, e)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# LINKEDIN
# ══════════════════════════════════════════════════════════════════════════════

LINKEDIN_QUERIES = [
    "fresher software engineer",
    "graduate engineer trainee",
    "junior developer",
    "associate software engineer",
]


async def scrape_linkedin() -> list[dict]:
    jobs = []
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser, ctx, page = await _make_page(pw)
            try:
                await _warm_cookies(page, "https://www.linkedin.com/")
                for query in LINKEDIN_QUERIES:
                    params = {"keywords": query, "location": "Pune, Maharashtra, India",
                              "f_E": "2,1", "f_TPR": "r604800", "start": 0}
                    url   = "https://www.linkedin.com/jobs/search/?" + urlencode(params)
                    found = await _scrape_linkedin_page(page, url, query)
                    jobs.extend(found)
                    await asyncio.sleep(random.uniform(3, 5))
            finally:
                await ctx.close()
                await browser.close()
    except ImportError:
        pass
    except Exception as e:
        logger.error("LinkedIn scraper failed: %s", e, exc_info=True)
    logger.info("LinkedIn total: %d", len(jobs))
    return jobs


async def _scrape_linkedin_page(page, url, query) -> list[dict]:
    try:
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(2)
        for sel in [".modal__dismiss", '[aria-label="Dismiss"]']:
            try:
                btn = await page.query_selector(sel)
                if btn: await btn.click()
            except Exception:
                pass
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await asyncio.sleep(1)
        try:
            await page.wait_for_selector(".job-search-card, .base-card", timeout=8_000)
        except Exception:
            return []
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        for card in soup.select(".job-search-card, .base-card"):
            try:
                te = card.select_one(".base-search-card__title, .job-result-card__title")
                ce = card.select_one(".base-search-card__subtitle, .job-result-card__company-name")
                le = card.select_one(".job-search-card__location, .base-search-card__metadata")
                ae = card.select_one("a[href*='/jobs/view/']")
                de = card.select_one("time")
                if not te or not ae: continue
                jobs.append(_normalize(
                    title=te.get_text(strip=True),
                    company=ce.get_text(strip=True) if ce else "",
                    location=le.get_text(strip=True) if le else "Pune",
                    link=ae["href"].split("?")[0],
                    posted_date=de.get("datetime", "") if de else "",
                    source="linkedin",
                ))
            except Exception:
                pass
        logger.info("LinkedIn [%s]: %d jobs", query, len(jobs))
        return jobs
    except Exception as e:
        logger.error("LinkedIn page error [%s]: %s", query, e)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# TIMESJOBS
# ══════════════════════════════════════════════════════════════════════════════

TIMESJOBS_QUERIES = [
    "fresher software engineer",
    "junior developer",
    "graduate engineer trainee",
    "walk-in fresher pune",
]


async def scrape_timesjobs() -> list[dict]:
    jobs = []
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser, ctx, page = await _make_page(pw)
            try:
                await _warm_cookies(page, "https://www.timesjobs.com/")
                for query in TIMESJOBS_QUERIES:
                    params = {"txtKeywords": query, "txtLocation": "Pune",
                              "cboWorkExp1": "0", "cboWorkExp2": "2", "postWeek": "7"}
                    url = "https://www.timesjobs.com/candidate/job-search.html?" + urlencode(params)
                    found = await _scrape_timesjobs_page(page, url, query)
                    jobs.extend(found)
                    await asyncio.sleep(random.uniform(2, 3))
            finally:
                await ctx.close()
                await browser.close()
    except ImportError:
        pass
    except Exception as e:
        logger.error("TimesJobs scraper failed: %s", e, exc_info=True)
    logger.info("TimesJobs total: %d", len(jobs))
    return jobs


async def _scrape_timesjobs_page(page, url, query) -> list[dict]:
    jobs = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        await asyncio.sleep(2)
        try:
            await page.wait_for_selector("li.clearfix.job-bx, [class*='job-bx']", timeout=8_000)
        except Exception:
            return []
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select("li.clearfix.job-bx, li[class*='job-bx']"):
            try:
                te = card.select_one("h2 a, .job-title a, h3 a")
                if not te: continue
                ce = card.select_one(".joblist-comp-name, [class*='comp-name']")
                ee = card.select_one("[class*='exp'], .exp")
                le = card.select_one("[class*='location'], .location")
                de = card.select_one("[class*='date'], .sim-posted")
                jobs.append(_normalize(
                    title=te.get_text(strip=True),
                    company=ce.get_text(strip=True) if ce else "",
                    experience=ee.get_text(strip=True) if ee else "",
                    location=le.get_text(strip=True) if le else "Pune",
                    link=te.get("href", ""),
                    posted_date=de.get_text(strip=True) if de else "",
                    source="timesjobs",
                ))
            except Exception:
                pass
        logger.info("TimesJobs [%s]: %d jobs", query, len(jobs))
    except Exception as e:
        logger.error("TimesJobs page error: %s", e)
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
# SHINE
# ══════════════════════════════════════════════════════════════════════════════

async def scrape_shine() -> list[dict]:
    jobs = []
    queries = ["fresher software engineer", "junior developer", "graduate engineer trainee"]
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser, ctx, page = await _make_page(pw)
            try:
                await _warm_cookies(page, "https://www.shine.com/")
                for query in queries:
                    url = "https://www.shine.com/job-search/" + query.replace(" ", "-") + "-jobs-in-pune/"
                    found = await _scrape_shine_page(page, url, query)
                    jobs.extend(found)
                    await asyncio.sleep(random.uniform(2, 3))
            finally:
                await ctx.close()
                await browser.close()
    except ImportError:
        pass
    except Exception as e:
        logger.error("Shine scraper failed: %s", e, exc_info=True)
    logger.info("Shine total: %d", len(jobs))
    return jobs


async def _scrape_shine_page(page, url, query) -> list[dict]:
    jobs = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        await asyncio.sleep(2)
        try:
            await page.wait_for_selector(".job-listing, article.job-card, [class*='job-card']", timeout=8_000)
        except Exception:
            return []
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select(".job-listing, article.job-card, [class*='job-card']"):
            try:
                te = card.select_one("h3 a, h2 a, .job-title a")
                if not te: continue
                link = te.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://www.shine.com" + link
                ce = card.select_one(".company-name, [class*='company']")
                ee = card.select_one("[class*='exp'], .experience")
                le = card.select_one("[class*='location'], .location")
                jobs.append(_normalize(
                    title=te.get_text(strip=True),
                    company=ce.get_text(strip=True) if ce else "",
                    experience=ee.get_text(strip=True) if ee else "",
                    location=le.get_text(strip=True) if le else "Pune",
                    link=link, source="shine",
                ))
            except Exception:
                pass
        logger.info("Shine [%s]: %d jobs", query, len(jobs))
    except Exception as e:
        logger.error("Shine page error: %s", e)
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
# SHARED
# ══════════════════════════════════════════════════════════════════════════════

def _parse_jsonld(data, source) -> dict | None:
    if data.get("@type") != "JobPosting":
        return None
    try:
        loc  = data.get("jobLocation", {})
        if isinstance(loc, list): loc = loc[0] if loc else {}
        addr = loc.get("address", {})
        location = addr.get("addressLocality") or addr.get("addressRegion") or ""
        org  = data.get("hiringOrganization", {})
        company = org.get("name", "") if isinstance(org, dict) else str(org)
        exp = data.get("experienceRequirements", "")
        if isinstance(exp, dict): exp = f"{exp.get('monthsOfExperience','')} months"
        desc = re.sub(r"<[^>]+>", " ", data.get("description", "")).strip()
        return _normalize(
            title=data.get("title", ""), company=company, location=location,
            experience=str(exp), link=data.get("url") or data.get("sameAs", ""),
            description=desc, posted_date=data.get("datePosted", ""),
            salary=str(data.get("baseSalary", "")), source=source,
        )
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

async def scrape_all() -> list[dict]:
    logger.info("Starting all scrapers…")
    results = await asyncio.gather(
        scrape_naukri(),
        scrape_indeed(),
        scrape_linkedin(),
        scrape_timesjobs(),
        scrape_shine(),
        return_exceptions=True,
    )
    names    = ["naukri", "indeed", "linkedin", "timesjobs", "shine"]
    all_jobs: list[dict] = []
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            logger.error("Scraper '%s' raised: %s", name, result)
        else:
            all_jobs.extend(result)

    seen, unique = set(), []
    for j in all_jobs:
        key = re.sub(r"\?.*$", "", j.get("link", ""))
        if key and key not in seen:
            seen.add(key)
            unique.append(j)

    logger.info("All scrapers done — unique raw jobs: %d", len(unique))
    return unique


def scrape_all_sync() -> list[dict]:
    return asyncio.run(scrape_all())