import streamlit as st
import subprocess
import sys
import os
import time
import datetime
import glob

# ============ CONFIGURATION ============
WORKER_PATTERN = "visitor_worker*.py"    # visitor_worker.py, visitor_worker1.py, ...
LOG_DIR = "logs"                          # per-worker logs live here
PID_DIR = "pids"                          # per-worker pid files live here

# ---- Run mode configuration ----
RUN_MULTIPLE_WORKERS = False               # True = run all discovered workers; False = run only SINGLE_WORKER_NAME
SINGLE_WORKER_NAME = "visitor_worker9.py"  # Full filename of the single worker to run when RUN_MULTIPLE_WORKERS = False
# =======================================

st.set_page_config(page_title="Visitor Scheduler", layout="wide")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(PID_DIR, exist_ok=True)


# ---------- Discovery ----------
def discover_workers():
    """Find all worker scripts matching the pattern, sorted."""
    here = os.path.dirname(os.path.abspath(__file__))
    files = sorted(glob.glob(os.path.join(here, WORKER_PATTERN)))
    return [os.path.basename(f) for f in files]


def select_workers(all_workers):
    """
    Apply RUN_MULTIPLE_WORKERS flag:
      - True  -> return all discovered workers
      - False -> return only [SINGLE_WORKER_NAME] if it exists, else []
    """
    if RUN_MULTIPLE_WORKERS:
        return all_workers

    if SINGLE_WORKER_NAME in all_workers:
        return [SINGLE_WORKER_NAME]
    return []


def log_path(worker):
    return os.path.join(LOG_DIR, f"{os.path.splitext(worker)[0]}.log")


def pid_path(worker):
    return os.path.join(PID_DIR, f"{os.path.splitext(worker)[0]}.pid")


# ---------- Worker management ----------
def is_worker_running(worker):
    """Check if a specific worker process is alive."""
    pfile = pid_path(worker)
    if not os.path.exists(pfile):
        return False
    try:
        with open(pfile) as f:
            pid = int(f.read().strip())
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True
            )
            return str(pid) in out.stdout
        else:
            os.kill(pid, 0)
            return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def start_worker(worker):
    """Spawn a specific worker as a detached background process."""
    if is_worker_running(worker):
        return False, f"{worker} already running"

    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
    else:
        kwargs["start_new_session"] = True

    log_fh = open(log_path(worker), "a", encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, worker],
        stdout=log_fh,
        stderr=log_fh,
        stdin=subprocess.DEVNULL,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        **kwargs,
    )

    with open(pid_path(worker), "w") as f:
        f.write(str(proc.pid))

    return True, f"{worker} started (PID {proc.pid})"


def stop_worker(worker):
    """Kill a specific worker."""
    pfile = pid_path(worker)
    if not os.path.exists(pfile):
        return False, f"{worker} not running"
    try:
        with open(pfile) as f:
            pid = int(f.read().strip())

        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True)
        else:
            os.kill(pid, 15)  # SIGTERM

        try:
            os.remove(pfile)
        except OSError:
            pass
        return True, f"Stopped {worker} (PID {pid})"
    except (ValueError, ProcessLookupError) as e:
        try:
            os.remove(pfile)
        except OSError:
            pass
        return False, f"Could not stop {worker}: {e}"


def read_log_tail(worker, n=150):
    """Read the last N lines of a worker's log."""
    p = log_path(worker)
    if not os.path.exists(p):
        return "(no logs yet)"
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]) or "(log is empty)"
    except Exception as e:
        return f"(could not read log: {e})"


# ---------- Resolve which workers to manage ----------
all_workers = discover_workers()
workers = select_workers(all_workers)


# ---------- Auto-start workers on first page load ----------
if "auto_started" not in st.session_state:
    st.session_state.auto_started = True
    started = []
    for w in workers:
        if not is_worker_running(w):
            ok, msg = start_worker(w)
            if ok:
                started.append(msg)
    print(f"[auto-start] started: {started}")


# ---------- UI ----------
st.title("🌐 Selenium Visitor Scheduler")

# Show current run mode
if RUN_MULTIPLE_WORKERS:
    st.info("🔀 **Run mode: MULTIPLE** — all matching workers will run.")
else:
    st.info(
        f"🎯 **Run mode: SINGLE** — only `{SINGLE_WORKER_NAME}` will run. "
        f"(Toggle `RUN_MULTIPLE_WORKERS` in the config to change.)"
    )

st.caption(
    "Each `visitor_worker*.py` file runs as its own detached background "
    "process. Closing this tab does **not** stop them."
)

# Discovery summary
st.subheader(f"🧭 Active workers ({len(workers)} of {len(all_workers)} discovered)")
if not all_workers:
    st.warning(
        f"No files matching `{WORKER_PATTERN}` found. Add one and reload."
    )
elif not workers:
    st.error(
        f"Run mode is SINGLE but `{SINGLE_WORKER_NAME}` was not found "
        f"among discovered workers: {all_workers}"
    )
else:
    st.code("\n".join(workers), language="text")
    if not RUN_MULTIPLE_WORKERS and len(all_workers) > 1:
        with st.expander("Show ignored workers"):
            ignored = [w for w in all_workers if w not in workers]
            st.code("\n".join(ignored), language="text")

st.divider()

# ---- Per-worker cards ----
for w in workers:
    running = is_worker_running(w)
    header = f"{'🟢' if running else '🔴'}  `{w}`"

    with st.expander(header, expanded=True):
        col1, col2 = st.columns([2, 2])

        with col1:
            st.metric("Status", "Running" if running else "Stopped")
            if running:
                try:
                    with open(pid_path(w)) as f:
                        st.caption(f"PID: {f.read().strip()}")
                except Exception:
                    pass

        with col2:
            bc1, bc2, bc3 = st.columns(3)
            with bc1:
                if st.button("▶️", key=f"start_{w}"):
                    ok, msg = start_worker(w)
                    st.toast(msg)
                    st.rerun()
            with bc2:
                if st.button("⏹", key=f"stop_{w}"):
                    ok, msg = stop_worker(w)
                    st.toast(msg)
                    st.rerun()
            with bc3:
                if st.button("🧹", key=f"clear_{w}"):
                    try:
                        open(log_path(w), "w").close()
                    except Exception:
                        pass
                    st.rerun()

        st.code(read_log_tail(w, 100) or "(no logs yet)", language="log")


# ---- Global controls ----
st.divider()
st.subheader("⚙️ Global controls")
g1, g2 = st.columns(2)
with g1:
    if st.button("▶️ Start all workers"):
        for w in workers:
            if not is_worker_running(w):
                start_worker(w)
        st.toast("Started all workers")
        st.rerun()
with g2:
    if st.button("⏹ Stop all workers"):
        for w in workers:
            stop_worker(w)
        st.toast("Stopped all workers")
        st.rerun()


# ---- Auto-refresh the page every 5s ----
st.markdown(
    """
    <script>
        setTimeout(function() {
            window.parent.location.reload();
        }, 5000);
    </script>
    """,
    unsafe_allow_html=True,
)

st.caption(
    "Page auto-reloads every 5s. Reloading or closing the tab does not "
    "affect the background workers."
)