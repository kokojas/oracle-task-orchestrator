#!/usr/bin/env python3
"""Adaptive oracle image driver with a staged rate-limit ladder. Resumable.

Usage: drive_images.py <jobs.json> [--concurrency N] [--stagger SEC] [--logdir DIR]
jobs.json: [{"target":"/abs/NN.png","slug":"niche3-img-05","prompt":"..."}]

ChatGPT "Too many requests" handling (staged ladder, per user spec):
  1. Banner detected  -> BREAK_1 (7 min) pause, then a single probe attempt.
  2. Still limited     -> BREAK_2 (10 min) pause.
  3. After that        -> resume the MAIN mechanism at concurrency 3.
  If it recurs later, the ladder restarts from BREAK_1. A success resets the ladder.

Detection: a job that yields no PNG is treated as a rate-limit if oracle output
matches a rate-limit phrase OR it failed suspiciously fast (< FAST_FAIL_SEC);
otherwise it is a genuine FAIL (e.g. content filter) and is not retried.
A PNG is "done" if it exists, >1KB, PNG magic header. Present files are skipped.
"""
import sys, os, json, time, argparse, subprocess, pathlib, threading, queue, re

RATE_PATTERNS = ("too many requests", "temporarily limited", "rate limit",
                 "rate-limited", "slow down", "try again", "429",
                 "making requests too quickly",
                 # ChatGPT Plus image-generation quota (resets after a few minutes):
                 "plan limit for image generation", "limit for image generations",
                 "when the limit resets", "limit resets in")
MODEL_UI_PATTERNS = ("unable to find model option",)   # ChatGPT model-switcher UI changed
FAST_FAIL_SEC = 45            # a no-image return faster than this smells like a throttle
BREAK_1 = 7 * 60              # first pause: 7 minutes
BREAK_2 = 10 * 60             # second pause: 10 minutes
MAIN_CONCURRENCY = 3          # main mechanism after cooldowns: 3 at a time
# Oracle v0.21.3 handles ChatGPT's unified Intelligence picker, so select the
# requested GPT model and explicit high thinking time instead of relying on the
# UI's current model.
MODEL = os.environ.get("ORACLE_IMG_MODEL", "gpt-5.5")
THINKING_TIME = os.environ.get("ORACLE_IMG_THINKING_TIME", "high")
CHATGPT_URL = os.environ.get(
    "ORACLE_CHATGPT_URL",
    "https://chatgpt.com/",
)
# Node 26 + undici intermittently crashes with `setTypeOfService EINVAL` on IPv6
# sockets (macOS); forcing IPv4-first DNS avoids it.
ORACLE_ENV = {**os.environ, "NODE_OPTIONS": "--dns-result-order=ipv4first"}


def is_png(p: pathlib.Path) -> bool:
    return p.exists() and p.stat().st_size > 1000 and p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def looks_rate_limited(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in RATE_PATTERNS)


