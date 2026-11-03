# AI Life Assistant

A beginner-friendly Flask productivity app with private accounts, tasks, habits, goals, dashboard metrics, and a multilingual AI coach.

## Exact Windows setup and run commands

1. Install Python 3.10 or newer, then open PowerShell in this project folder.
2. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Create your local configuration file and edit it:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   Set a long, random `SECRET_KEY`. To enable AI, add your Google AI Studio key after `GEMINI_API_KEY=` and keep `GEMINI_MODEL=gemini-2.5-flash`. The key is used only by Flask on the server and is never sent to the browser.

5. Run the app:

   ```powershell
   python -m flask --app app run --debug
   ```

6. Visit `http://127.0.0.1:5000`, create an account, and start using the app. Stop it with `Ctrl+C`.

The SQLite database is created automatically on first use. If you want to create it manually, use:

```powershell
python -m flask --app app init-db
```

## Notes

- Database location is configurable with `DATABASE` in `.env`.
- Authentication uses a username and password only. Usernames are 3–32 characters and can contain letters, numbers, dots, hyphens, and underscores.
- Existing email-based databases are migrated automatically and are never deleted. A legacy account receives a username based on the part before its old email's `@` (or `user<ID>` if needed); add `-1`, `-2`, and so on if that username was already taken. Its existing password and all tasks, habits, goals, and chat history are preserved. The old email column is retained only in legacy SQLite databases for compatibility and is no longer read, shown, or used for login.
- Tasks, habits, goals, and AI history are scoped to the signed-in user.
- AI uses the official Google Gen AI Python SDK with Gemini. Requests accept English, Hindi, and Hinglish; the model is told to reply in the language used in the request.
- Without `GEMINI_API_KEY`, the app shows clear setup instructions instead of inventing an AI reply.
- Forms and AI requests use a per-session CSRF token. Session cookies are HTTP-only and SameSite=Lax.
- Never commit `.env` or the generated `life_assistant.db` file.
