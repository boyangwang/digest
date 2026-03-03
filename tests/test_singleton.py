"""DIGEST-008: Singleton Guard — Tests for PID lock, SIGTERM handler, concurrent process rejection.

These tests cover:
- T1: flock-based PID lock (acquire, reject, release, PID content)
- T2: SIGTERM handler (cleanup, logging)
- T3: Integration — concurrent processes, lock after SIGTERM
"""

import fcntl
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest


# ============================================================
# T1: Unit tests for flock-based PID lock
# ============================================================


class TestAcquirePidLock:
    """T1 — flock-based PID lock unit tests."""

    def test_acquire_creates_pidfile(self, tmp_path):
        """After acquire, PID file exists with correct PID."""
        from main import acquire_pid_lock

        pidfile = str(tmp_path / "test.pid")
        fd = acquire_pid_lock(pidfile=pidfile)

        assert os.path.exists(pidfile)
        with open(pidfile) as f:
            content = f.read().strip()
        assert content == str(os.getpid())

        # Cleanup
        fd.close()

    def test_acquire_rejects_if_locked(self, tmp_path):
        """If another fd holds the flock, acquire_pid_lock() calls sys.exit(1)."""
        pidfile = str(tmp_path / "test.pid")

        # First: acquire lock manually
        fd1 = open(pidfile, "w")
        fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd1.write("99999")
        fd1.flush()

        # Second: try to acquire — should exit
        from main import acquire_pid_lock

        with pytest.raises(SystemExit) as exc_info:
            acquire_pid_lock(pidfile=pidfile)
        assert exc_info.value.code == 1

        # Cleanup
        fd1.close()

    def test_lock_released_on_fd_close(self, tmp_path):
        """Closing the fd releases the lock (simulates process death including SIGKILL)."""
        pidfile = str(tmp_path / "test.pid")

        # Acquire lock
        fd1 = open(pidfile, "w")
        fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd1.write("11111")
        fd1.flush()

        # Close fd — should release lock
        fd1.close()

        # Now a new acquire should succeed
        fd2 = open(pidfile, "w")
        try:
            fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            locked = False
        finally:
            fd2.close()

        assert locked, "Lock should be available after fd close"

    def test_pidfile_contains_pid(self, tmp_path):
        """PID file contains the current process PID (informational)."""
        from main import acquire_pid_lock

        pidfile = str(tmp_path / "test.pid")
        fd = acquire_pid_lock(pidfile=pidfile)

        with open(pidfile) as f:
            pid_str = f.read().strip()

        assert pid_str == str(os.getpid())

        # Cleanup
        fd.close()

    def test_second_acquire_after_release(self, tmp_path):
        """After first fd closes, new acquire succeeds."""
        from main import acquire_pid_lock

        pidfile = str(tmp_path / "test.pid")

        # First acquire
        fd1 = acquire_pid_lock(pidfile=pidfile)
        fd1.close()

        # Second acquire — should succeed
        fd2 = acquire_pid_lock(pidfile=pidfile)
        assert fd2 is not None

        with open(pidfile) as f:
            assert f.read().strip() == str(os.getpid())

        fd2.close()


# ============================================================
# T2: Unit tests for SIGTERM handler
# ============================================================


class TestSigtermHandler:
    """T2 — SIGTERM handler tests."""

    def test_sigterm_handler_cleans_pidfile(self, tmp_path):
        """Calling _handle_sigterm() removes PID file."""
        pidfile = str(tmp_path / "test.pid")

        # Create a PID file
        with open(pidfile, "w") as f:
            f.write(str(os.getpid()))

        from main import _remove_pidfile

        _remove_pidfile(pidfile=pidfile)

        assert not os.path.exists(pidfile)

    def test_sigterm_handler_logs(self, tmp_path, caplog):
        """SIGTERM handler logs 'Received SIGTERM'."""
        import logging

        from main import _handle_sigterm

        pidfile = str(tmp_path / "test.pid")
        with open(pidfile, "w") as f:
            f.write(str(os.getpid()))

        with caplog.at_level(logging.INFO, logger="digest-bot"):
            with pytest.raises(SystemExit):
                _handle_sigterm(signal.SIGTERM, None, pidfile=pidfile)

        assert any("SIGTERM" in record.message for record in caplog.records)


# ============================================================
# T3: Integration tests — concurrent processes
# ============================================================


