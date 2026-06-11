"""
PredictionLab Site Checker — Email Configuration
=================================================
Fill in your Gmail credentials below, then run:

  python qa/site_checker.py --email

HOW TO GET A GMAIL APP PASSWORD:
  1. Go to https://myaccount.google.com/security
  2. Enable 2-Step Verification if not already on
  3. Go to https://myaccount.google.com/apppasswords
  4. Create a new App Password (name it "PredictionLab Checker")
  5. Paste the 16-character password below (spaces are fine)

NOTE: This file is .gitignored — your password won't be committed.
"""

# ── Fill these in ─────────────────────────────────────────────────────────

# Your Gmail address (the account you want to send FROM)
EMAIL_FROM = "nmesghali@gmail.com"

# 16-character Gmail App Password (NOT your regular Gmail password)
# Example: "abcd efgh ijkl mnop"
# → Get yours at: https://myaccount.google.com/apppasswords
EMAIL_PASSWORD = ""   # ← PASTE YOUR APP PASSWORD HERE

# Who receives the report
EMAIL_TO = "nmesghali@gmail.com"

# SMTP settings — leave as-is for Gmail
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