class Driver:
    def __init__(self, jobs, concurrency, stagger, logdir):
        self.q = queue.Queue()
        for j in jobs:
            self.q.put(j)
        self.concurrency = concurrency
        self.stagger = stagger
        self.logdir = pathlib.Path(logdir); self.logdir.mkdir(parents=True, exist_ok=True)

        self.lock = threading.Lock()
        self.serial_lock = threading.Lock()  # held during single-flight probe
        self.cooldown_until = 0.0             # workers must not launch before this wall-clock
        self.single_flight = False            # True during the post-7min probe window
        self.stage = 0                        # 0=clean, 1=after 7min break, 2=after 10min break
        self.abort = False                    # set when the model label no longer matches the UI
        self.done = 0
        self.total = len(jobs)
        self.results = {}

    # ---- oracle call -------------------------------------------------------
    def run_one(self, job):
        target = pathlib.Path(job["target"]); target.parent.mkdir(parents=True, exist_ok=True)
        tag = job["slug"]
        if is_png(target):
            return "skip", ""
        cmd = ["oracle", "--engine", "browser", "--model", MODEL,
               "--chatgpt-url", CHATGPT_URL,
               "--browser-thinking-time", THINKING_TIME,
               "--prompt", f"Generate one image. {job['prompt']}",
               "--generate-image", str(target), "--slug", tag,
               "--force",
               "--browser-manual-login",
               "--browser-auto-reattach-delay", "30s",
               "--browser-auto-reattach-interval", "2m",
               "--browser-auto-reattach-timeout", "8m"]
        t0 = time.time()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                               env=ORACLE_ENV)
            out = (p.stdout or "") + "\n" + (p.stderr or "")
        except subprocess.TimeoutExpired as e:
            out = (e.stdout if isinstance(e.stdout, str) else "") + "\n" + \
                  (e.stderr if isinstance(e.stderr, str) else "")
        dt = time.time() - t0
        (self.logdir / f"{tag}.log").write_text(out, encoding="utf-8")
        if is_png(target):
            return "ok", out
        low = out.lower()
        if any(p in low for p in MODEL_UI_PATTERNS):
            return "model_ui", out   # wrong model label for current ChatGPT UI; no point waiting
        # "No images generated" with an EMPTY response text means the conversation
        # was silently throttled (image-quota / too-many-requests), not refused —
        # a real refusal comes back with explanatory text. Treat empty as retryable.
        m = re.search(r"no images generated\.?\s*response text:\s*(\S?)", low)
        if m and not m.group(1):
            return "ratelimit", out
        if looks_rate_limited(out) or dt < FAST_FAIL_SEC:
            return "ratelimit", out
        return "fail", out

    # ---- throttle gate -----------------------------------------------------
    def gate_wait(self):
        """Block until any active cooldown has elapsed; return current single_flight."""
        while True:
            with self.lock:
                remaining = self.cooldown_until - time.time()
                sf = self.single_flight
            if remaining <= 0:
                return sf
            time.sleep(min(remaining, 10))

    def escalate(self, tag):
        """Advance the rate-limit ladder. Only escalates if no cooldown is active,
        so simultaneous hits from concurrent workers don't stack 7+10 minutes."""
        with self.lock:
            now = time.time()
            if now < self.cooldown_until:
                return  # another worker already opened a cooldown window; just requeue
            self.stage += 1
            if self.stage == 1:
                wait, self.single_flight = BREAK_1, True
                label = f"break 1 = {BREAK_1//60} min, then single probe"
            elif self.stage == 2:
                wait, self.single_flight = BREAK_2, False
                label = f"break 2 = {BREAK_2//60} min, then resume @ {MAIN_CONCURRENCY}"
            else:
                # recurring banner after we already resumed -> restart ladder
                self.stage, wait, self.single_flight = 1, BREAK_1, True
                label = f"ladder restart: break {BREAK_1//60} min, then single probe"
            self.cooldown_until = now + wait
        print(f"  [RATELIMIT] {tag}: {label}; requeued", flush=True)

    def on_success(self):
        with self.lock:
            self.stage = 0
            self.single_flight = False

    # ---- worker ------------------------------------------------------------
    def worker(self):
        while True:
            if self.abort:
                return
            try:
                job = self.q.get_nowait()
            except queue.Empty:
                return
            tag = job["slug"]
            # Already-done files skip instantly — no gate wait, no stagger.
            if is_png(pathlib.Path(job["target"])):
                with self.lock:
                    self.done += 1; self.results[tag] = "skip"
                print(f"  {tag}: skip ({self.done}/{self.total})", flush=True)
                self.q.task_done()
                continue
            sf = self.gate_wait()
            if sf:
                self.serial_lock.acquire()
            try:
                if self.stagger:
                    time.sleep(self.stagger)
                status, _ = self.run_one(job)
            finally:
                if sf and self.serial_lock.locked():
                    self.serial_lock.release()

            if status in ("ok", "skip"):
                if status == "ok":
                    self.on_success()
                with self.lock:
                    self.done += 1; self.results[tag] = status
                print(f"  {tag}: {status} ({self.done}/{self.total})", flush=True)
            elif status == "model_ui":
                self.abort = True
                self.q.put(job)          # keep it pending for the relaunch
                print(f"  [MODEL-UI] {tag}: ChatGPT switcher no longer has \"{MODEL}\" — "
                      f"aborting run; relaunch with ORACLE_IMG_MODEL=<label from the menu> "
                      f"(see {self.logdir}/{tag}.log for available options)", flush=True)
            elif status == "ratelimit":
                self.escalate(tag)
                self.q.put(job)          # retry later; not counted as done/failed
            else:
                with self.lock:
                    self.results[tag] = "fail"
                print(f"  {tag}: FAIL (non-ratelimit; see {self.logdir}/{tag}.log)", flush=True)
            self.q.task_done()

    def run(self):
        print(f"jobs={self.total} concurrency={self.concurrency} stagger={self.stagger}s "
              f"ladder={BREAK_1//60}min->{BREAK_2//60}min->@{MAIN_CONCURRENCY}", flush=True)
        threads = [threading.Thread(target=self.worker) for _ in range(self.concurrency)]
        for t in threads: t.start()
        for t in threads: t.join()
        present = sum(1 for v in self.results.values() if v in ("ok", "skip"))
        failed = [k for k, v in self.results.items() if v == "fail"]
        print(f"DONE: {present}/{self.total} present; failed(non-rl): {failed or 'none'}", flush=True)
        return 0 if not failed else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs")
    ap.add_argument("--concurrency", type=int, default=MAIN_CONCURRENCY)
    ap.add_argument("--stagger", type=float, default=8.0)
    ap.add_argument("--logdir", default="_drive_logs")
    a = ap.parse_args()
    jobs = json.load(open(a.jobs, encoding="utf-8"))
    sys.exit(Driver(jobs, a.concurrency, a.stagger, a.logdir).run())


if __name__ == "__main__":
    main()
