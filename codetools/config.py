CLIPBOARD_POLL_SECONDS = 0.5

READ_MAX_LINES = 2000
MAX_TEXT_FILE_BYTES = 2_000_000
GREP_MAX_MATCHES = 300
GREP_MAX_CONTEXT = 20
FIND_MAX_RESULTS = 500
TREE_MAX_LINES = 500
OUTLINE_MAX_LINES = 800
OUTPUT_LINE_MAX_CHARS = 300

COMMAND_OUTPUT_HEAD_LINES = 100
COMMAND_OUTPUT_TAIL_LINES = 300

# Directory names never read, written, or listed.
PROTECTED_DIRS = frozenset({".git", ".codetools"})

# Directory names skipped when listing files outside a git work tree.
WALK_SKIP_DIRS = frozenset(
    {".git", ".codetools", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
     ".ruff_cache", "dist", "build"}
)

# Basename patterns whose contents are never sent to the chat or edited by it.
SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx", "id_rsa*", "id_dsa*",
                   "id_ecdsa*", "id_ed25519*")
SECRET_EXCEPTIONS = frozenset({".env.example", ".env.sample", ".env.template"})
