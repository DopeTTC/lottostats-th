"""
LottoStat TH — Data Pipeline
1. Download historical base (ANTDPU CSV 2000–2024) from HuggingFace
2. Scrape recent draws from vicha-w GitHub archive (2007–present, updated regularly)
3. Scrape latest single draw from glo.or.th as fallback
4. Merge + deduplicate → calculate stats → write data/data.json
"""

import json
import re
import sys
from datetime import datetime, date
from io import StringIO
from pathlib import Path
from typing import Optional, List, Dict

import pandas as pd
import requests
from bs4 import BeautifulSoup

OUT_FILE = Path(__file__).parent.parent / "data" / "data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

# ── Sources ──────────────────────────────────────────────────────────────────

ANTDPU_CSV_URL = (
    "https://huggingface.co/datasets/ANTDPU/ThaiGovernmentLotteryResults"
    "/resolve/main/lotto.csv"
)

VICHA_API_URL = (
    "https://api.github.com/repos/vicha-w/thai-lotto-archive/contents/lottonumbers"
)

GLO_RESULT_URL = "https://www.glo.or.th/result/lottery-result.php"


# ── Fetch helpers ─────────────────────────────────────────────────────────────

def fetch_antdpu() -> List[dict]:
    """Download ANTDPU CSV → list of draw dicts."""
    print("Fetching ANTDPU historical CSV...")
    try:
        r = requests.get(ANTDPU_CSV_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text))
        print(f"  ANTDPU columns: {list(df.columns)}")
        print(f"  Sample row: {dict(df.iloc[0])}")

        draws = []
        for _, row in df.iterrows():
            d = _parse_antdpu_row(row)
            if d:
                draws.append(d)
        print(f"  ANTDPU: {len(draws)} draws parsed")
        return draws
    except Exception as e:
        print(f"  ANTDPU failed: {e}")
        return []


def _parse_antdpu_row(row) -> Optional[dict]:
    """Parse ANTDPU CSV row with columns: date(day), month, year, num."""
    try:
        day   = int(row["date"])
        month = int(row["month"])
        year  = int(row["year"])
        num   = str(row["num"]).strip().zfill(6) if pd.notna(row["num"]) else None
    except (KeyError, ValueError):
        return None

    # ANTDPU year might be Buddhist (2xxx+543) or Gregorian
    if year > 2500:
        year -= 543

    try:
        draw_date = date(year, month, day).isoformat()
    except ValueError:
        return None

    if not num:
        return None

    # num may be 6-digit 1st prize or 2-digit last prize
    if re.match(r"^\d{6}$", num):
        first_prize = num
        last3 = num[-3:]
        last2 = num[-2:]
    elif re.match(r"^\d{2}$", num):
        # Only last2 available
        first_prize = None
        last3 = None
        last2 = num
    else:
        return None

    return {
        "date": draw_date,
        "first_prize": first_prize,
        "last3": last3,
        "last2": last2,
    }


