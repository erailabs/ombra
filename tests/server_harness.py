# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Runs the CPU-only `ombra_server` for tests (crash, shutdown, upgrade tests build on this).

    server = ServerProcess(tmp_path)          # data dir: tmp_path / "data"
    server.start()                            # loopback, no TLS, no API keys
    client = server.client()                  # the real ombra.Client
    server.kill(); server.start()             # SIGKILL, then restart on the same data dir
    server.stop()                             # SIGTERM, then SIGKILL after a timeout

The binary comes from the environment variable OMBRA_SERVER_BIN, else from
build-cpu/server/grpc/ombra_server (`make build-cpu`). `find_server_binary()` returns None if it
does not exist, so a test can skip with a clear reason. The server listens on 127.0.0.1 with port 0
(the system picks a free one) and its log goes to <root>/server.log (appended across restarts).
Every start may get another port: use `server.address` after each start.

Synthetic data only: the harness never reads what the server stores.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINARY = REPO_ROOT / "build-cpu" / "server" / "grpc" / "ombra_server"
LISTENING = "ombra_server listening on "


def find_server_binary() -> Path | None:
    """The server binary named by OMBRA_SERVER_BIN or the default build dir, or None."""
    env = os.environ.get("OMBRA_SERVER_BIN")
    for candidate in ([Path(env)] if env else []) + [DEFAULT_BINARY]:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _installed_openfhe_version() -> str:
    import openfhe

    return openfhe.__version__


