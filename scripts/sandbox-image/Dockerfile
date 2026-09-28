FROM python:3.12-bullseye

# No network at runtime (upstream passes --network none); pip installs here
# happen at build time only.
RUN pip install --no-cache-dir --upgrade pip

# Non-root execution is not possible for bind-mount write persistence in the
# default docker setup on Windows; the runner keeps the container short-lived
# (--rm) and the image contains no secrets.
COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh && mkdir -p /workspace

# The container reads a JSON payload from stdin with keys:
#   _workspace: working directory inside the container
#   _command:   argv list to execute (no shell)
#   _cwd:       working directory for the command (defaults to _workspace)
# and emits a sentinel-wrapped JSON result on stdout:
#   {"returncode": int, "output": str, "error": bool}
ENTRYPOINT ["/usr/local/bin/run.sh"]
