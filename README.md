# LottoStat TH

Thai lottery statistics PWA — frequency analysis of 2-digit and 3-digit prize numbers across 460+ historical draws.

**Live app:** https://dopettc.github.io/lottostats-th

## Features

- 🔥 Hot & cold number heatmap
- 🏆 Frequency rankings with percentage vs baseline
- 📊 Bar charts (Top 20 / Top 15)
- 🔄 Pull-to-refresh for latest data
- 📱 iPhone PWA — add to home screen

## Data Sources

All lottery result data is sourced from publicly available repositories. Full credit to the original collectors:

| Source | What it provides |
|--------|-----------------|
| [vicha-w/thai-lotto-archive](https://github.com/vicha-w/thai-lotto-archive) | Per-draw result files 2006–present (main source) |
| [ANTDPU/ThaiGovernmentLotteryResults](https://huggingface.co/datasets/ANTDPU/ThaiGovernmentLotteryResults) | Historical CSV dataset |
| [glo.or.th](https://www.glo.or.th) | Official Government Lottery Office (latest draw fallback) |

Lottery result numbers are public facts published by the Thai government.

## Auto-Update

GitHub Actions runs the scraper automatically on the 1st and 16th of every month at 16:00 Thailand time, matching the official draw schedule.

## Local Development

```bash
# Run scraper manually
pip install -r scraper/requirements.txt
python scraper/fetch_data.py

# Serve locally (required — fetch won't work on file://)
python3 -m http.server 8888
# Open http://localhost:8888
```

## Disclaimer

Statistical frequency data is for entertainment only. Past results do not predict future draws.