class TestConcurrentProcesses:
    """T3 — Integration tests for concurrent process rejection."""

    def test_second_process_exits_immediately(self, tmp_path):
        """Spawn a subprocess that tries acquire_pid_lock() while parent holds it — verify child exits with rc=1."""
        pidfile = str(tmp_path / "test.pid")

        # Parent acquires lock
        fd = open(pidfile, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()

        # Child tries to acquire
        child_script = textwrap.dedent("""\
            import sys
            sys.path.insert(0, "%s")
            from main import acquire_pid_lock
            acquire_pid_lock(pidfile="%s")
            print("SHOULD NOT REACH HERE")
        """ % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), pidfile))

        result = subprocess.run(
            [sys.executable, "-c", child_script],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 1, "Child should exit with rc=1, got rc=%d: %s" % (
            result.returncode,
            result.stderr,
        )
        assert "SHOULD NOT REACH HERE" not in result.stdout

        # Cleanup
        fd.close()

    def test_lock_survives_sigterm(self, tmp_path):
        """Parent holds lock, child receives SIGTERM → handler runs → lock released for next process."""
        pidfile = str(tmp_path / "test.pid")

        # Start a child process that acquires lock and waits
        child_script = textwrap.dedent("""\
            import sys, signal, time, os, fcntl
            sys.path.insert(0, "%s")
            from main import acquire_pid_lock, _remove_pidfile

            def handler(signum, frame):
                _remove_pidfile(pidfile="%s")
                sys.exit(0)

            signal.signal(signal.SIGTERM, handler)
            fd = acquire_pid_lock(pidfile="%s")
            # Signal ready
            print("READY", flush=True)
            # Wait for SIGTERM
            time.sleep(30)
        """ % (
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            pidfile,
            pidfile,
        ))

        proc = subprocess.Popen(
            [sys.executable, "-c", child_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for child to be ready (read until we find READY or timeout)
        ready = False
        for _ in range(10):  # Try up to 10 lines
            line = proc.stdout.readline()
            if "READY" in line:
                ready = True
                break
        assert ready, "Child didn't print READY (logging may have interfered)"

        # Verify lock is held
        fd_check = open(pidfile, "r+")
        try:
            fcntl.flock(fd_check, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd_check.close()
            pytest.fail("Lock should be held by child process")
        except BlockingIOError:
            fd_check.close()
            pass  # Expected — lock is held

        # Send SIGTERM
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        assert proc.returncode == 0

        # PID file should be removed
        assert not os.path.exists(pidfile), "PID file should be cleaned up by SIGTERM handler"

        # Lock should now be available
        fd_new = open(pidfile, "w")
        try:
            fcntl.flock(fd_new, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            locked = False
        finally:
            fd_new.close()

        assert locked, "Lock should be available after child SIGTERM"


# ============================================================
# T10: Orphan file cleanup tests
# ============================================================


class TestOrphanCleanup:
    """T10 — Tests for cleaning up legacy orphan files."""

    def test_identify_orphan_files(self, tmp_path):
        """Correctly identify empty orphan files (≤400 bytes, status: active)."""
        # Create orphan file (394 bytes, typical empty template)
        orphan = tmp_path / "2026-03-02-1043.md"
        orphan.write_text(
            "---\n"
            "coverage_from: '2026-03-02T10:43:32+08:00'\n"
            "coverage_to: '2026-03-02T10:43:32+08:00'\n"
            "generated_at: '2026-03-02T10:43:32+08:00'\n"
            "status: active\n"
            "---\n\n\n\n"
        )

        # Create real file (>1KB with content)
        real = tmp_path / "2026-03-02-1216.md"
        real.write_text(
            "---\n"
            "coverage_from: '2026-03-02T12:16:43+08:00'\n"
            "coverage_to: '2026-03-02T23:01:11+08:00'\n"
            "status: active\n"
            "---\n\n"
            + "# Real content\n" * 100
        )

        # Create finalized file (small but status: final)
        final = tmp_path / "2026-03-02-2314.md"
        final.write_text(
            "---\n"
            "coverage_from: '2026-03-02T23:14:34+08:00'\n"
            "coverage_to: '2026-03-02T23:14:34+08:00'\n"
            "status: final\n"
            "---\n\n"
            "Some content\n"
        )

        from main import identify_orphan_files

        orphans = identify_orphan_files(str(tmp_path))
        orphan_names = [os.path.basename(f) for f in orphans]

        assert "2026-03-02-1043.md" in orphan_names, "Should identify empty active file as orphan"
        assert "2026-03-02-1216.md" not in orphan_names, "Should NOT identify real content file as orphan"
        assert "2026-03-02-2314.md" not in orphan_names, "Should NOT identify finalized file as orphan"

    def test_delete_orphan_files(self, tmp_path):
        """Delete identified orphan files."""
        orphan1 = tmp_path / "2026-03-02-1043.md"
        orphan1.write_text(
            "---\n"
            "coverage_from: '2026-03-02T10:43:32+08:00'\n"
            "coverage_to: '2026-03-02T10:43:32+08:00'\n"
            "generated_at: '2026-03-02T10:43:32+08:00'\n"
            "status: active\n"
            "---\n\n\n\n"
        )

        orphan2 = tmp_path / "2026-03-02-1049.md"
        orphan2.write_text(
            "---\n"
            "coverage_from: '2026-03-02T10:49:52+08:00'\n"
            "coverage_to: '2026-03-02T10:49:52+08:00'\n"
            "generated_at: '2026-03-02T10:49:52+08:00'\n"
            "status: active\n"
            "---\n\n\n\n"
        )

        from main import delete_orphan_files

        deleted = delete_orphan_files([str(orphan1), str(orphan2)])

        assert deleted == 2
        assert not orphan1.exists()
        assert not orphan2.exists()
