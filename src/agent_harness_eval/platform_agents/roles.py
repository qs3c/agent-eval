import shlex

TOOLS = {
    "guardian": {"snapshot", "evidence", "exec", "final", "finish"},
    "diagnosis": {"read", "exec", "final"},
    "integration": {"read", "exec", "write_recipe", "check", "final"},
    "improvement": {"read", "exec", "write", "check", "final"},
}


def guardian_command(text):
    """Accept bounded read-only inspection, never an arbitrary recovery shell."""
    if not isinstance(text, str) or not text or len(text) > 16384:
        raise ValueError("Invalid inspection command")
    if any(char in text for char in "\n\r\x00;|&<>`$\\"):
        raise ValueError("Guardian inspection does not accept shell syntax")
    args = shlex.split(text)
    if not args or args[0] not in {"ps", "pwd", "uname", "df", "free", "uptime", "date", "cat", "head", "tail", "wc", "stat"}:
        raise ValueError("Guardian permits read-only inspection; use final for supervised termination")
    if args[0] == "ps" and args[1:] not in ([], ["-eo", "pid,ppid,stat,etime,comm"]):
        raise ValueError("Guardian ps must omit environment and argument values")
    if args[0] in {"cat", "head", "tail", "wc", "stat"}:
        for arg in args[1:]:
            if arg.startswith("-") or arg.isdigit():
                continue
            # No arbitrary task sources, secrets, /proc/environ, or verifier inputs.
            if not arg.startswith("/logs/agent/") or ".." in arg.split("/") or any(word in arg.lower() for word in ("key", "secret", "credential", "policy")):
                raise ValueError("Guardian file inspection is limited to non-secret Agent logs")
    if args[0] == "date" and any(arg in {"-s", "--set", "-f", "--file"} or arg.startswith(("--set=", "--file=")) for arg in args[1:]):
        raise ValueError("Guardian cannot change time or read arbitrary files")
    return shlex.join(args)
