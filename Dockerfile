# Development environment for ForkJudge.
#
# It contains everything the judge needs to run real code:
#   gcc, g++        compile C and C++ (we link statically, see config.py)
#   libc6-dev       provides libc.a, the static C library that "-static" needs
#   python3         runs the judge itself AND is one of the judged languages
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc g++ libc6-dev python3 python3-venv openssl \
 && rm -rf /var/lib/apt/lists/*

# Flask goes into a virtual environment (Ubuntu forbids pip installs into the
# system Python). The sandbox still runs user code with the BASE interpreter
# /usr/bin/python3, so Flask is never visible inside the jail.
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The unprivileged account that user code runs as after the judge drops root.
# The numbers must match SANDBOX_UID / SANDBOX_GID in config.py.
# It has no home directory and no login shell.
RUN groupadd --gid 20000 sandbox \
 && useradd --uid 20000 --gid 20000 --no-create-home --shell /usr/sbin/nologin sandbox

WORKDIR /app
COPY . /app

# The judge starts as root (it needs chroot and setuid) and gives root up in
# every child before the user's program runs.
ENV JUDGE_HOST=0.0.0.0 \
    JUDGE_PORT=5000
EXPOSE 5000
CMD ["python3", "app.py"]