def fetch_vicha_archive() -> List[dict]:
    """Fetch vicha-w per-file archive — each file = one draw."""
    print("Fetching vicha-w GitHub archive (file list)...")
    try:
        r = requests.get(VICHA_API_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        files = r.json()

        draws = []
        for f in files:
            name = f.get("name", "")
            # Files named like YYYY-MM-DD.txt
            m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
            if not m:
                continue
            draw_date = m.group(1)
            raw_url = f.get("download_url")
            if not raw_url:
                continue
            try:
                fr = requests.get(raw_url, headers=HEADERS, timeout=10)
                fr.raise_for_status()
                d = _parse_vicha_file(draw_date, fr.text)
                if d:
                    draws.append(d)
            except Exception:
                continue

        print(f"  vicha-w: {len(draws)} draws parsed")
        return draws
    except Exception as e:
        print(f"  vicha-w failed: {e}")
        return []


def _parse_vicha_file(draw_date: str, content: str) -> Optional[dict]:
    """Parse a single vicha-w draw file.
    Format: FIRST XXXXXX / TWO XX / THREE_LAST XXX XXX / etc.
    """
    first_prize = None
    last2 = None
    for line in content.splitlines():
        line = line.strip()
        parts = line.split()
        if not parts:
            continue
        label = parts[0].upper()
        if label == "FIRST" and len(parts) >= 2:
            val = parts[1]
            if re.match(r"^\d{6}$", val):
                first_prize = val
        elif label == "TWO" and len(parts) >= 2:
            val = parts[1].zfill(2)
            if re.match(r"^\d{2}$", val):
                last2 = val

    if not first_prize:
        return None
    return {
        "date": draw_date,
        "first_prize": first_prize,
        "last3": first_prize[-3:],   # last 3 digits of 1st prize
        "last2": last2,              # independently drawn 2-digit prize
    }


def _parse_vicha_item(item: dict) -> Optional[dict]:
    date_str = item.get("date") or item.get("Date") or item.get("งวด")
    if not date_str:
        return None
    draw_date = _parse_date(str(date_str))
    if not draw_date:
        return None

    first_prize = None
    for key in ["first", "First", "first_prize", "รางวัลที่1", "prize1"]:
        val = item.get(key)
        if val and re.match(r"^\d{6}$", str(val).strip()):
            first_prize = str(val).strip()
            break

    last2 = None
    for key in ["last2", "Last2", "เลขท้าย2ตัว", "tail2"]:
        val = item.get(key)
        if val:
            v = str(val).strip().zfill(2)
            if re.match(r"^\d{2}$", v):
                last2 = v
                break

    if not first_prize:
        return None

    last3 = first_prize[-3:]
    if not last2:
        last2 = first_prize[-2:]

    return {"date": draw_date, "first_prize": first_prize, "last3": last3, "last2": last2}


def fetch_glo_latest() -> List[dict]:
    """Scrape glo.or.th for the most recent draw result."""
    print("Fetching latest draw from glo.or.th...")
    try:
        r = requests.get(GLO_RESULT_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        # glo.or.th result page patterns (may change — add more selectors as needed)
        first_prize = None
        last2 = None
        draw_date = None

        # Try common patterns
        for sel in [".lottery-result", ".result-number", "#award1", ".prize-first"]:
            el = soup.select_one(sel)
            if el:
                nums = re.findall(r"\d{6}", el.get_text())
                if nums:
                    first_prize = nums[0]
                    break

        # Date from page
        for sel in ["h1", "h2", ".lottery-date", ".result-date"]:
            el = soup.select_one(sel)
            if el:
                d = _parse_date(el.get_text())
                if d:
                    draw_date = d
                    break

        # Last 2 digits
        for sel in [".award-2digit", ".last2", "#award2digit"]:
            el = soup.select_one(sel)
            if el:
                nums = re.findall(r"\d{2}", el.get_text())
                if nums:
                    last2 = nums[0].zfill(2)
                    break

        if first_prize:
            last3 = first_prize[-3:]
            if not last2:
                last2 = first_prize[-2:]
            if not draw_date:
                draw_date = date.today().isoformat()
            print(f"  glo.or.th: {draw_date} → {first_prize}")
            return [{"date": draw_date, "first_prize": first_prize, "last3": last3, "last2": last2}]

        print("  glo.or.th: could not parse result")
        return []
    except Exception as e:
        print(f"  glo.or.th failed: {e}")
        return []


# ── Date parsing ──────────────────────────────────────────────────────────────

_THAI_MONTHS = {
    "มกราคม": 1, "ม.ค.": 1,
    "กุมภาพันธ์": 2, "ก.พ.": 2,
    "มีนาคม": 3, "มี.ค.": 3,
    "เมษายน": 4, "เม.ย.": 4,
    "พฤษภาคม": 5, "พ.ค.": 5,
    "มิถุนายน": 6, "มิ.ย.": 6,
    "กรกฎาคม": 7, "ก.ค.": 7,
    "สิงหาคม": 8, "ส.ค.": 8,
    "กันยายน": 9, "ก.ย.": 9,
    "ตุลาคม": 10, "ต.ค.": 10,
    "พฤศจิกายน": 11, "พ.ย.": 11,
    "ธันวาคม": 12, "ธ.ค.": 12,
}

def _parse_date(s: str) -> Optional[str]:
    s = s.strip()
    # ISO format
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y > 2500:
                y -= 543  # Thai Buddhist year → Gregorian
            return date(y, mo, d).isoformat()
        except ValueError:
            pass

    # dd/mm/yyyy or dd-mm-yyyy
    m = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", s)
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
            if y > 2500:
                y -= 543
            return date(y, mo, d).isoformat()
        except ValueError:
            pass

    # Thai text month
    for th_name, mo_num in _THAI_MONTHS.items():
        if th_name in s:
            nums = re.findall(r"\d+", s)
            if len(nums) >= 2:
                try:
                    d_num = int(nums[0])
                    y_num = int(nums[-1])
                    if y_num > 2500:
                        y_num -= 543
                    return date(y_num, mo_num, d_num).isoformat()
                except ValueError:
                    pass

    return None


# ── Merge & stats ─────────────────────────────────────────────────────────────

def merge_draws(sources: List[List[dict]]) -> List[dict]:
    """Merge multiple draw lists, deduplicate by date, sort ascending."""
    seen = {}
    for source in sources:
        for draw in source:
            d = draw["date"]
            if d not in seen:
                seen[d] = draw
    sorted_draws = sorted(seen.values(), key=lambda x: x["date"])
    print(f"Merged: {len(sorted_draws)} unique draws")
    return sorted_draws


def calculate_stats(draws: List[dict]) -> dict:
    total = len(draws)
    two_digit: Dict[str, int] = {}
    three_digit: Dict[str, int] = {}

    for draw in draws:
        l2 = draw.get("last2")
        l3 = draw.get("last3")
        if l2:
            two_digit[l2] = two_digit.get(l2, 0) + 1
        if l3:
            three_digit[l3] = three_digit.get(l3, 0) + 1

    def build_stat(counts: dict[str, int]) -> dict:
        return {
            k: {"count": v, "pct": round(v / total * 100, 4)}
            for k, v in counts.items()
        }

    return {
        "two_digit": build_stat(two_digit),
        "three_digit": build_stat(three_digit),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    all_sources = []

    # 1. Historical base (ANTDPU)
    antdpu = fetch_antdpu()
    if antdpu:
        all_sources.append(antdpu)

    # 2. vicha-w archive (2007–present)
    vicha = fetch_vicha_archive()
    if vicha:
        all_sources.append(vicha)

    # 3. Latest from glo.or.th
    glo = fetch_glo_latest()
    if glo:
        all_sources.append(glo)

    if not all_sources:
        print("ERROR: all sources failed. No data written.")
        sys.exit(1)

    draws = merge_draws(all_sources)
    stats = calculate_stats(draws)

    output = {
        "meta": {
            "total_draws": len(draws),
            "last_updated": date.today().isoformat(),
            "data_from": draws[0]["date"] if draws else None,
            "data_to": draws[-1]["date"] if draws else None,
        },
        "draws": draws,
        "stats": stats,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nWrote {OUT_FILE}")
    print(f"Total draws: {len(draws)}")
    print(f"Range: {output['meta']['data_from']} → {output['meta']['data_to']}")
    print(f"2-digit entries: {len(stats['two_digit'])}")
    print(f"3-digit entries: {len(stats['three_digit'])}")


if __name__ == "__main__":
    main()