class ServerProcess:
    def __init__(
        self,
        root: Path,
        *,
        binary: Path | None = None,
        extra_args: list[str] | None = None,
        rest: bool = False,
        openfhe_version: str | None = None,
        auth: bool = False,
    ) -> None:
        found = binary or find_server_binary()
        if found is None:
            raise FileNotFoundError(
                "ombra_server not found: run `make build-cpu` or set OMBRA_SERVER_BIN"
            )
        self.binary = found
        self.root = Path(root)
        self.data_dir = self.root / "data"
        self.log_path = self.root / "server.log"
        self.extra_args = list(extra_args or [])
        self.rest = rest
        # False (default): --no-auth. True: the server needs an API key file in the data dir
        # (create one with `ombra_server --create-api-key`, see `create_key`).
        self.auth = auth
        # The version the stored envelopes carry; the client writes openfhe-python's version.
        self.openfhe_version = openfhe_version
        self.address = ""  # "127.0.0.1:PORT" while running
        self.rest_address = ""
        self._proc: subprocess.Popen[str] | None = None
        self._clients: list[Any] = []

    # ---- life cycle ---------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int:
        assert self._proc is not None
        return self._proc.pid

    def start(self, timeout: float = 60.0) -> str:
        """Start the server and wait until it listens. Returns its address."""
        if self.running:
            raise RuntimeError("server already running")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        args = [
            str(self.binary),
            "--data-dir",
            str(self.data_dir),
            "--listen",
            "127.0.0.1:0",
            "--insecure",
            *([] if self.auth else ["--no-auth"]),
            "--openfhe-version",
            self.openfhe_version or _installed_openfhe_version(),
            *(["--rest-port", "0"] if self.rest else []),
            *self.extra_args,
        ]
        log = open(self.log_path, "ab")
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - our own binary, fixed arguments
                args, stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1
            )
        finally:
            log.close()
        self.address = self.rest_address = ""
        ready = threading.Event()
        threading.Thread(target=self._read_stdout, args=(self._proc, ready), daemon=True).start()
        deadline = time.monotonic() + timeout
        while not ready.wait(0.05):
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"server exited with code {self._proc.returncode} before it was ready; "
                    f"log tail:\n{self.log_tail()}"
                )
            if time.monotonic() > deadline:
                self.kill()
                raise TimeoutError(
                    f"server not ready after {timeout} s; log tail:\n{self.log_tail()}"
                )
        return self.address

    def _read_stdout(self, proc: subprocess.Popen[str], ready: threading.Event) -> None:
        # Keeps draining stdout until the process closes it, so the server never blocks on a pipe.
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith(LISTENING):
                self.address = line[len(LISTENING) :]
                if not self.rest:
                    ready.set()
            elif line.startswith("ombra_server rest listening on "):
                self.rest_address = line.rsplit(" ", 1)[1]
                ready.set()

    def stop(self, timeout: float = 20.0) -> int | None:
        """SIGTERM, then SIGKILL after `timeout` seconds. Returns the exit code."""
        self._close_clients()
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return None if proc is None else proc.returncode
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return proc.returncode

    def kill(self) -> int | None:
        """SIGKILL, no clean shutdown. Returns the exit code (-9)."""
        self._close_clients()
        proc = self._proc
        if proc is None:
            return None
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        return proc.returncode

    def restart(self, *, kill: bool = False) -> str:
        """Stop (or SIGKILL) and start again on the same data dir."""
        self.kill() if kill else self.stop()
        return self.start()

    def create_key(self, scope: str, collection: str | None = None) -> str:
        """Create an API key in the data dir with the server binary and return its token."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        args = [str(self.binary), "--create-api-key", "--scope", scope]
        args += ["--data-dir", str(self.data_dir)]
        if collection is not None:
            args += ["--collection", collection]
        out = subprocess.run(args, capture_output=True, text=True, check=True)  # noqa: S603
        return out.stdout.strip()

    # ---- clients ------------------------------------------------------------------------------
    def client(self, **kwargs: Any) -> Any:
        """A new `ombra.Client` on the current address (closed again by stop/kill)."""
        from ombra import Client

        c = Client(self.address, insecure=True, **kwargs)
        self._clients.append(c)
        return c

    def _close_clients(self) -> None:
        clients, self._clients = self._clients, []
        for c in clients:
            with contextlib.suppress(Exception):
                c.close()

    def log_tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    # ---- context manager ----------------------------------------------------------------------
    def __enter__(self) -> ServerProcess:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.kill()


# ---- router and CPU nodes ----
DEFAULT_ROUTER = REPO_ROOT / "build-cpu" / "server" / "grpc" / "ombra_router"
CPU_NODE_SCRIPT = REPO_ROOT / "tests" / "cpu_node.py"


def find_router_binary() -> Path | None:
    """The router binary named by OMBRA_ROUTER_BIN or the default build dir, or None."""
    env = os.environ.get("OMBRA_ROUTER_BIN")
    for candidate in ([Path(env)] if env else []) + [DEFAULT_ROUTER]:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


class _ListeningProcess:
    """A child process that prints '<something> listening on HOST:PORT' when it is ready."""

    marker = " listening on "

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "process.log"
        self.address = ""
        self._proc: subprocess.Popen[str] | None = None
        self._clients: list[Any] = []

    def _args(self) -> list[str]:
        raise NotImplementedError

    def _env(self) -> dict[str, str]:
        return dict(os.environ)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout: float = 120.0) -> str:
        if self.running:
            raise RuntimeError("already running")
        log = open(self.log_path, "ab")
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - our own binary and script
                self._args(),
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                bufsize=1,
                env=self._env(),
            )
        finally:
            log.close()
        self.address = ""
        ready = threading.Event()

        def drain(proc: subprocess.Popen[str]) -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self.marker in line and not ready.is_set():
                    self.address = line.strip().rsplit(" ", 1)[1]
                    ready.set()

        threading.Thread(target=drain, args=(self._proc,), daemon=True).start()
        deadline = time.monotonic() + timeout
        while not ready.wait(0.05):
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"exited with code {self._proc.returncode} before it was ready; log tail:\n"
                    f"{self.log_tail()}"
                )
            if time.monotonic() > deadline:
                self.kill()
                raise TimeoutError(f"not ready after {timeout} s; log tail:\n{self.log_tail()}")
        return self.address

    def stop(self, timeout: float = 20.0) -> int | None:
        self._close_clients()
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return None if proc is None else proc.returncode
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return proc.returncode

    def kill(self) -> int | None:
        self._close_clients()
        proc = self._proc
        if proc is None:
            return None
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        return proc.returncode

    def client(self, **kwargs: Any) -> Any:
        from ombra import Client

        c = Client(self.address, insecure=True, **kwargs)
        self._clients.append(c)
        return c

    def _close_clients(self) -> None:
        clients, self._clients = self._clients, []
        for c in clients:
            with contextlib.suppress(Exception):
                c.close()

    def log_tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def __enter__(self) -> _ListeningProcess:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.kill()


class CpuNodeProcess(_ListeningProcess):
    """A node that searches on the CPU (tests/cpu_node.py over local mode's LocalServer).

    The data dir is <root>/data. Restarting it on the same dir keeps the data; the port changes
    unless `port` is given (the placement file names the address, so a restarted node should keep
    its port: start(), read `.address`, and pass that port on the next start).
    """

    def __init__(self, root: Path, *, token: str = "", search_log: bool = False) -> None:
        super().__init__(root)
        self.data_dir = self.root / "data"
        self.token = token
        self.search_log = self.root / "searches.txt" if search_log else None
        self.port = 0

    def _env(self) -> dict[str, str]:
        # The node must run this repository's client code (the pytest `pythonpath` setting does
        # not reach a child process).
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT / "client")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
        )
        return env

    def _args(self) -> list[str]:
        args = [
            sys.executable,
            str(CPU_NODE_SCRIPT),
            "--data-dir",
            str(self.data_dir),
            "--listen",
            f"127.0.0.1:{self.port}",
            "--openfhe-version",
            _installed_openfhe_version(),
        ]
        if self.token:
            args += ["--token", self.token]
        if self.search_log:
            args += ["--search-log", str(self.search_log)]
        return args

    def start(self, timeout: float = 120.0) -> str:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        addr = super().start(timeout)
        self.port = int(addr.rsplit(":", 1)[1])  # restart on the same port
        return addr

    def searched_clusters(self) -> list[list[int]]:
        """Cluster ids of every SearchChosen this node has received (needs search_log=True)."""
        if not self.search_log or not self.search_log.exists():
            return []
        return [[int(x) for x in ln.split()] for ln in self.search_log.read_text().splitlines()]


class RouterProcess(_ListeningProcess):
    """`ombra_router` on loopback with a placement file written from the nodes' addresses.

    router = RouterProcess(tmp_path / "router", [node_a, node_b, node_c])
    router.start()
    client = router.client()
    """

    marker = "ombra_router listening on "

    def __init__(
        self,
        root: Path,
        nodes: list[CpuNodeProcess],
        *,
        binary: Path | None = None,
        extra_args: list[str] | None = None,
        node_token: str = "",
        epoch: int = 1,
    ) -> None:
        super().__init__(root)
        found = binary or find_router_binary()
        if found is None:
            raise FileNotFoundError("ombra_router not found: run `make build-cpu`")
        self.binary = found
        self.nodes = nodes
        self.extra_args = list(extra_args or [])
        self.node_token = node_token
        self.epoch = epoch
        self.data_dir = self.root / "data"
        self.placement_path = self.root / "placement"
        self.port = 0

    def write_placement(self) -> None:
        lines = ["ombra-placement 1", f"epoch {self.epoch}"]
        lines += [f"node n{i} {n.address}" for i, n in enumerate(self.nodes)]
        self.placement_path.write_text("\n".join(lines) + "\n")

    def _args(self) -> list[str]:
        args = [
            str(self.binary),
            "--data-dir",
            str(self.data_dir),
            "--listen",
            f"127.0.0.1:{self.port}",
            "--placement",
            str(self.placement_path),
            "--insecure",
            "--no-auth",
            "--node-insecure",
        ]
        if self.node_token:
            key_file = self.root / "node.key"
            key_file.write_text(self.node_token + "\n")
            key_file.chmod(0o600)
            args += ["--node-key-file", str(key_file)]
        return args + self.extra_args

    def start(self, timeout: float = 60.0) -> str:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.write_placement()
        addr = super().start(timeout)
        self.port = int(addr.rsplit(":", 1)[1])
        return addr
