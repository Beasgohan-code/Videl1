# Permanent Link Cloudflare Worker

This is the backend for the permanent link feature of the Telegram Bot. It is designed to be hosted entirely on **Cloudflare Workers** and communicates directly with MongoDB.

## Features
- Provides an API for the main Telegram bot to store user token updates.
- Provides an API for the main Telegram bot to store link-to-user mappings.
- Redirects users when they visit the Cloudflare Worker URL with a link token.

## Setup Instructions

1.  **Clone / Download this folder**
2.  **Install Wrangler (Cloudflare's CLI):**
    ```bash
    npm install -g wrangler
    ```
3.  **Login to Cloudflare:**
    ```bash
    wrangler login
    ```
4.  **Create a `wrangler.toml` file** in this folder:
    ```toml
    name = "permanent-link-worker"
    main = "index.js"
    compatibility_date = "2023-10-30"
    compatibility_flags = ["nodejs_compat"]

    [vars]
    MONGODB_URI = "your_mongodb_connection_string_here"
    BACKEND_API_SECRET = "your_secret_key_here"
    ```
5.  **Deploy the worker:**
    ```bash
    wrangler deploy
    ```
6.  **Get the URL:**
    After deployment, Cloudflare will give you a URL (e.g., `https://permanent-link-worker.yourname.workers.dev`).
7.  **Update your Telegram Bot Config:**
    In your Python bot's `.env` or configuration, set:
    ```
    BACKEND_API_URL=https://permanent-link-worker.yourname.workers.dev
    BACKEND_API_SECRET=your_secret_key_here
    ```

Once configured, the Telegram bot will automatically send user data and link data to this worker whenever a user toggles "Permanent Link" on in their bot dashboard or generates a new link.
