## 10. Git hygiene

### 10.1 Commit message format
Follow Conventional Commits format:
```
<type>(<scope>): <short summary>

[optional body explaining why, not what]
```
- **Types**: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`.
- **Imperative mood**: Use verbs like `add`, `fix`, `update` — never past tense (e.g., "added" or "fixed").
- **Length**: Keep the first line under 50 characters.
- **Emoji**: Always start the message with a relevant git emoji.
- **Example**: `feat(ingest): add MinerU optional parsing path`

### 10.2 One logical change per commit
Do not mix a feature addition and a formatting cleanup in one commit.

### 10.3 Never commit secrets
`.env` is in `.gitignore`. Only `.env.example` (no real values) is committed.
