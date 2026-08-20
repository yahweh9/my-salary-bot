# Multi-Job Salary Tracker Bot

A smart Telegram bot designed to help track hours and calculate pay for multiple part-time jobs or freelance gigs. Built with Python, Flask, and PostgreSQL, and optimized for serverless deployment on Vercel.

##  Features
* **Multi-Job Support:** Create profiles for different jobs (e.g., Cafe, Tutor, Freelance) with unique hourly rates.
* **Bulk Time Logging:** Log multiple shifts at once using a comma-separated format (e.g., `8 45, 4, 7 30`).
* **Monthly Overviews:** Generate instant breakdowns of hours worked and total expected pay per job.
* **Serverless Architecture:** Uses webhooks and PostgreSQL state-management to run 24/7 on Vercel without background polling.

## Tech Stack
* **Language:** Python 3
* **Bot Framework:** `pyTelegramBotAPI` (Telebot)
* **Web Server:** Flask (for handling Telegram Webhooks)
* **Database:** PostgreSQL (`psycopg2`)

---

## Local Development Setup

If you want to run and test this bot on your own machine, follow these steps:

### 1. Prerequisites
* Python 3.9+ installed
* A Telegram Bot Token from [@BotFather](https://t.me/botfather)
* A PostgreSQL database (you can use a free cloud database like Vercel Postgres or Neon)
* [ngrok](https://ngrok.com/) (to expose your local server to the internet)

### 2. Environment Variables
Create a `.env` file in the root directory and add your credentials. **Never commit this file to GitHub.**
```ini
BOT_TOKEN=your_telegram_bot_token_here
DATABASE_URL=postgres://user:password@host/dbname
